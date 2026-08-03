from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.server.identity import RequestContext
from openviking.service.core import OpenVikingService


@pytest.mark.asyncio
async def test_add_resource_indexing_logic(
    service: OpenVikingService,
    request_context: RequestContext,
    tmp_path,
):
    """The resource service must honor build_index and summarize independently."""
    resource_file = tmp_path / "test_doc.md"
    resource_file.write_text("# Test Document\n\nThis is a test document.", encoding="utf-8")

    mock_parse_result = MagicMock()
    mock_parse_result.source_path = str(resource_file)
    mock_parse_result.meta = {}
    mock_parse_result.temp_dir_path = "/tmp/fake_temp_dir"
    mock_parse_result.warnings = []
    mock_parse_result.source_format = "markdown"

    mock_context_tree = MagicMock()
    mock_context_tree.root = MagicMock()
    mock_context_tree.root.uri = "viking://resources/test_doc"
    mock_context_tree.root.temp_uri = None

    with (
        patch(
            "openviking.utils.summarizer.Summarizer.summarize",
            new_callable=AsyncMock,
        ) as mock_summarize,
        patch(
            "openviking.utils.media_processor.UnifiedResourceProcessor.process",
            new_callable=AsyncMock,
            return_value=mock_parse_result,
        ),
        patch(
            "openviking.parse.tree_builder.TreeBuilder.finalize_from_temp",
            new_callable=AsyncMock,
            return_value=mock_context_tree,
        ),
    ):
        mock_summarize.return_value = {"status": "success"}

        await service.resources._execute_resource_ingestion(
            path=str(resource_file),
            ctx=request_context,
            defer_post_processing=False,
            build_index=True,
        )
        assert mock_summarize.call_count == 1
        assert mock_summarize.call_args.kwargs.get("skip_vectorization") is False

        mock_summarize.reset_mock()
        await service.resources._execute_resource_ingestion(
            path=str(resource_file),
            ctx=request_context,
            defer_post_processing=False,
            build_index=False,
            summarize=True,
        )
        assert mock_summarize.call_count == 1
        assert mock_summarize.call_args.kwargs.get("skip_vectorization") is True

        mock_summarize.reset_mock()
        await service.resources._execute_resource_ingestion(
            path=str(resource_file),
            ctx=request_context,
            defer_post_processing=False,
            build_index=False,
            summarize=False,
        )
        mock_summarize.assert_not_called()
