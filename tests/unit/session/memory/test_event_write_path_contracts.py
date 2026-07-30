# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Event write-path behavior contracts.

Pins two memory-write behaviors so future template / formatter changes
do not regress:
  - events.yaml prefers a short summary over the full transcript
  - _format_contiguous_group suppresses turns with no text content
"""

from openviking.message import Message
from openviking.message.part import TextPart, ToolPart
from openviking.prompts.manager import PromptManager
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MessageRange,
)
from openviking.session.memory.utils import MemoryFileUtils


def test_events_template_uses_short_summary_instead_of_full_transcript():
    """When summary is much shorter than the transcript, the rendered
    event body should contain the summary, not the full pretty-print."""
    long_text = "hello world " * 100
    short_summary = "User talked about expanding a clothing store."

    extract_ctx = ExtractContext(
        messages=[
            Message(
                id="1",
                role="user",
                parts=[TextPart(text=long_text)],
                created_at="2024-01-01T00:00:00",
            )
        ]
    )

    registry = MemoryTypeRegistry(load_schemas=False)
    registry.load_from_yaml(
        str(PromptManager._get_bundled_templates_dir() / "memory" / "events.yaml")
    )

    rendered = MemoryFileUtils.write(
        MemoryFile(
            extra_fields={
                "event_name": "user_message",
                "goal": "",
                "summary": short_summary,
                "ranges": "0",
            }
        ),
        content_template=registry.get("events").content_template,
        extract_context=extract_ctx,
    )

    assert short_summary in rendered
    assert "hello world " * 10 not in rendered


def test_format_contiguous_group_skips_turns_with_no_text():
    """Turns with only non-text parts (e.g. ToolPart) do not produce
    an empty speaker line."""
    mr = MessageRange(elements=[])
    msgs = [
        Message(
            id="1",
            role="user",
            parts=[TextPart(text="hello")],
            created_at="2024-01-01T00:00:00",
        ),
        Message(
            id="2",
            role="assistant",
            parts=[ToolPart(tool_name="tool_a")],
            created_at="2024-01-01T00:00:00",
        ),
        Message(
            id="3",
            role="assistant",
            parts=[TextPart(text="done")],
            created_at="2024-01-01T00:00:00",
        ),
    ]

    lines = mr._format_contiguous_group(msgs)
    non_empty = [l for l in lines if l.strip()]
    assert len(non_empty) == 2
