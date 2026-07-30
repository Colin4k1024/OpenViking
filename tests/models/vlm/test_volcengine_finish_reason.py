# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

from openviking.models.vlm.backends.volcengine_vlm import (
    VolcEngineTextResponse,
    VolcEngineVLM,
)


def test_text_response_preserves_finish_reason_without_tools():
    vlm = VolcEngineVLM({"model": "test-model", "api_key": "test-key"})
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"groups":[]}', tool_calls=None),
                finish_reason="length",
            )
        ],
        usage=None,
    )

    result = vlm._build_vlm_response(response, has_tools=False)

    assert isinstance(result, str)
    assert isinstance(result, VolcEngineTextResponse)
    assert result.finish_reason == "length"
