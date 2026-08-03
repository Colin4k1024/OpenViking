"""Evaluate LoCoMo answers with one directory-agnostic memory retrieval pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
LOCOMO_DIR = SCRIPT_DIR.parent
REPO_ROOT = LOCOMO_DIR.parents[1]
VIKINGBOT_DIR = LOCOMO_DIR / "vikingbot"
for path in (REPO_ROOT, VIKINGBOT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from benchmark.locomo.openviking.locomo_prompts import (  # noqa: E402
    get_answer_generation_prompt,
)
from benchmark.locomo.openviking.run_eval import (  # noqa: E402
    _response_text,
    _token_usage_from_vlm,
    build_single_search_vlm,
    count_retrieved_memory_content_tokens,
)
from benchmark.locomo.vikingbot.run_eval import load_locomo_qa  # noqa: E402
from openviking_cli.client.sync_http import SyncHTTPClient  # noqa: E402

DEFAULT_DATASET = LOCOMO_DIR / "data" / "locomo10.json"
DEFAULT_ERRORS = LOCOMO_DIR / "data" / "errors.json"
DEFAULT_RESULT_DIR = SCRIPT_DIR / "result"
DEFAULT_OUTPUT = DEFAULT_RESULT_DIR / "locomo_compile_qa_result.csv"
DEFAULT_MEMORY_ROOT_TEMPLATE = "viking://user/{user}/peers/{sample_id}/memories"
DEFAULT_SEARCH_LIMIT = 50
DEFAULT_MAX_CHARS = 20_000
EXCLUDED_BASENAMES = {
    ".abstract.md",
    ".overview.md",
    ".relations.json",
    "index.md",
    "log.md",
}
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_MEMORY_FIELDS_RE = re.compile(r"\n*<!--\s*MEMORY_FIELDS\b.*?-->\s*", re.DOTALL)
_CITATIONS_RE = re.compile(r"(?ms)\n*^# Citations[ \t]*\n.*\Z")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\([^)]+\)")


def clean_memory_content(content: str) -> str:
    """Remove storage metadata that should not consume the answerer's memory budget."""
    cleaned = _FRONTMATTER_RE.sub("", content or "", count=1)
    cleaned = _MEMORY_FIELDS_RE.sub("\n", cleaned)
    cleaned = _CITATIONS_RE.sub("", cleaned)
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _search_items(result: Any) -> list[Any]:
    if isinstance(result, Mapping):
        return [
            item
            for key in ("memories", "resources", "skills")
            for item in result.get(key, []) or []
        ]
    if isinstance(result, list):
        return result
    return [
        item
        for key in ("memories", "resources", "skills")
        for item in getattr(result, key, []) or []
    ]


def select_search_contexts(result: Any, limit: int) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for raw_rank, item in enumerate(_search_items(result), start=1):
        uri = str(_value(item, "uri", "") or "").rstrip("/")
        basename = uri.rsplit("/", 1)[-1]
        if (
            not uri
            or not uri.casefold().endswith(".md")
            or basename.casefold() in EXCLUDED_BASENAMES
        ):
            continue
        contexts.append(
            {
                "raw_rank": raw_rank,
                "uri": uri,
                "score": float(_value(item, "score", 0.0) or 0.0),
                "abstract": str(_value(item, "abstract", "") or ""),
                "created_at": str(_value(item, "created_at", "") or ""),
            }
        )
        if len(contexts) >= limit:
            break
    return contexts


def fit_contexts_to_char_budget(
    contexts: list[dict[str, Any]], max_chars: int
) -> tuple[list[dict[str, Any]], list[str], list[str], int]:
    if max_chars <= 0:
        total = sum(len(str(context.get("content", "") or "")) for context in contexts)
        return contexts, [], [], total

    selected: list[dict[str, Any]] = []
    skipped: list[str] = []
    truncated: list[str] = []
    total = 0
    for index, context in enumerate(contexts):
        content = str(context.get("content", "") or "")
        remaining = max_chars - total
        if remaining <= 0:
            skipped.extend(str(item.get("uri", "")) for item in contexts[index:])
            break
        if len(content) <= remaining:
            selected.append(context)
            total += len(content)
            continue

        partial = dict(context)
        if remaining <= 3:
            partial["content"] = "." * remaining
        else:
            partial["content"] = content[: remaining - 3].rstrip() + "..."
        partial["truncated"] = True
        if partial["content"]:
            selected.append(partial)
            total += len(partial["content"])
            truncated.append(str(context.get("uri", "")))
        skipped.extend(str(item.get("uri", "")) for item in contexts[index + 1 :])
        break
    return selected, skipped, truncated, total


def _load_connection(
    cli_config: Path,
    *,
    url: str | None,
    account: str | None,
    user: str | None,
    timeout: float,
) -> dict[str, Any]:
    config = json.loads(cli_config.expanduser().read_text(encoding="utf-8"))
    return {
        "url": url or config.get("url") or "http://127.0.0.1:1933",
        "api_key": config.get("api_key") or config.get("root_api_key"),
        "account": account or config.get("account") or "default",
        "user": user or config.get("user") or "default",
        "timeout": timeout,
        "profile_enabled": False,
    }


def _read_contexts(client: SyncHTTPClient, contexts: list[dict[str, Any]]) -> None:
    for context in contexts:
        try:
            raw = client.read(context["uri"], offset=0, limit=-1)
            content = clean_memory_content(str(raw or ""))
        except Exception as exc:
            content = ""
            context["read_error"] = f"{type(exc).__name__}: {exc}"
        context["content"] = content or clean_memory_content(context.get("abstract", ""))


def evaluate_question(
    *,
    question: str,
    question_time: str | None,
    sample_id: str,
    connection: dict[str, Any],
    memory_root_template: str,
    search_limit: int,
    max_chars: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    target_uri = memory_root_template.format(user=connection["user"], sample_id=sample_id)
    client = SyncHTTPClient(**connection)
    try:
        client.initialize()
        result = client.find(question, target_uri=target_uri, limit=search_limit)
        contexts = select_search_contexts(result, search_limit)
        _read_contexts(client, contexts)
        retrieved_uris = [context["uri"] for context in contexts]
    finally:
        client.close()

    candidate_chars = sum(len(str(context.get("content", "") or "")) for context in contexts)
    contexts, skipped_uris, truncated_uris, memory_chars = fit_contexts_to_char_budget(
        contexts,
        max_chars,
    )

    answer_memories = [
        {
            "memory": context.get("content", ""),
            "score": context.get("score", 0.0),
            "created_at": context.get("created_at", ""),
        }
        for context in contexts
    ]
    prompt = get_answer_generation_prompt(
        question=question,
        search_results=answer_memories,
        reference_date=question_time or "2023",
    )
    vlm = build_single_search_vlm()
    memory_tokens, measured_chars, tokenizer = count_retrieved_memory_content_tokens(
        contexts,
        model_name=getattr(vlm, "model", None),
    )
    response = vlm.get_completion(prompt)
    token_usage = _token_usage_from_vlm(vlm, response)
    token_usage.update(
        {
            "memory_prompt_tokens": memory_tokens,
            "memory_chars": measured_chars,
            "memory_tokenizer": tokenizer,
        }
    )
    return {
        "response": _response_text(response),
        "token_usage": token_usage,
        "time_cost": time.perf_counter() - started,
        "model_input_prompt": prompt,
        "memory_target_uri": target_uri,
        "retrieval": {
            "retrieved_uris": retrieved_uris,
            "context_uris": [context["uri"] for context in contexts],
            "search_method": "find",
            "ranking": "embedding",
            "search_limit": search_limit,
            "candidate_chars": candidate_chars,
            "max_chars": max_chars,
            "memory_chars": memory_chars,
            "truncated_uris": truncated_uris,
            "skipped_uris": skipped_uris,
        },
    }


def _invalid_questions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data and isinstance(data[0], dict):
        return {str(item["question"]) for item in data}
    return {str(item) for item in data}


def _row_key(row: Mapping[str, Any]) -> str:
    return f"{row.get('sample_id', '')}:{row.get('question_index', '')}"


def _write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate LoCoMo by searching each peer's entire memories root, independent of "
            "memory type or directory layout."
        )
    )
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERRORS)
    parser.add_argument("--sample")
    parser.add_argument("--question-index", type=int)
    parser.add_argument("--count", type=int)
    parser.add_argument("--threads", type=int, default=40)
    parser.add_argument(
        "--cli-config",
        type=Path,
        default=Path(
            os.environ.get("OPENVIKING_CLI_CONFIG_FILE", Path.home() / ".openviking" / "ovcli.conf")
        ),
    )
    parser.add_argument("--openviking-url")
    parser.add_argument("--account")
    parser.add_argument("--user")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument(
        "--memory-root-template",
        default=DEFAULT_MEMORY_ROOT_TEMPLATE,
        help="Viking URI template with {user} and {sample_id} placeholders.",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=DEFAULT_SEARCH_LIMIT,
        help="Top-N memories returned by OpenViking find() (default: 50).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=(
            "Maximum cleaned memory-content characters per question; the final memory "
            "is truncated with '...' when needed, and <=0 disables the limit."
        ),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--debug-print-model-input", action="store_true")
    args = parser.parse_args()
    if args.question_index is not None and args.count is None:
        args.count = 1
    if args.threads <= 0 or args.search_limit <= 0:
        parser.error("--threads and --search-limit must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        args.memory_root_template.format(user="user", sample_id="sample")
    except (KeyError, ValueError) as exc:
        parser.error(f"invalid --memory-root-template: {exc}")
    return args


def main() -> None:
    args = parse_args()
    invalid = _invalid_questions(args.errors.expanduser())
    qa_items = load_locomo_qa(
        str(args.input),
        sample_index=args.sample,
        count=args.count,
        question_index=args.question_index,
        invalid_questions=invalid,
    )
    qa_items = [item for item in qa_items if str(item.get("category", "")) != "5"]
    connection = _load_connection(
        args.cli_config,
        url=args.openviking_url,
        account=args.account,
        user=args.user,
        timeout=args.timeout,
    )

    fieldnames = [
        "sample_id",
        "question_id",
        "question_index",
        "result",
        "reasoning",
        "is_invalid",
        "question",
        "answer",
        "category",
        "question_time",
        "evidence",
        "evidence_text",
        "response",
        "memory_target_uri",
        "max_chars",
        "memory_chars",
        "token_usage",
        "time_cost",
        "retrieval",
        "model_input_prompt",
    ]
    rows: list[dict[str, Any]] = []
    if args.resume and args.output.exists():
        with args.output.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    completed = {_row_key(row) for row in rows}
    remaining = [
        item
        for item in qa_items
        if f"{item.get('original_sample_id', '')}:{item.get('question_index', '')}"
        not in completed
    ]
    if not args.resume and args.output.exists():
        args.output.unlink()

    print(
        f"Evaluating {len(remaining)} questions with {args.threads} threads, "
        f"max_chars={args.max_chars}"
    )
    lock = threading.Lock()
    done = 0

    def process(item: dict[str, Any]) -> dict[str, Any]:
        sample_id = str(item.get("original_sample_id") or item["sample_id"])
        question_index = item.get("question_index", "")
        question_id = f"{sample_id}_qa{question_index}"
        try:
            evaluation = evaluate_question(
                question=item["question"],
                question_time=item.get("question_time"),
                sample_id=sample_id,
                connection=connection,
                memory_root_template=args.memory_root_template,
                search_limit=args.search_limit,
                max_chars=args.max_chars,
            )
        except Exception as exc:
            evaluation = {
                "response": f"[EVAL ERROR] {type(exc).__name__}: {exc}",
                "token_usage": {},
                "time_cost": 0.0,
                "model_input_prompt": "",
                "memory_target_uri": args.memory_root_template.format(
                    user=connection["user"],
                    sample_id=sample_id,
                ),
                "retrieval": {"max_chars": args.max_chars, "memory_chars": 0},
            }
        retrieval = evaluation["retrieval"]
        return {
            "sample_id": sample_id,
            "question_id": question_id,
            "question_index": question_index,
            "result": "",
            "reasoning": "",
            "is_invalid": item.get("is_invalid", False),
            "question": item["question"],
            "answer": item["answer"],
            "category": item.get("category", ""),
            "question_time": item.get("question_time") or "",
            "evidence": json.dumps(item.get("evidence", []), ensure_ascii=False),
            "evidence_text": json.dumps(item.get("evidence_text", []), ensure_ascii=False),
            "response": evaluation["response"],
            "memory_target_uri": evaluation["memory_target_uri"],
            "max_chars": args.max_chars,
            "memory_chars": retrieval.get("memory_chars", 0),
            "token_usage": json.dumps(evaluation["token_usage"], ensure_ascii=False),
            "time_cost": round(float(evaluation["time_cost"]), 3),
            "retrieval": json.dumps(retrieval, ensure_ascii=False),
            "model_input_prompt": (
                evaluation["model_input_prompt"] if args.debug_print_model_input else ""
            ),
        }

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = [executor.submit(process, item) for item in remaining]
        for future in as_completed(futures):
            row = future.result()
            with lock:
                rows.append(row)
                _write_rows(args.output, fieldnames, rows)
                done += 1
                print(f"[{done}/{len(remaining)}] {row['question_id']}")

    print(f"Evaluation completed: {args.output}")


if __name__ == "__main__":
    main()
