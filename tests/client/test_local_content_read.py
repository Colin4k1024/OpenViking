# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.client.local import LocalClient
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils


def _client_with_content(content: str) -> LocalClient:
    client = object.__new__(LocalClient)
    client._ctx = object()
    client._service = SimpleNamespace(
        fs=SimpleNamespace(read=AsyncMock(return_value=content))
    )
    return client


@pytest.mark.asyncio
async def test_local_read_hides_memory_fields_but_read_raw_preserves_them():
    uri = "viking://user/default/memories/cases/example.md"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=uri,
            content="# Example\n\nAgent-visible content.",
            memory_type="cases",
            extra_fields={
                "case_identity": '{"goal":"hidden"}',
                "case_status": "promoted",
            },
        )
    )
    client = _client_with_content(raw)

    assert await LocalClient.read(client, uri) == "# Example\n\nAgent-visible content."
    assert await LocalClient.read_raw(client, uri) == raw
