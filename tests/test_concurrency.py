import asyncio
from uuid import UUID

import httpx
import pytest

from app.agent import DecisionAgent
from app.main import create_app
from app.storage import InMemoryRunStore
from app.tools import ToolRegistry, create_follow_up, draft_message
from tests.helpers import make_request


async def exercise_interleaving(count=300, capacity=50):
    active = 0
    peak_active = 0

    async def yielding_draft(data):
        nonlocal active, peak_active
        active += 1
        peak_active = max(active, peak_active)
        await asyncio.sleep(0)
        result = await draft_message(data)
        active -= 1
        return result

    registry = ToolRegistry()
    registry.register("draft_message", yielding_draft)
    registry.register("create_follow_up", create_follow_up)
    store = InMemoryRunStore(capacity)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(store, registry=registry)),
        base_url="http://test",
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/agent/run",
                    json=make_request(
                        context={
                            "name": str(index),
                            "details": "alpha",
                        }
                    ).model_dump(),
                )
                for index in range(count)
            )
        )
    assert peak_active > 1, "The test must exercise interleaving"
    assert all(response.status_code == 200 for response in responses)
    for index, response in enumerate(responses):
        assert response.json()["message"].startswith(f"Hi {index},"), (
            "request isolation"
        )
    records = [
        await store.get(UUID(response.json()["run_id"])) for response in responses
    ]
    retained = [record for record in records if record is not None]
    assert len(retained) == capacity
    for record in retained:
        assert record.response.message.startswith(f"Hi {record.request.context.name},")


@pytest.mark.anyio
@pytest.mark.stress
async def test_300_interleaved_api_requests_are_isolated():
    await exercise_interleaving()


@pytest.mark.anyio
async def test_isolation_check_detects_deliberately_shared_request_state(monkeypatch):
    class RacyAgent(DecisionAgent):
        async def run(self, request):
            self.shared_request = request
            await asyncio.sleep(0)
            return await super().run(self.shared_request)

    # Negative control: the same assertions must catch an actual interleaving bug.
    monkeypatch.setattr("app.main.DecisionAgent", RacyAgent)
    with pytest.raises(AssertionError, match="request isolation"):
        await exercise_interleaving(count=30, capacity=5)
