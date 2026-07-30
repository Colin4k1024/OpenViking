"""Import LoCoMo conversations through skillless ``ov compile``.

Each LoCoMo conversation is isolated in one peer memory store. Utterances are
flattened in chronological order, split into batches (100 by default), written
to one OpenViking session per batch, and compiled in configurable adjacent
session groups into the same peer memory store. Independent conversations are
processed concurrently:

    session(s) + existing peer memories -> same peer memories

The script intentionally invokes the real ``ov compile`` binary so the
benchmark exercises the CLI and VikingBot compile service, not a private
shortcut.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from import_to_ov import build_session_messages

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = SCRIPT_DIR.parent / "data" / "locomo10.json"
DEFAULT_RESULT_DIR = SCRIPT_DIR / "result"
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_SESSIONS_PER_COMPILE = 2


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_cli_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = _load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"CLI config must contain a JSON object: {path}")
    return value


def _parse_session_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%I:%M %p on %d %B, %Y")
    except (TypeError, ValueError):
        return None


def flatten_conversation(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return chronological OV messages for one LoCoMo conversation."""
    flattened: list[dict[str, Any]] = []
    fallback_time = datetime(2000, 1, 1)
    sessions = build_session_messages(item, group_chat=False)
    for session_offset, session in enumerate(sessions):
        base_time = _parse_session_time(str(session["meta"].get("date_time") or "")) or (
            fallback_time + timedelta(days=session_offset)
        )
        for message_offset, message in enumerate(session["messages"]):
            flattened.append(
                {
                    "role": message["role"],
                    "parts": [{"type": "text", "text": message["text"]}],
                    "created_at": (base_time + timedelta(seconds=message_offset)).isoformat(),
                    "peer_id": str(message["peer_id"]),
                }
            )
    return flattened


def _batched(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _select_samples(
    dataset: list[dict[str, Any]],
    selectors: list[str],
) -> list[dict[str, Any]]:
    if not selectors:
        return dataset
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_id = {str(item["sample_id"]): item for item in dataset}
    for selector in selectors:
        if selector in by_id:
            item = by_id[selector]
        else:
            try:
                item = dataset[int(selector)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"Unknown LoCoMo sample selector: {selector}") from exc
        sample_id = str(item["sample_id"])
        if sample_id not in seen:
            selected.append(item)
            seen.add(sample_id)
    return selected


def _safe_run_id(value: str) -> str:
    normalized = _SAFE_ID_RE.sub("-", value.strip()).strip("-")
    if not normalized:
        raise ValueError("--run-id must contain at least one safe identifier character")
    return normalized[:40]


def _compile_command(
    *,
    ov_bin: str,
    session_uris: list[str],
    memory_uri: str,
    reason: str,
    no_default_instruction: bool,
    timeout: float,
    account: str | None,
    user: str | None,
) -> list[str]:
    command = [ov_bin, "--output", "json"]
    if account:
        command.extend(["--account", account])
    if user:
        command.extend(["--user", user])
    command.append("compile")
    for session_uri in session_uris:
        command.extend(["--from", session_uri])
    command.extend(
        [
            "--from",
            memory_uri,
            "--to",
            memory_uri,
            "--wait",
            "--timeout",
            str(timeout),
        ]
    )
    if reason.strip():
        command.extend(["--reason", reason.strip()])
    if no_default_instruction:
        command.append("--no-default-instruction")
    return command


async def _run_compile(command: list[str], env: dict[str, str]) -> str:
    completed = await asyncio.to_thread(
        subprocess.run,
        command,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"ov compile failed ({completed.returncode}): {detail}")
    return completed.stdout.strip()


async def run(args: argparse.Namespace) -> None:
    dataset_value = _load_json(args.dataset)
    if not isinstance(dataset_value, list):
        raise ValueError("LoCoMo dataset root must be a JSON array")
    samples = _select_samples(dataset_value, args.sample)
    if args.dry_run:
        total_compiles = 0
        for item in samples:
            messages = flatten_conversation(item)
            sample_batches = (len(messages) + args.batch_size - 1) // args.batch_size
            sample_compiles = (
                sample_batches + args.sessions_per_compile - 1
            ) // args.sessions_per_compile
            total_compiles += sample_compiles
            print(
                f"{item['sample_id']}: {len(messages)} utterances, {sample_batches} sessions, "
                f"{sample_compiles} compile groups"
            )
        print(f"Dry run complete: {len(samples)} conversations, {total_compiles} compile groups")
        return

    import openviking as ov

    cli_config = _load_cli_config(args.cli_config)
    url = args.url or str(cli_config.get("url") or "http://127.0.0.1:1933")
    api_key = args.api_key or cli_config.get("api_key") or cli_config.get("root_api_key")
    account = args.account or cli_config.get("account") or "default"
    user = args.user or cli_config.get("user") or "default"
    state_scope = {
        "dataset": str(args.dataset.resolve()),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "batch_size": args.batch_size,
        "sessions_per_compile": args.sessions_per_compile,
        "run_id": args.run_id,
        "reason": args.reason.strip(),
        "no_default_instruction": args.no_default_instruction,
        "url": url,
        "account": account,
        "user": user,
    }
    state = _load_json(args.state) if args.state.exists() else {"completed": {}}
    if not isinstance(state, dict):
        raise ValueError(f"Invalid import state: {args.state}")
    existing_scope = state.get("scope")
    if existing_scope is not None and existing_scope != state_scope:
        raise ValueError(
            f"Import state belongs to a different dataset/run/reason/identity: {args.state}. "
            "Choose a new --run-id or --state path."
        )
    state["scope"] = state_scope
    completed_batches = state.setdefault("completed", {})
    if not isinstance(completed_batches, dict):
        raise ValueError(f"Invalid completed batch map: {args.state}")

    subprocess_config = dict(cli_config)
    subprocess_config.update(
        {
            "url": url,
            "account": account,
            "user": user,
        }
    )
    if api_key:
        subprocess_config["api_key"] = api_key
    subprocess_config.pop("root_api_key", None)

    client = ov.AsyncHTTPClient(
        url=url,
        api_key=api_key,
        account=account,
        user=user,
        timeout=max(180.0, args.timeout),
        profile_enabled=False,
    )
    await client.initialize()
    temp_config: tempfile.NamedTemporaryFile[str] | None = None
    try:
        temp_config = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="locomo-compile-ovcli-",
            suffix=".json",
            delete=False,
        )
        json.dump(subprocess_config, temp_config)
        temp_config.close()
        compile_env = os.environ.copy()
        compile_env["OPENVIKING_CLI_CONFIG_FILE"] = temp_config.name

        total_compiles = sum(
            (
                (len(flatten_conversation(item)) + args.batch_size - 1) // args.batch_size
                + args.sessions_per_compile
                - 1
            )
            // args.sessions_per_compile
            for item in samples
        )
        compile_counter = 0
        progress_lock = asyncio.Lock()
        state_lock = asyncio.Lock()
        conversation_semaphore = asyncio.Semaphore(args.concurrency)

        async def process_conversation(item: dict[str, Any]) -> None:
            nonlocal compile_counter
            async with conversation_semaphore:
                sample_id = str(item["sample_id"])
                messages = flatten_conversation(item)
                memory_uri = f"viking://user/{user}/peers/{sample_id}/memories"
                batches = list(_batched(messages, args.batch_size))
                for group_start in range(0, len(batches), args.sessions_per_compile):
                    group = batches[group_start : group_start + args.sessions_per_compile]
                    batch_indices = list(range(group_start + 1, group_start + len(group) + 1))
                    message_count = sum(map(len, group))
                    async with progress_lock:
                        compile_counter += 1
                        current_compile = compile_counter
                    group_label = "-".join(f"{value:04d}" for value in batch_indices)
                    state_key = f"{args.run_id}:{sample_id}:{group_label}"
                    if state_key in completed_batches and not args.force:
                        print(
                            f"[{current_compile}/{total_compiles}] {sample_id} batches "
                            f"{group_label}: already completed"
                        )
                        continue
                    session_ids = []
                    session_uris = []
                    for batch_index, batch in zip(batch_indices, group, strict=True):
                        session_id = f"locomo-compile-{args.run_id}-{sample_id}-{batch_index:04d}"
                        try:
                            await client.delete_session(session_id)
                        except Exception as exc:
                            if (
                                getattr(exc, "code", None) != "NOT_FOUND"
                                and "not found" not in str(exc).lower()
                            ):
                                raise
                        created = await client.create_session(session_id=session_id)
                        await client.batch_add_messages(session_id=session_id, messages=batch)
                        session_ids.append(session_id)
                        session_uris.append(
                            str(
                                created.get("uri") or f"viking://user/{user}/sessions/{session_id}"
                            ).rstrip("/")
                        )
                    command = _compile_command(
                        ov_bin=args.ov_bin,
                        session_uris=session_uris,
                        memory_uri=memory_uri,
                        reason=args.reason,
                        no_default_instruction=args.no_default_instruction,
                        timeout=args.timeout,
                        account=account,
                        user=user,
                    )
                    print(
                        f"[{current_compile}/{total_compiles}] {sample_id} batches "
                        f"{group_label}: compiling {message_count} messages "
                        f"from {len(group)} session(s)"
                    )
                    output = await _run_compile(command, compile_env)
                    async with state_lock:
                        completed_batches[state_key] = {
                            "sample_id": sample_id,
                            "batch_indices": batch_indices,
                            "message_count": message_count,
                            "session_ids": session_ids,
                            "memory_uri": memory_uri,
                            "compile_output": output,
                        }
                        _write_json(args.state, state)
                    if args.cleanup_sessions:
                        for session_id in session_ids:
                            await client.delete_session(session_id)

        results = await asyncio.gather(
            *(process_conversation(item) for item in samples),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        print(f"Import complete. State: {args.state}")
    finally:
        await client.close()
        if temp_config is not None:
            Path(temp_config.name).unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import LoCoMo through skillless ov compile. The default batch size is "
            "100 utterances per session; adjacent sessions from one conversation "
            "can be extracted together."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--sample",
        action="append",
        default=[],
        help="Sample index or sample_id; repeat to select multiple (default: all).",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--sessions-per-compile",
        type=int,
        default=DEFAULT_SESSIONS_PER_COMPILE,
        help="Adjacent sessions combined into one Compile task (default: 2, maximum: 15).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Maximum conversations processed concurrently (default: 10).",
    )
    parser.add_argument("--run-id", default="dream")
    parser.add_argument("--reason", default="")
    parser.add_argument(
        "--no-default-instruction",
        action="store_true",
        help="Do not add Compile's default memory extraction instruction.",
    )
    parser.add_argument("--state", type=Path)
    parser.add_argument("--ov-bin", default="ov")
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument(
        "--cli-config",
        type=Path,
        default=Path(
            os.environ.get(
                "OPENVIKING_CLI_CONFIG_FILE",
                Path.home() / ".openviking" / "ovcli.conf",
            )
        ),
    )
    parser.add_argument("--url")
    parser.add_argument("--api-key")
    parser.add_argument("--account")
    parser.add_argument("--user")
    parser.add_argument("--cleanup-sessions", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.batch_size <= 0 or args.batch_size > 100:
        parser.error("--batch-size must be between 1 and 100")
    if args.sessions_per_compile <= 0 or args.sessions_per_compile > 15:
        parser.error("--sessions-per-compile must be between 1 and 15")
    if args.concurrency <= 0:
        parser.error("--concurrency must be positive")
    if not args.timeout > 0:
        parser.error("--timeout must be positive")
    args.run_id = _safe_run_id(args.run_id)
    if args.state is None:
        args.state = DEFAULT_RESULT_DIR / f"locomo_compile_{args.run_id}_state.json"
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
