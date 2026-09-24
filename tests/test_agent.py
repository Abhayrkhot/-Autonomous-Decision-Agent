import pytest

from app.agent import DecisionAgent
from app.storage import InMemoryRunStore
from app.tools import ToolInput, ToolRegistry, default_registry
from tests.helpers import make_request

pytestmark = pytest.mark.anyio


async def test_complete_run_is_persisted():
    store = InMemoryRunStore()
    request = make_request(documents=[{"id": "guide", "text": "alpha help"}])
    result = await DecisionAgent(store).run(request)
    saved = await store.get(result.run_id)
    assert saved.request == request
    assert saved.response == result
    assert result.retrieved_context[0].document_id == "guide"
    assert result.evaluation.objective_token_coverage == 1.0
    assert [tool.tool for tool in result.tool_results] == [
        "draft_message",
        "create_follow_up",
    ]


async def test_no_match_draft_reports_missing_evidence():
    result = await DecisionAgent(InMemoryRunStore()).run(make_request())
    assert result.retrieved_context == []
    assert "No relevant supporting document" in result.message


async def test_results_are_deterministic_except_run_id():
    agent = DecisionAgent(InMemoryRunStore())
    first, second = await agent.run(make_request()), await agent.run(make_request())
    assert first.model_dump(exclude={"run_id"}) == second.model_dump(exclude={"run_id"})


async def test_unknown_tool_is_rejected():
    with pytest.raises(ValueError, match="Unknown tool"):
        await default_registry().execute(
            "shell",
            ToolInput(request=make_request(), action="help", retrieved=[]),
        )


async def test_duplicate_tool_is_rejected():
    async def unused(data):
        return "unused"

    with pytest.raises(ValueError, match="already registered"):
        default_registry().register("draft_message", unused)


async def test_tool_failure_does_not_save():
    calls = []

    class RecordingStore(InMemoryRunStore):
        async def save(self, record):
            calls.append(record)

    async def failing(data):
        raise RuntimeError("tool failed")

    registry = ToolRegistry()
    registry.register("draft_message", failing)
    with pytest.raises(RuntimeError, match="tool failed"):
        await DecisionAgent(RecordingStore(), registry).run(make_request())
    assert calls == []


async def test_retrieved_text_is_incorporated_in_draft():
    agent = DecisionAgent(InMemoryRunStore())
    first = await agent.run(
        make_request(documents=[{"id": "same", "text": "alpha first"}])
    )
    second = await agent.run(
        make_request(documents=[{"id": "same", "text": "alpha second"}])
    )
    assert "alpha first" in first.message
    assert "alpha second" in second.message


async def test_follow_up_is_a_local_review_task():
    result = await default_registry().execute(
        "create_follow_up",
        ToolInput(request=make_request(), action="help", retrieved=[]),
    )
    assert result.tool == "create_follow_up"
    assert result.output == "Pending human review: help for customer."
