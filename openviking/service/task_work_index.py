# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime index of durable queue work owned by a tracked task.

QueueFS remains the durable source of truth.  This index is rebuilt from all
unacknowledged queue messages during startup and then maintained by enqueue/ACK
hooks.  It deliberately does not persist a second counter beside QueueFS.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional
from uuid import uuid4

TASK_WORK_ID_FIELD = "_task_work_id"
TASK_ACCOUNT_ID_FIELD = "_task_account_id"
TASK_USER_ID_FIELD = "_task_user_id"


@dataclass(frozen=True)
class TaskExecutionContext:
    task_id: str
    account_id: str
    user_id: str


@dataclass(frozen=True)
class QueueTaskMetadata:
    task_id: str
    work_id: str
    account_id: str = ""
    user_id: str = ""

    @property
    def owner(self) -> Optional[tuple[str, str]]:
        if self.account_id and self.user_id:
            return self.account_id, self.user_id
        return None


_current_task_context: ContextVar[Optional[TaskExecutionContext]] = ContextVar(
    "openviking_task_execution_context",
    default=None,
)


@contextmanager
def bind_task_context(
    task_id: str,
    account_id: str,
    user_id: str,
) -> Iterator[None]:
    """Propagate task ownership to queue messages produced by this coroutine."""
    token = _current_task_context.set(
        TaskExecutionContext(
            task_id=str(task_id),
            account_id=str(account_id),
            user_id=str(user_id),
        )
    )
    try:
        yield
    finally:
        _current_task_context.reset(token)


def get_task_context() -> Optional[TaskExecutionContext]:
    return _current_task_context.get()


def _payload_dict(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return None
    payload: Any = message.get("data", message)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _owner_from_payload(payload: Mapping[str, Any]) -> tuple[str, str]:
    account_id = payload.get(TASK_ACCOUNT_ID_FIELD) or payload.get("account_id")
    user_id = payload.get(TASK_USER_ID_FIELD) or payload.get("user_id")

    user = payload.get("user")
    if isinstance(user, dict):
        account_id = account_id or user.get("account_id")
        user_id = user_id or user.get("user_id")

    context_data = payload.get("context_data")
    if isinstance(context_data, dict):
        account_id = account_id or context_data.get("account_id")
        user_id = user_id or context_data.get("owner_user_id")
        context_user = context_data.get("user")
        if isinstance(context_user, dict):
            account_id = account_id or context_user.get("account_id")
            user_id = user_id or context_user.get("user_id")

    return str(account_id or ""), str(user_id or "")


def prepare_task_payload(
    data: Dict[str, Any],
) -> tuple[Dict[str, Any], Optional[QueueTaskMetadata]]:
    """Copy a payload and attach durable task metadata when it belongs to a task."""
    payload = dict(data)
    current = _current_task_context.get()
    task_id = payload.get("task_id") or (current.task_id if current is not None else "")
    if not task_id:
        return payload, None

    task_id = str(task_id)
    account_id, user_id = _owner_from_payload(payload)
    if current is not None and current.task_id == task_id:
        account_id = account_id or current.account_id
        user_id = user_id or current.user_id

    work_id = str(payload.get(TASK_WORK_ID_FIELD) or uuid4())
    payload["task_id"] = task_id
    payload[TASK_WORK_ID_FIELD] = work_id
    if account_id:
        payload[TASK_ACCOUNT_ID_FIELD] = account_id
    if user_id:
        payload[TASK_USER_ID_FIELD] = user_id
    return payload, QueueTaskMetadata(task_id, work_id, account_id, user_id)


def extract_task_metadata(message: Any) -> Optional[QueueTaskMetadata]:
    """Read task metadata from either a QueueFS envelope or its inner payload."""
    payload = _payload_dict(message)
    if payload is None:
        return None
    task_id = payload.get("task_id")
    work_id = payload.get(TASK_WORK_ID_FIELD)
    if not work_id and isinstance(message, dict) and "data" in message and message.get("id"):
        # Messages produced before task work IDs were introduced can still be
        # rebuilt from their stable QueueFS envelope ID.
        work_id = f"queuefs:{message['id']}"
    if not task_id or not work_id:
        return None
    account_id, user_id = _owner_from_payload(payload)
    return QueueTaskMetadata(str(task_id), str(work_id), account_id, user_id)


IdleCallback = Callable[[str, Optional[tuple[str, str]]], None]
CancellationCheck = Callable[[str], bool]


class TaskWorkIndex:
    """Thread-safe, rebuildable index of persistent and currently active task work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._work: Dict[str, set[tuple[str, str]]] = {}
        self._active: Dict[
            str,
            Dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]]],
        ] = {}
        self._owners: Dict[str, tuple[str, str]] = {}
        self._on_idle: Optional[IdleCallback] = None
        self._is_cancellation_requested: Optional[CancellationCheck] = None

    def set_callbacks(
        self,
        *,
        on_idle: IdleCallback,
        is_cancellation_requested: CancellationCheck,
    ) -> None:
        self._on_idle = on_idle
        self._is_cancellation_requested = is_cancellation_requested

    def rebuild(self, snapshots: Mapping[str, Iterable[Any]]) -> None:
        """Rebuild persistent work from QueueFS while preserving concurrent enqueues."""
        work: Dict[str, set[tuple[str, str]]] = {}
        owners: Dict[str, tuple[str, str]] = {}
        for queue_name, messages in snapshots.items():
            for message in messages:
                metadata = extract_task_metadata(message)
                if metadata is None:
                    continue
                work.setdefault(metadata.task_id, set()).add((queue_name, metadata.work_id))
                if metadata.owner is not None:
                    owners[metadata.task_id] = metadata.owner
        with self._lock:
            for task_id, entries in self._work.items():
                work.setdefault(task_id, set()).update(entries)
            owners = {**self._owners, **owners}
            self._work = work
            self._owners = owners

    def register(self, queue_name: str, metadata: Optional[QueueTaskMetadata]) -> None:
        if metadata is None:
            return
        with self._lock:
            self._work.setdefault(metadata.task_id, set()).add((queue_name, metadata.work_id))
            if metadata.owner is not None:
                self._owners[metadata.task_id] = metadata.owner

    def settle(self, queue_name: str, message: Any) -> None:
        metadata = (
            message if isinstance(message, QueueTaskMetadata) else extract_task_metadata(message)
        )
        if metadata is None:
            return
        callback: Optional[IdleCallback] = None
        owner: Optional[tuple[str, str]] = None
        with self._lock:
            entries = self._work.get(metadata.task_id)
            if entries is not None:
                entries.discard((queue_name, metadata.work_id))
                if not entries:
                    self._work.pop(metadata.task_id, None)
            if not self._work.get(metadata.task_id) and not self._active.get(metadata.task_id):
                owner = self._owners.pop(metadata.task_id, metadata.owner)
                callback = self._on_idle
        if callback is not None:
            callback(metadata.task_id, owner)

    def has_work(self, task_id: str) -> bool:
        with self._lock:
            return bool(self._work.get(task_id) or self._active.get(task_id))

    def owners(self) -> Dict[str, tuple[str, str]]:
        with self._lock:
            return dict(self._owners)

    def cancellation_requested(self, task_id: str) -> bool:
        callback = self._is_cancellation_requested
        return bool(callback is not None and callback(task_id))

    def register_active(
        self,
        task_id: str,
        active_task: asyncio.Task[Any],
        owner: Optional[tuple[str, str]] = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._active.setdefault(task_id, {})[id(active_task)] = (loop, active_task)
            if owner is not None and all(owner):
                self._owners[task_id] = owner
        if self.cancellation_requested(task_id):
            loop.call_soon(active_task.cancel)

    def unregister_active(self, task_id: str, active_task: asyncio.Task[Any]) -> None:
        callback: Optional[IdleCallback] = None
        owner: Optional[tuple[str, str]] = None
        with self._lock:
            entries = self._active.get(task_id)
            if entries is not None:
                entries.pop(id(active_task), None)
                if not entries:
                    self._active.pop(task_id, None)
            if not self._work.get(task_id) and not self._active.get(task_id):
                owner = self._owners.pop(task_id, None)
                callback = self._on_idle
        if callback is not None:
            callback(task_id, owner)

    def cancel_active(self, task_id: str) -> None:
        with self._lock:
            active = list(self._active.get(task_id, {}).values())
        for loop, task in active:
            loop.call_soon_threadsafe(task.cancel)
