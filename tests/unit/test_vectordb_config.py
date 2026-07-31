# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def test_vectors_only_abstract_preview_tokens_defaults_to_follow_embedding_limit():
    cfg = VectorDBBackendConfig()

    assert cfg.vectors_only_abstract_preview_tokens is None


@pytest.mark.parametrize("value", [None, 0, 128, 8192])
def test_vectors_only_abstract_preview_tokens_accepts_supported_values(value):
    cfg = VectorDBBackendConfig(vectors_only_abstract_preview_tokens=value)

    assert cfg.vectors_only_abstract_preview_tokens == value


@pytest.mark.parametrize("value", [-1, 8193])
def test_vectors_only_abstract_preview_tokens_rejects_out_of_range_values(value):
    with pytest.raises(ValueError):
        VectorDBBackendConfig(vectors_only_abstract_preview_tokens=value)
