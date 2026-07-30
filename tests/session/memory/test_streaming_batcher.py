# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio

import pytest

from openviking.session.memory.utils.streaming_batcher import (
    StreamingBatcher,
    StreamingBatcherConfig,
)


@pytest.mark.asyncio
async def test_streaming_batcher_enforces_hard_limit_per_process_call():
    processed_batches: list[list[int]] = []

    async def process_batch(items: list[int], reason: str) -> int:
        del reason
        processed_batches.append(items)
        return len(items)

    batcher = StreamingBatcher(
        name="hard-limit-test",
        process_batch=process_batch,
        config=StreamingBatcherConfig(
            max_items_per_batch=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.005,
        ),
    )

    await asyncio.gather(*(batcher.submit(item) for item in range(10)))
    await batcher.close()

    assert [len(batch) for batch in processed_batches] == [8, 2]
    assert all(len(batch) <= 8 for batch in processed_batches)
    assert [item for batch in processed_batches for item in batch] == list(range(10))


@pytest.mark.asyncio
async def test_streaming_batcher_rejects_single_item_larger_than_hard_limit():
    async def process_batch(items: list[list[int]], reason: str) -> int:
        del reason
        return len(items)

    batcher = StreamingBatcher(
        name="oversized-item-test",
        process_batch=process_batch,
        item_size=len,
        config=StreamingBatcherConfig(max_items_per_batch=8),
    )

    with pytest.raises(ValueError, match="exceeds hard batch limit 8"):
        await batcher.submit(list(range(9)))
