# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persist add-time search tags beside a resource root.

The sidecar is the durable source for ``search_tags`` supplied at
``add_resource`` time: vector records are rebuildable artifacts, so tags stored
only on them would vanish on a collection rebuild. Vector build paths
(semantic processing and reindex) read the sidecar and stamp its tags onto
every record created under the resource root.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from openviking.server.identity import RequestContext
from openviking.utils.tags import normalize_search_tags
from openviking_cli.utils import get_logger
from openviking_cli.utils.uri import VikingURI

logger = get_logger(__name__)

SEARCH_TAGS_SIDECAR_FILENAME = ".search_tags.json"


def resource_root_uri_for(uri: str) -> Optional[str]:
    """Return the resource root that owns ``uri``, or None outside resources.

    Mirrors the root resolution in ContentWriteCoordinator._resolve_root_uri:
    ``viking://resources/<name>/...`` roots at ``viking://resources/<name>``,
    ``viking://user/<u>/resources/<name>/...`` at its resource directory.
    Memory, skill, and temp URIs have no resource root and return None, as
    does the bare resources collection root itself.
    """
    try:
        parsed = VikingURI(uri)
        parts = [part for part in parsed.full_path.split("/") if part]
    except Exception:
        return None
    if not parts:
        return None
    if parts[0] == "resources":
        if len(parts) < 2:
            return None
        return VikingURI.build("resources", parts[1])
    if parts[0] == "user" and "resources" in parts:
        resources_idx = parts.index("resources")
        if len(parts) <= resources_idx + 1:
            return None
        return VikingURI.build(*parts[: resources_idx + 2])
    return None


def sidecar_uri_for_root(root_uri: str) -> str:
    return f"{root_uri.rstrip('/')}/{SEARCH_TAGS_SIDECAR_FILENAME}"


async def write_search_tags_sidecar(
    root_uri: str,
    tags: list[str],
    *,
    ctx: RequestContext,
    viking_fs: Any = None,
    lock_handle: Any = None,
) -> None:
    """Write normalized add-time tags to ``<root>/.search_tags.json``."""
    if viking_fs is None:
        from openviking.storage import get_viking_fs

        viking_fs = get_viking_fs()
    content = json.dumps({"search_tags": list(tags)}, ensure_ascii=False, indent=2)
    await viking_fs.write_file(
        sidecar_uri_for_root(root_uri),
        content,
        ctx=ctx,
        lock_handle=lock_handle,
    )


async def load_search_tags_for_uri(
    uri: str,
    *,
    ctx: RequestContext,
    viking_fs: Any = None,
) -> Optional[list[str]]:
    """Load the sidecar tags that apply to ``uri``, or None when absent.

    Any read or parse failure returns None: sidecar tags must never block
    vector building, and a missing sidecar is the common case.
    """
    root_uri = resource_root_uri_for(uri)
    if not root_uri:
        return None
    if viking_fs is None:
        from openviking.storage import get_viking_fs

        viking_fs = get_viking_fs()
    sidecar_uri = sidecar_uri_for_root(root_uri)
    try:
        if not await viking_fs.exists(sidecar_uri, ctx=ctx):
            return None
        content = await viking_fs.read_file(sidecar_uri, ctx=ctx)
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        payload = json.loads(content or "{}")
        tags = normalize_search_tags(payload.get("search_tags"))
        return tags or None
    except Exception as exc:
        logger.debug("Failed to load search tags sidecar %s: %s", sidecar_uri, exc)
        return None
