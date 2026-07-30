from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_extract_merges_sessions_in_order_and_runs_extractor_once(monkeypatch):
    compressor = Mock()
    compressor.extract_long_term_memories = AsyncMock(return_value=[])
    service = SessionService(viking_fs=Mock(), session_compressor=compressor)
    sessions = {
        "session-1": SimpleNamespace(
            uri="viking://user/alice/sessions/session-1",
            messages=[
                Message(
                    id="message-1",
                    role="user",
                    parts=[TextPart(text="first batch")],
                    peer_id="conv-26",
                )
            ],
        ),
        "session-2": SimpleNamespace(
            uri="viking://user/alice/sessions/session-2",
            messages=[
                Message(
                    id="message-2",
                    role="user",
                    parts=[TextPart(text="second batch")],
                    peer_id="conv-26",
                )
            ],
        ),
    }
    monkeypatch.setattr(
        service,
        "get",
        AsyncMock(side_effect=lambda session_id, _ctx: sessions[session_id]),
    )
    monkeypatch.setattr(service, "_record_lifecycle_metric", Mock())
    ctx = RequestContext(
        user=UserIdentifier("default", "alice"),
        role=Role.ADMIN,
    )

    await service.extract(
        "session-1",
        ctx,
        additional_session_ids=["session-2"],
        memory_policy={
            "self": {"enabled": False},
            "peer": {"enabled": True},
            "memory_types": ["entities", "events", "preferences", "profile"],
        },
    )

    compressor.extract_long_term_memories.assert_awaited_once()
    kwargs = compressor.extract_long_term_memories.call_args.kwargs
    assert [message.content for message in kwargs["messages"]] == [
        "first batch",
        "second batch",
    ]
    assert kwargs["session_id"] == "session-1"
    assert kwargs["allowed_peer_ids"] == {"conv-26"}
