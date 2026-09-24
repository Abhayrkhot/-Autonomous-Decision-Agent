import asyncio

import pytest

from app.agent import DecisionAgent
from app.models import AgentRequest, Document
from app.retrieval import retrieve
from app.storage import InMemoryRunStore
from app.tools import ToolInput, default_registry


def request(**overrides):
    return AgentRequest.model_validate(
        {
            "objective": "Improve onboarding",
            "context": {"name": "Sam", "details": "Needs onboarding assistance"},
            **overrides,
        }
    )


def test_complete_run_and_storage_isolation():
    async def scenario():
        store = InMemoryRunStore(capacity=1)
        agent = DecisionAgent(store)
        first = await agent.run(
            request(
                documents=[{"id": "guide", "text": "Onboarding assistance guide"}],
                outcome_signals={"engagement": "low"},
            )
        )
        assert first.retrieved_context[0].document_id == "guide"
        assert "Supporting reference [guide]" in first.message
        assert first.recommended_action.startswith("Offer assistance")
        assert first.evaluation.objective_token_coverage == 1.0
        assert [item.tool for item in first.tool_results] == [
            "draft_message",
            "create_follow_up",
        ]
        saved = await store.get(first.run_id)
        assert saved.response == first
        first.message = "changed"
        assert (await store.get(first.run_id)).response.message != "changed"
        second = await agent.run(request())
        assert await store.get(first.run_id) is None
        assert (await store.get(second.run_id)).response == second
        assert "No relevant supporting document" in second.message

    asyncio.run(scenario())


def test_retrieval_ranking_ties_and_no_match():
    docs = [
        Document(id="b", text="onboarding"),
        Document(id="a", text="onboarding"),
        Document(id="c", text="gardening soil"),
    ]
    assert [hit.document_id for hit in retrieve("onboarding", docs)] == ["a", "b"]
    assert retrieve("unrelated", docs) == []
    assert retrieve("the and", docs) == []


def test_unknown_and_duplicate_tools_are_rejected():
    async def scenario():
        registry = default_registry()
        with pytest.raises(ValueError, match="Unknown tool"):
            await registry.execute("shell", ToolInput(request(), "help", []))
        with pytest.raises(ValueError, match="already registered"):
            registry.register("draft_message", lambda _: None)

    asyncio.run(scenario())


def test_results_are_deterministic_except_run_id():
    async def scenario():
        agent = DecisionAgent(InMemoryRunStore())
        first, second = await agent.run(request()), await agent.run(request())
        assert first.run_id != second.run_id
        assert first.model_dump(exclude={"run_id"}) == second.model_dump(
            exclude={"run_id"}
        )

    asyncio.run(scenario())
