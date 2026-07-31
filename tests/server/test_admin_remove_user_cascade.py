# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for issue #3660: remove_user must cascade-delete user data.

The endpoint must fail closed: every *configured* cleanup stage has to succeed
before the credential/registry entry is removed. A present subsystem that
raises must abort (user stays registered, response fails); an absent optional
subsystem is skipped. Partial cleanup is idempotently retryable because each
stage treats already-removed data as a no-op.
"""

import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.api_keys import APIKeyManager
from openviking.server.config import ServerConfig
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import admin as admin_router
from openviking_cli.exceptions import OpenVikingError

ROOT_KEY = "issue-3660-root-key-abcdef1234567890ab"


def _acct(prefix="acct") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class _FakeVectorStore:
    def __init__(self):
        self.delete_user_calls: list[tuple[str, str]] = []
        self.raise_on_delete: Exception | None = None

    async def delete_user_data(self, account_id: str, user_id: str) -> int:
        self.delete_user_calls.append((account_id, user_id))
        if self.raise_on_delete is not None:
            raise self.raise_on_delete
        return 3


class _FakeOAuthStore:
    def __init__(self):
        self.revoke_calls: list[dict] = []
        self.raise_on_revoke: Exception | None = None

    async def revoke_user_tokens(self, *, account_id: str, user_id: str) -> dict:
        self.revoke_calls.append({"account_id": account_id, "user_id": user_id})
        if self.raise_on_revoke is not None:
            raise self.raise_on_revoke
        return {
            "access_tokens_revoked": 1,
            "refresh_tokens_revoked": 1,
            "codes_revoked": 0,
        }


class _FakeUsageStore:
    def __init__(self):
        self.delete_calls: list[dict] = []
        self.raise_on_delete: Exception | None = None

    async def delete_user_data(self, *, account_id: str, user_id: str) -> dict:
        self.delete_calls.append({"account_id": account_id, "user_id": user_id})
        if self.raise_on_delete is not None:
            raise self.raise_on_delete
        return {
            "usage_token_hourly": 2,
            "usage_retrieval_hourly": 1,
            "usage_context_write_bucket": 0,
            "request_audit": 5,
        }


class _FakeUsageWorker:
    def __init__(self):
        self.flushed = 0

    async def flush(self):
        self.flushed += 1


class _FakeUsageRuntime:
    def __init__(self, store):
        self.store = store
        self.worker = _FakeUsageWorker()

    async def delete_user_data(self, *, account_id: str, user_id: str) -> dict:
        await self.worker.flush()
        return await self.store.delete_user_data(
            account_id=account_id,
            user_id=user_id,
        )


class _RecordingAGFS:
    """Minimal in-memory sync AGFS surface used by AsyncAGFSClient."""

    def __init__(self):
        self._files: dict[str, bytes] = {}

    def read(self, path, **_kwargs):
        if path not in self._files:
            raise AGFSNotFoundError(path)
        return self._files[path]

    def write(self, path, content, **_kwargs):
        self._files[path] = content

    def ensure_parent_dirs(self, path, **_kwargs):
        return None

    def pathlock_acquire_exact(
        self, fs_ctx, path, timeout_secs=0.0, owner_lease_ref=None
    ):
        return {"lease_ref": f"test:{path}"}

    def pathlock_release(self, fs_ctx, lease):
        return None


class _RecordingVikingFS:
    def __init__(self):
        self.rm_calls: list[dict] = []
        self.raise_on_rm: Exception | None = None
        self._vector_store = _FakeVectorStore()
        self.agfs = _RecordingAGFS()

    @property
    def vector_store(self):
        return self._vector_store

    @vector_store.setter
    def vector_store(self, value):
        self._vector_store = value

    async def rm(self, uri, *, recursive=False, ctx=None):
        self.rm_calls.append({"uri": uri, "recursive": recursive, "ctx": ctx})
        if self.raise_on_rm is not None:
            raise self.raise_on_rm


class _FakeService:
    def __init__(self, viking_fs):
        self.viking_fs = viking_fs
        self.sessions = type(
            "S", (), {"get_agent_evolution_enabled": lambda self: False}
        )()

    async def initialize_account_directories(self, ctx):
        return None

    async def initialize_user_directories(self, ctx):
        return None


@pytest_asyncio.fixture()
async def cascade_client(monkeypatch):
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.auth.registry import get_registry

    viking_fs = _RecordingVikingFS()
    oauth_store = _FakeOAuthStore()
    usage_runtime = _FakeUsageRuntime(_FakeUsageStore())

    app = FastAPI()

    @app.exception_handler(OpenVikingError)
    async def _ov_error_handler(_request, exc: OpenVikingError):
        from fastapi.responses import JSONResponse

        from openviking.server.models import ErrorInfo, Response

        return JSONResponse(
            status_code=500,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=exc.code, message=exc.message, details=exc.details
                ),
            ).model_dump(),
        )

    app.state.config = ServerConfig(root_api_key=ROOT_KEY)
    app.state.oauth_store = oauth_store
    app.state.usage_audit_runtime = usage_runtime

    service = _FakeService(viking_fs)
    app.state.fake_service = service
    set_service(service)

    monkeypatch.setattr(admin_router, "get_viking_fs", lambda: viking_fs)

    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=viking_fs)
    await manager.load()
    app.state.api_key_manager = manager

    registry = get_registry()
    if registry.get("api_key") is None:
        registry.register(ApiKeyAuthPlugin)
    app.state.auth_plugin = registry.get("api_key")()

    app.include_router(admin_router.router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        c.app = app
        c.viking_fs = viking_fs
        c.manager = manager
        c._oauth_store = oauth_store
        c._usage_store = usage_runtime.store
        yield c

    set_service(None)


def _root_headers():
    return {"X-API-Key": ROOT_KEY}


async def _register_account(client, acct):
    resp = await client.post(
        "/api/v1/admin/accounts",
        json={"account_id": acct, "admin_user_id": "alice"},
        headers=_root_headers(),
    )
    assert resp.status_code == 200, resp.text


async def _register_user(client, acct, user_id, role="user"):
    resp = await client.post(
        f"/api/v1/admin/accounts/{acct}/users",
        json={"user_id": user_id, "role": role},
        headers=_root_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["user_key"]


async def _register_account_and_user(client, acct, user_id="bob"):
    await _register_account(client, acct)
    return await _register_user(client, acct, user_id)


def _user_exists(manager, acct, user_id):
    account = manager._accounts.get(acct)
    return account is not None and user_id in account.users


# ---- success path ---------------------------------------------------------


async def test_remove_user_cascades_agfs_vectors_oauth_and_usage(cascade_client):
    acct = _acct("cascade")
    await _register_account_and_user(cascade_client, acct, "bob")

    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=_root_headers()
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["deleted"] is True

    assert len(cascade_client.viking_fs.rm_calls) == 1
    rm = cascade_client.viking_fs.rm_calls[0]
    assert rm["uri"] == "viking://user/bob/"
    assert rm["recursive"] is True
    assert isinstance(rm["ctx"], RequestContext)
    assert rm["ctx"].role == Role.ROOT
    assert rm["ctx"].account_id == acct
    assert rm["ctx"].user.user_id == "bob"

    assert cascade_client.viking_fs.vector_store.delete_user_calls == [(acct, "bob")]
    assert cascade_client._oauth_store.revoke_calls == [
        {"account_id": acct, "user_id": "bob"}
    ]
    assert cascade_client._usage_store.delete_calls == [
        {"account_id": acct, "user_id": "bob"}
    ]
    assert not _user_exists(cascade_client.manager, acct, "bob")


async def test_remove_user_invalidates_api_key(cascade_client):
    acct = _acct("key")
    user_key = await _register_account_and_user(cascade_client, acct, "carol")

    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/carol", headers=_root_headers()
    )
    assert resp.status_code == 200

    # The removed user's key must no longer resolve to an identity.
    from openviking_cli.exceptions import UnauthenticatedError

    with pytest.raises(UnauthenticatedError):
        cascade_client.manager.resolve(user_key)


async def test_remove_user_cascade_is_per_user(cascade_client):
    acct = _acct("sibling")
    await _register_account(cascade_client, acct)
    await _register_user(cascade_client, acct, "bob")
    await _register_user(cascade_client, acct, "carol")

    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=_root_headers()
    )
    assert resp.status_code == 200

    assert len(cascade_client.viking_fs.rm_calls) == 1
    rm = cascade_client.viking_fs.rm_calls[0]
    assert rm["uri"] == "viking://user/bob/"
    assert rm["recursive"] is True
    assert rm["ctx"].user.user_id == "bob"
    assert cascade_client.viking_fs.vector_store.delete_user_calls == [(acct, "bob")]
    assert _user_exists(cascade_client.manager, acct, "carol")


# ---- fail-closed: each configured cleanup stage failing -------------------


@pytest.mark.parametrize(
    "stage",
    ["agfs", "vectordb", "oauth", "usage_audit"],
)
async def test_remove_user_fails_closed_when_cleanup_stage_fails(cascade_client, stage):
    acct = _acct(f"fail_{stage}")
    await _register_account_and_user(cascade_client, acct, "bob")

    if stage == "agfs":
        cascade_client.viking_fs.raise_on_rm = RuntimeError("agfs boom")
    elif stage == "vectordb":
        cascade_client.viking_fs.vector_store.raise_on_delete = RuntimeError(
            "vector boom"
        )
    elif stage == "oauth":
        cascade_client._oauth_store.raise_on_revoke = RuntimeError("oauth boom")
    elif stage == "usage_audit":
        cascade_client._usage_store.raise_on_delete = RuntimeError("usage boom")

    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=_root_headers()
    )
    assert resp.status_code >= 400, resp.text
    assert _user_exists(
        cascade_client.manager, acct, "bob"
    ), f"user must remain registered after {stage} failure for safe retry"

    # Public error must name only the failed stage, never leak raw backend text.
    body = resp.text
    assert stage in body
    assert "boom" not in body

    # The failed stage must still have been attempted so retry can resume.
    if stage == "agfs":
        assert len(cascade_client.viking_fs.rm_calls) == 1
    elif stage == "vectordb":
        assert cascade_client.viking_fs.vector_store.delete_user_calls == [
            (acct, "bob")
        ]
    elif stage == "oauth":
        assert cascade_client._oauth_store.revoke_calls == [
            {"account_id": acct, "user_id": "bob"}
        ]
    elif stage == "usage_audit":
        assert cascade_client._usage_store.delete_calls == [
            {"account_id": acct, "user_id": "bob"}
        ]
        # The accepted-event drain must run before the purge.
        assert cascade_client.app.state.usage_audit_runtime.worker.flushed == 1


async def test_remove_user_retry_succeeds_after_transient_failure(cascade_client):
    """Partial cleanup must be idempotently retryable."""
    acct = _acct("retry")
    await _register_account_and_user(cascade_client, acct, "bob")

    # First attempt: AGFS cleanup fails before vector/oauth/usage run.
    cascade_client.viking_fs.raise_on_rm = RuntimeError("transient agfs")
    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=_root_headers()
    )
    assert resp.status_code >= 400, resp.text
    assert _user_exists(cascade_client.manager, acct, "bob")

    # Retry: AGFS recovers; remaining stages run and the user is removed.
    cascade_client.viking_fs.raise_on_rm = None
    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=_root_headers()
    )
    assert resp.status_code == 200, resp.text
    assert not _user_exists(cascade_client.manager, acct, "bob")
    assert len(cascade_client.viking_fs.rm_calls) == 2
    # Vector delete runs on the successful retry; it is idempotent, so any
    # extra prior invocation (if stages were partially reached) is harmless.
    assert cascade_client.viking_fs.vector_store.delete_user_calls[-1] == (acct, "bob")


async def test_remove_user_rolls_back_live_registry_on_persistence_failure(
    cascade_client, monkeypatch
):
    """If registry persistence fails, the in-memory auth state must roll back."""
    acct = _acct("registry_retry")
    user_key = await _register_account_and_user(cascade_client, acct, "bob")

    legacy = cascade_client.manager._legacy
    original_save = legacy._save_users_json

    async def _fail_save(_account_id):
        raise RuntimeError("registry persistence unavailable")

    monkeypatch.setattr(legacy, "_save_users_json", _fail_save)
    with pytest.raises(RuntimeError, match="registry persistence unavailable"):
        await cascade_client.manager.remove_user(acct, "bob")

    # User is still resolvable after the failed persistence (rollback).
    assert _user_exists(cascade_client.manager, acct, "bob")
    assert cascade_client.manager.resolve(user_key).user_id == "bob"

    # Retry after persistence recovers succeeds and removes the user.
    monkeypatch.setattr(legacy, "_save_users_json", original_save)
    await cascade_client.manager.remove_user(acct, "bob")
    assert not _user_exists(cascade_client.manager, acct, "bob")


# ---- absent optional subsystems are skipped, not failed -------------------


async def test_remove_user_skips_absent_vector_store(cascade_client, monkeypatch):
    acct = _acct("no_vector")
    await _register_account_and_user(cascade_client, acct, "bob")

    class _NoVectorFS(_RecordingVikingFS):
        @property
        def vector_store(self):
            return None

    no_vector_fs = _NoVectorFS()
    # Preserve the AGFS/manager that holds the registered account.
    no_vector_fs.agfs = cascade_client.viking_fs.agfs
    cascade_client.manager._viking_fs = no_vector_fs
    monkeypatch.setattr(admin_router, "get_viking_fs", lambda: no_vector_fs)

    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=_root_headers()
    )
    assert resp.status_code == 200, resp.text
    assert not _user_exists(cascade_client.manager, acct, "bob")
    assert len(no_vector_fs.rm_calls) == 1
    assert cascade_client._oauth_store.revoke_calls
    assert cascade_client._usage_store.delete_calls


async def test_remove_user_skips_absent_oauth_and_usage(cascade_client):
    acct = _acct("no_optional")
    await _register_account_and_user(cascade_client, acct, "bob")

    # Simulate disabled OAuth / usage runtimes by clearing them from app state.
    cascade_client.app.state.oauth_store = None
    cascade_client.app.state.usage_audit_runtime = None

    resp = await cascade_client.delete(
        f"/api/v1/admin/accounts/{acct}/users/bob", headers=_root_headers()
    )
    assert resp.status_code == 200, resp.text
    assert not _user_exists(cascade_client.manager, acct, "bob")
    assert len(cascade_client.viking_fs.rm_calls) == 1
    assert cascade_client.viking_fs.vector_store.delete_user_calls == [(acct, "bob")]
