# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import pytest

from openviking.service.resource_service import ResourceService
from openviking.storage.search_tags_sidecar import (
    SEARCH_TAGS_SIDECAR_FILENAME,
    load_search_tags_for_uri,
    resource_root_uri_for,
    sidecar_uri_for_root,
    write_search_tags_sidecar,
)
from openviking.utils.embedding_utils import _apply_scalar_overrides


class _FakeVikingFS:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def write_file(self, uri, content, ctx=None, lock_handle=None):
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[uri] = content

    async def exists(self, uri, ctx=None):
        return uri in self.files

    async def read_file(self, uri, ctx=None):
        return self.files[uri].decode("utf-8")


class _FakeEmbeddingMsg:
    def __init__(self):
        self.context_data = {}


def test_resource_root_for_public_resource_paths():
    assert (
        resource_root_uri_for("viking://resources/myrepo/docs/a.md")
        == "viking://resources/myrepo"
    )
    assert resource_root_uri_for("viking://resources/myrepo") == "viking://resources/myrepo"


def test_resource_root_for_user_scoped_resource_paths():
    assert (
        resource_root_uri_for("viking://user/alice/resources/notes/a.md")
        == "viking://user/alice/resources/notes"
    )


def test_resource_root_rejects_non_resource_paths():
    assert resource_root_uri_for("viking://resources") is None
    assert resource_root_uri_for("viking://user/alice/memories/profile.md") is None
    assert resource_root_uri_for("viking://temp/abc/file.md") is None


@pytest.mark.asyncio
async def test_sidecar_write_load_roundtrip():
    fs = _FakeVikingFS()
    root = "viking://resources/myrepo"
    await write_search_tags_sidecar(root, ["team=infra", "env=prod"], ctx=None, viking_fs=fs)

    assert sidecar_uri_for_root(root) == f"{root}/{SEARCH_TAGS_SIDECAR_FILENAME}"
    tags = await load_search_tags_for_uri(f"{root}/docs/a.md", ctx=None, viking_fs=fs)
    assert tags == ["team=infra", "env=prod"]


@pytest.mark.asyncio
async def test_sidecar_load_missing_returns_none():
    fs = _FakeVikingFS()
    tags = await load_search_tags_for_uri(
        "viking://resources/myrepo/docs/a.md", ctx=None, viking_fs=fs
    )
    assert tags is None


@pytest.mark.asyncio
async def test_sidecar_load_tolerates_corrupt_content():
    fs = _FakeVikingFS()
    root = "viking://resources/myrepo"
    fs.files[sidecar_uri_for_root(root)] = b"not json"
    assert await load_search_tags_for_uri(f"{root}/a.md", ctx=None, viking_fs=fs) is None

    fs.files[sidecar_uri_for_root(root)] = json.dumps({"search_tags": ["notkv"]}).encode()
    assert await load_search_tags_for_uri(f"{root}/a.md", ctx=None, viking_fs=fs) is None


@pytest.mark.asyncio
async def test_sidecar_load_normalizes_tags():
    fs = _FakeVikingFS()
    root = "viking://resources/myrepo"
    fs.files[sidecar_uri_for_root(root)] = json.dumps(
        {"search_tags": ["Team=Infra", " team=infra "]}
    ).encode()
    assert await load_search_tags_for_uri(f"{root}/a.md", ctx=None, viking_fs=fs) == [
        "team=infra"
    ]


def test_scalar_override_carries_search_tags():
    msg = _FakeEmbeddingMsg()
    _apply_scalar_overrides(msg, {"search_tags": ["team=infra"], "ignored": "x"})
    assert msg.context_data == {"search_tags": ["team=infra"]}


def test_connector_rejects_search_tags():
    unsupported = ResourceService._unsupported_connector_params(
        add_type="tos",
        wait=False,
        instruction="",
        build_index=True,
        summarize=False,
        watch_interval=0,
        connector_args={},
        kwargs={"search_tags": ["team=infra"]},
        to="viking://resources/x",
        parent=None,
    )
    assert any("search_tags" in item for item in unsupported)


def test_connector_allows_requests_without_search_tags():
    unsupported = ResourceService._unsupported_connector_params(
        add_type="tos",
        wait=False,
        instruction="",
        build_index=True,
        summarize=False,
        watch_interval=0,
        connector_args={},
        kwargs={},
        to="viking://resources/x",
        parent=None,
    )
    assert not any("search_tags" in item for item in unsupported)
