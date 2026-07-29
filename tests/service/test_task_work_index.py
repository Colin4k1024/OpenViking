# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

from openviking.service.task_work_index import (
    TASK_ACCOUNT_ID_FIELD,
    TASK_USER_ID_FIELD,
    TASK_WORK_ID_FIELD,
    TaskWorkIndex,
    bind_task_context,
    extract_task_metadata,
    prepare_task_payload,
)


def test_task_context_is_persisted_and_rebuilt_from_queue_messages():
    with bind_task_context("task-1", "acme", "alice"):
        payload, metadata = prepare_task_payload({"uri": "viking://resources/demo"})

    assert metadata is not None
    assert payload["task_id"] == "task-1"
    assert payload[TASK_ACCOUNT_ID_FIELD] == "acme"
    assert payload[TASK_USER_ID_FIELD] == "alice"
    assert payload[TASK_WORK_ID_FIELD]

    envelope = {"id": "queue-id", "data": json.dumps(payload)}
    rebuilt = TaskWorkIndex()
    owners = rebuilt.rebuild({"Semantic": [envelope]})

    assert rebuilt.has_work("task-1")
    assert owners == {"task-1": ("acme", "alice")}
    rebuilt.settle("Semantic", envelope)
    assert not rebuilt.has_work("task-1")


def test_legacy_task_message_uses_queuefs_id_as_work_id():
    envelope = {
        "id": "legacy-queue-id",
        "data": '{"task_id":"task-1","account_id":"acme","user_id":"alice"}',
    }

    metadata = extract_task_metadata(envelope)

    assert metadata is not None
    assert metadata.work_id == "queuefs:legacy-queue-id"


def test_rebuild_preserves_work_registered_during_startup():
    index = TaskWorkIndex()
    _, metadata = prepare_task_payload(
        {
            "task_id": "task-1",
            "account_id": "acme",
            "user_id": "alice",
        }
    )
    index.register("Semantic", metadata)

    owners = index.rebuild({"Semantic": []})

    assert index.has_work("task-1")
    assert owners == {}
