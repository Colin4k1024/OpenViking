# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ambiguous .ts media detection."""

from openviking.parse.parsers.media.utils import (
    MPEG_TS_PROBE_BYTES,
    get_media_type,
    read_mpeg_ts_probe,
)
from openviking.service.resource_service import ResourceService


class _ParserAPIConfig:
    enable = True
    extensions = ["ts"]


class _OpenVikingConfig:
    parser_api = _ParserAPIConfig()


def test_get_media_type_uses_viking_video_uri_for_ts_resource():
    assert get_media_type("viking://resources/video/2026/07/27/test2_h264.ts", None) == "video"


def test_get_media_type_detects_local_mpeg_ts_by_magic_bytes(tmp_path):
    packet = b"\x47" + (b"\x00" * 187)
    source = tmp_path / "test2_h264.ts"
    source.write_bytes(packet * 3)

    assert get_media_type(str(source), None) == "video"


def test_get_media_type_does_not_classify_typescript_as_video(tmp_path):
    source = tmp_path / "hello.ts"
    source.write_text("console.log('hello');")

    assert get_media_type(str(source), None) is None


def test_read_mpeg_ts_probe_reads_only_probe_bytes(tmp_path):
    source = tmp_path / "large.ts"
    source.write_bytes(b"\x47" * (MPEG_TS_PROBE_BYTES + 1024))

    assert read_mpeg_ts_probe(source) == b"\x47" * MPEG_TS_PROBE_BYTES


async def test_remote_mpeg_ts_probe_keeps_only_probe_bytes(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"\x47" * (MPEG_TS_PROBE_BYTES + 1024)

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, headers):
            assert method == "GET"
            assert headers["Range"] == f"bytes=0-{MPEG_TS_PROBE_BYTES - 1}"
            return FakeStream()

    monkeypatch.setattr("openviking.service.resource_service.httpx.AsyncClient", FakeClient)

    probe = await ResourceService()._read_remote_mpeg_ts_probe("https://example.com/video.ts")

    assert probe == b"\x47" * MPEG_TS_PROBE_BYTES


async def test_typescript_mpeg_ts_url_info_uses_bounded_probe(monkeypatch):
    packet = b"\x47" + (b"\x00" * 187)
    service = ResourceService()

    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: _OpenVikingConfig(),
    )

    async def fake_probe(path, *, request_validator=None):
        assert path == "https://example.com/video.ts?token=1"
        return packet * 3

    monkeypatch.setattr(service, "_read_remote_mpeg_ts_probe", fake_probe)

    source_info = await service._resolve_typescript_mpeg_ts_url_info(
        "https://example.com/video.ts?token=1",
        source_name=None,
    )

    assert source_info is not None
    assert source_info.source_name == "video.ts"
    assert source_info.source_path == "https://example.com/video.ts?token=1"
    assert source_info.source_format == "video"
