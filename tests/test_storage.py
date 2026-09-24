from uuid import uuid4

import pytest

from app.agent import DecisionAgent
from app.storage import InMemoryRunStore
from tests.helpers import make_request

pytestmark = pytest.mark.anyio


async def test_storage_rejects_nonpositive_capacity():
    with pytest.raises(ValueError):
        InMemoryRunStore(0)


async def test_missing_record_returns_none():
    assert await InMemoryRunStore().get(uuid4()) is None


async def test_read_copy_isolates_nested_request_and_response():
    store = InMemoryRunStore()
    result = await DecisionAgent(store).run(
        make_request(documents=[{"id": "a", "text": "alpha"}])
    )
    copy = await store.get(result.run_id)
    copy.response.message = "changed"
    copy.response.plan.append("changed")
    copy.request.documents[0].text = "changed"
    saved = await store.get(result.run_id)
    assert saved.response == result
    assert saved.request.documents[0].text == "alpha"


async def test_write_copy_isolates_nested_request_and_response():
    store = InMemoryRunStore()
    result = await DecisionAgent(store).run(
        make_request(documents=[{"id": "a", "text": "alpha"}])
    )
    record = await store.get(result.run_id)
    record.response.message = "replacement"
    await store.save(record)
    record.response.message = "changed after save"
    record.request.documents[0].text = "changed after save"
    saved = await store.get(result.run_id)
    assert saved.response.message == "replacement"
    assert saved.request.documents[0].text == "alpha"


async def test_capacity_evicts_oldest_saved_record():
    store = InMemoryRunStore(2)
    agent = DecisionAgent(store)
    first, second, third = [await agent.run(make_request()) for _ in range(3)]
    assert await store.get(first.run_id) is None
    assert (await store.get(second.run_id)).response == second
    assert (await store.get(third.run_id)).response == third


async def test_overwrite_refreshes_eviction_order():
    store = InMemoryRunStore(2)
    agent = DecisionAgent(store)
    first, second = [await agent.run(make_request()) for _ in range(2)]
    record = await store.get(first.run_id)
    record.response.message = "updated"
    await store.save(record)
    third = await agent.run(make_request())
    assert await store.get(second.run_id) is None
    assert (await store.get(first.run_id)).response.message == "updated"
    assert (await store.get(third.run_id)).response == third
