# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Task tracking endpoints for OpenViking HTTP Server.

Provides observability for background operations (e.g. session commit
with ``wait=false``).  Callers receive a ``task_id`` and can poll these
endpoints to check completion, results, or errors.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID, SYSTEM_TASK_USER_ID
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    OpenVikingError,
    PermissionDeniedError,
)

router = APIRouter(prefix="/api/v1", tags=["tasks"])


def _resolve_task_owner(
    ctx: RequestContext,
    account_id: Optional[str],
    user_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    if ctx.role == Role.ROOT:
        if bool(account_id) != bool(user_id):
            raise InvalidArgumentError("account_id and user_id must be provided together")
        return account_id, user_id
    if ctx.role == Role.ADMIN:
        if account_id is not None and account_id != ctx.account_id:
            raise PermissionDeniedError("ADMIN may only manage tasks in their own account")
        return ctx.account_id, user_id or ctx.user.user_id
    if account_id is not None or user_id is not None:
        if account_id != ctx.account_id or user_id != ctx.user.user_id:
            raise PermissionDeniedError("Users may only manage their own tasks")
    return ctx.account_id, ctx.user.user_id


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Get the status of a single background task."""
    tracker = get_task_tracker()
    if _ctx.role == Role.ROOT:
        task = await tracker.get(task_id)
        if task is None:
            task = await tracker.get(
                task_id,
                account_id=SYSTEM_TASK_ACCOUNT_ID,
                user_id=SYSTEM_TASK_USER_ID,
            )
    else:
        task = await tracker.get(
            task_id,
            account_id=_ctx.account_id,
            user_id=_ctx.user.user_id,
        )
    if not task:
        raise OpenVikingError(
            "Task not found or expired",
            code="NOT_FOUND",
            details={"resource": task_id, "type": "task"},
        )
    return Response(status="ok", result=task.to_dict())


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    account_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    _ctx: RequestContext = Depends(get_request_context),
):
    """Request cooperative cancellation of a background task."""
    owner_account_id, owner_user_id = _resolve_task_owner(_ctx, account_id, user_id)
    tracker = get_task_tracker()
    try:
        task = await tracker.cancel(
            task_id,
            account_id=owner_account_id,
            user_id=owner_user_id,
        )
        if task is None and _ctx.role == Role.ROOT and owner_account_id is None:
            task = await tracker.cancel(
                task_id,
                account_id=SYSTEM_TASK_ACCOUNT_ID,
                user_id=SYSTEM_TASK_USER_ID,
            )
    except ValueError as exc:
        raise FailedPreconditionError(str(exc)) from exc
    if task is None:
        raise OpenVikingError(
            "Task not found or expired",
            code="NOT_FOUND",
            details={"resource": task_id, "type": "task"},
        )
    return Response(status="ok", result=task.to_dict())


@router.get("/tasks")
async def list_tasks(
    task_type: Optional[str] = Query(None, description="Filter by task type (e.g. session_commit)"),
    status: Optional[str] = Query(
        None, description="Filter by status (pending/running/completed/failed/cancelled)"
    ),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID (e.g. session_id)"),
    limit: int = Query(50, le=200, description="Max results"),
    _ctx: RequestContext = Depends(get_request_context),
):
    """List background tasks with optional filters."""
    tracker = get_task_tracker()
    if _ctx.role == Role.ROOT:
        system_tasks = await tracker.list_tasks(
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
            account_id=SYSTEM_TASK_ACCOUNT_ID,
            user_id=SYSTEM_TASK_USER_ID,
        )
        cached_tasks = await tracker.list_tasks(
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
        )
        tasks_by_id = {task.task_id: task for task in cached_tasks}
        tasks_by_id.update({task.task_id: task for task in system_tasks})
        tasks = sorted(tasks_by_id.values(), key=lambda task: task.created_at, reverse=True)[:limit]
    else:
        tasks = await tracker.list_tasks(
            task_type=task_type,
            status=status,
            resource_id=resource_id,
            limit=limit,
            account_id=_ctx.account_id,
            user_id=_ctx.user.user_id,
        )
    return Response(status="ok", result=[t.to_dict() for t in tasks])
