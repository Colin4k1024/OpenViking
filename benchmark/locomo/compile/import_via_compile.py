"""Import LoCoMo conversations through ordinary Skill-driven ``ov compile``."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
VIKINGBOT_DIR = SCRIPT_DIR.parent / "vikingbot"
if str(VIKINGBOT_DIR) not in sys.path:
    sys.path.insert(0, str(VIKINGBOT_DIR))

from import_to_ov import build_session_messages  # noqa: E402

DEFAULT_DATASET = SCRIPT_DIR.parent / "data" / "locomo10.json"
DEFAULT_RESULT_DIR = SCRIPT_DIR / "result"
DEFAULT_SKILL_NAME = "session-memory-consolidation"
DEFAULT_SKILL_SOURCE = SCRIPT_DIR.parents[2] / "examples" / "skills" / DEFAULT_SKILL_NAME
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TOKEN_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_tokens",
    "reasoning_tokens",
    "llm_total_tokens",
    "embedding_tokens",
    "total_tokens",
)
_USER_DIRECTORY_NAMES = ("memories", "resources", "privacy", "peers", "skills", "sessions")


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _session_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%I:%M %p on %d %B, %Y")
    except (TypeError, ValueError):
        return None


def conversation_sessions(item: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Convert each original LoCoMo session without changing its boundary."""
    result: list[list[dict[str, Any]]] = []
    fallback = datetime(2000, 1, 1)
    for session_index, session in enumerate(build_session_messages(item, group_chat=False)):
        started = _session_time(session["meta"].get("date_time")) or (
            fallback + timedelta(days=session_index)
        )
        messages: list[dict[str, Any]] = []
        for message_index, message in enumerate(session["messages"]):
            messages.append(
                {
                    "role": message["role"],
                    "parts": [{"type": "text", "text": message["text"]}],
                    "created_at": (started + timedelta(seconds=message_index)).isoformat(),
                    "peer_id": str(message["peer_id"]),
                }
            )
        result.append(messages)
    return result


def _select_samples(dataset: list[dict[str, Any]], selectors: list[str]) -> list[dict[str, Any]]:
    if not selectors:
        return dataset
    by_id = {str(item["sample_id"]): item for item in dataset}
    selected: dict[str, dict[str, Any]] = {}
    for selector in selectors:
        if selector in by_id:
            item = by_id[selector]
        else:
            try:
                item = dataset[int(selector)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"Unknown LoCoMo sample selector: {selector}") from exc
        selected.setdefault(str(item["sample_id"]), item)
    return list(selected.values())


def _safe_run_id(value: str) -> str:
    result = _SAFE_ID_RE.sub("-", value.strip()).strip("-")[:40]
    if not result:
        raise ValueError("--run-id must contain a safe identifier character")
    return result


def _compile_command(
    args: argparse.Namespace,
    *,
    session_uris: list[str],
    memory_uri: str,
    skill_uri: str,
) -> list[str]:
    command = [args.ov_bin, "--output", "json", "compile"]
    for uri in session_uris:
        command.extend(["--from", uri])
    command.extend(
        [
            "--to",
            memory_uri,
            "--skill",
            skill_uri,
            "--wait",
            "--timeout",
            str(args.timeout),
        ]
    )
    if args.reason.strip():
        command.extend(["--reason", args.reason.strip()])
    return command


async def _run_compile(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    completed = await asyncio.to_thread(
        subprocess.run,
        command,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"ov compile failed ({completed.returncode}): {detail}")
    payload = json.loads(completed.stdout)
    status = payload.get("result", payload)
    return status.get("result", status)


async def _delete_session_if_exists(client: Any, session_id: str) -> None:
    try:
        await client.delete_session(session_id)
    except Exception as exc:
        if getattr(exc, "code", None) != "NOT_FOUND":
            raise


async def _missing_user_directories(client: Any, user: str) -> list[str]:
    try:
        entries = await client.ls(f"viking://user/{user}")
    except Exception as exc:
        if getattr(exc, "code", None) == "NOT_FOUND":
            return list(_USER_DIRECTORY_NAMES)
        raise
    existing = {
        str(entry.get("name") or entry.get("uri") or "").rstrip("/").rsplit("/", 1)[-1]
        for entry in entries
        if isinstance(entry, dict) and entry.get("isDir", entry.get("is_dir", False))
    }
    return [name for name in _USER_DIRECTORY_NAMES if name not in existing]


async def _prepare_session_namespace(client: Any, user: str, run_id: str) -> None:
    """Initialize shared user directories once before concurrent session creation."""
    if not await _missing_user_directories(client, user):
        return

    session_id = f"locomo-compile-{run_id}-bootstrap"
    try:
        await client.create_session(session_id=session_id)
    except Exception as exc:
        if getattr(exc, "code", None) != "ALREADY_EXISTS":
            raise

    missing = await _missing_user_directories(client, user)
    if missing:
        raise RuntimeError("User directory initialization incomplete: " + ", ".join(missing))


def _skill_location(skill_uri: str) -> tuple[str, str]:
    normalized = skill_uri.removesuffix("/SKILL.md").rstrip("/")
    parent, name = normalized.rsplit("/", 1)
    if "/skills" not in parent:
        raise ValueError(f"--skill must identify an OpenViking Skill: {skill_uri}")
    return parent, name


def _summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(records)
    count = len(values)
    divisor = count or 1
    total_usage = {
        field: sum(
            int(item.get("compile_result", {}).get("token_usage", {}).get(field, 0))
            for item in values
        )
        for field in _TOKEN_USAGE_FIELDS
    }
    return {
        "compile_groups": count,
        "messages": sum(int(item.get("message_count", 0)) for item in values),
        "pages": sum(int(item.get("compile_result", {}).get("page_count", 0)) for item in values),
        "average_compile_duration_seconds": round(
            sum(
                float(item.get("compile_result", {}).get("duration_seconds", 0))
                for item in values
            )
            / divisor,
            3,
        ),
        "average_pipeline_duration_seconds": round(
            sum(float(item.get("pipeline_duration_seconds", 0)) for item in values)
            / divisor,
            3,
        ),
        "average_token_usage": {
            key: round(value / divisor, 2) for key, value in total_usage.items()
        },
        "total_token_usage": total_usage,
    }


async def run(args: argparse.Namespace) -> None:
    dataset = _load_json(args.dataset)
    if not isinstance(dataset, list):
        raise ValueError("LoCoMo dataset root must be an array")
    samples = _select_samples(dataset, args.sample)
    if args.dry_run:
        total = 0
        for item in samples:
            sessions = conversation_sessions(item)
            message_count = sum(map(len, sessions))
            groups = (
                len(sessions) + args.sessions_per_compile - 1
            ) // args.sessions_per_compile
            total += groups
            print(
                f"{item['sample_id']}: {message_count} messages, "
                f"{len(sessions)} sessions, {groups} compile groups"
            )
        print(f"Dry run: {len(samples)} conversations, {total} compile groups")
        return

    import openviking as ov

    config = _load_json(args.cli_config)
    user = str(config.get("user") or "default")
    account = str(config.get("account") or "default")
    url = str(config.get("url") or "http://127.0.0.1:1933")
    api_key = config.get("api_key") or config.get("root_api_key")
    skill_uri = (args.skill or f"viking://user/{user}/skills/{DEFAULT_SKILL_NAME}").rstrip("/")
    skill_parent, skill_name = _skill_location(skill_uri)

    client = ov.AsyncHTTPClient(
        url=url,
        api_key=api_key,
        account=account,
        user=user,
        timeout=max(args.timeout, 180),
        profile_enabled=False,
    )
    await client.initialize()
    try:
        try:
            skill = await client.get_skill(
                skill_name,
                include_content=True,
                include_files=False,
                target_uri=skill_parent,
            )
        except Exception as exc:
            install_hint = (
                f" Install it with: ov add-skill {DEFAULT_SKILL_SOURCE} --wait"
                if args.skill is None
                else ""
            )
            raise RuntimeError(f"Skill not found: {skill_uri}.{install_hint}") from exc
        skill_hash = hashlib.sha256(str(skill.get("content") or "").encode()).hexdigest()
        scope = {
            "dataset": str(args.dataset.resolve()),
            "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
            "session_partition": "locomo_original",
            "sessions_per_compile": args.sessions_per_compile,
            "run_id": args.run_id,
            "reason": args.reason.strip(),
            "skill_uri": skill_uri,
            "skill_sha256": skill_hash,
            "account": account,
            "user": user,
            "metrics_schema": "normalized-token-usage-v1",
        }
        state = _load_json(args.state) if args.state.exists() else {"completed": {}}
        if state.get("scope") not in (None, scope):
            raise ValueError(f"State belongs to a different experiment: {args.state}")
        state["scope"] = scope
        completed = state.setdefault("completed", {})
        if not isinstance(completed, dict):
            raise ValueError(f"Invalid state file: {args.state}")

        compile_env = os.environ.copy()
        compile_env["OPENVIKING_CLI_CONFIG_FILE"] = str(args.cli_config)
        await _prepare_session_namespace(client, user, args.run_id)
        semaphore = asyncio.Semaphore(args.concurrency)
        state_lock = asyncio.Lock()
        progress_lock = asyncio.Lock()
        counter = 0
        total_groups = sum(
            (len(conversation_sessions(item)) + args.sessions_per_compile - 1)
            // args.sessions_per_compile
            for item in samples
        )

        async def process(item: dict[str, Any]) -> None:
            nonlocal counter
            async with semaphore:
                sample_id = str(item["sample_id"])
                memory_uri = f"viking://user/{user}/peers/{sample_id}/memories"
                sessions = conversation_sessions(item)
                for start in range(0, len(sessions), args.sessions_per_compile):
                    group = sessions[start : start + args.sessions_per_compile]
                    indices = list(range(start + 1, start + len(group) + 1))
                    label = "-".join(f"{index:04d}" for index in indices)
                    state_key = f"{args.run_id}:{sample_id}:{label}"
                    async with progress_lock:
                        counter += 1
                        current = counter
                    if state_key in completed and not args.force:
                        print(f"[{current}/{total_groups}] {sample_id} {label}: completed")
                        continue

                    pipeline_started = time.perf_counter()
                    session_ids: list[str] = []
                    session_uris: list[str] = []
                    for index, batch in zip(indices, group, strict=True):
                        session_id = f"locomo-compile-{args.run_id}-{sample_id}-{index:04d}"
                        await _delete_session_if_exists(client, session_id)
                        created = await client.create_session(session_id=session_id)
                        await client.batch_add_messages(session_id=session_id, messages=batch)
                        session_ids.append(session_id)
                        session_uris.append(str(created["uri"]).rstrip("/"))

                    message_count = sum(map(len, group))
                    print(
                        f"[{current}/{total_groups}] {sample_id} {label}: "
                        f"{message_count} messages from {len(group)} session(s)"
                    )
                    result = await _run_compile(
                        _compile_command(
                            args,
                            session_uris=session_uris,
                            memory_uri=memory_uri,
                            skill_uri=skill_uri,
                        ),
                        compile_env,
                    )
                    record = {
                        "sample_id": sample_id,
                        "session_indices": indices,
                        "message_count": message_count,
                        "session_ids": session_ids,
                        "memory_uri": memory_uri,
                        "compile_result": result,
                        "pipeline_duration_seconds": time.perf_counter() - pipeline_started,
                    }
                    async with state_lock:
                        completed[state_key] = record
                        state["summary"] = _summary(completed.values())
                        _write_json(args.state, state)
                    if args.cleanup_sessions:
                        for session_id in session_ids:
                            await client.delete_session(session_id)

        results = await asyncio.gather(*(process(item) for item in samples), return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError(
                f"{len(failures)} conversation(s) failed; first error: {failures[0]}"
            ) from failures[0]
        state["summary"] = _summary(completed.values())
        _write_json(args.state, state)
        print(json.dumps(state["summary"], ensure_ascii=False, indent=2))
        print(f"State: {args.state}")
    finally:
        await client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--sample", action="append", default=[])
    parser.add_argument(
        "--sessions-per-compile",
        type=int,
        default=32,
        help="Adjacent original LoCoMo sessions passed to one Compile task (default: 32).",
    )
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--run-id", default="skill-baseline")
    parser.add_argument("--skill")
    parser.add_argument("--reason", default="")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--ov-bin", default="ov")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument(
        "--cli-config",
        type=Path,
        default=Path(
            os.environ.get("OPENVIKING_CLI_CONFIG_FILE", Path.home() / ".openviking" / "ovcli.conf")
        ),
    )
    parser.add_argument("--cleanup-sessions", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.sessions_per_compile <= 32:
        parser.error("--sessions-per-compile must be between 1 and 32")
    if args.concurrency <= 0:
        parser.error("--concurrency must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    args.run_id = _safe_run_id(args.run_id)
    if args.state is None:
        args.state = DEFAULT_RESULT_DIR / f"locomo_compile_{args.run_id}_state.json"
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
