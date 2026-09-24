import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from psycopg.errors import UniqueViolation
from psycopg_pool import PoolTimeout

from app.agent import DecisionAgent
from app.main import create_app
from app.models import AgentRequest
from app.postgres import PostgresRunStore, migrate
from app.storage import InMemoryRunStore, RunRecord

DSN = os.getenv("TEST_DATABASE_URL")


def sample():
    return AgentRequest(objective="help", context={"details": "help"})


@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_storage_contract(backend):
    if backend == "postgres" and not DSN:
        pytest.skip("TEST_DATABASE_URL required for real PostgreSQL tests")

    async def scenario():
        owner = str(uuid4())
        if backend == "postgres":
            await migrate(DSN)
            await migrate(DSN)
            store = PostgresRunStore(DSN)
            await store.open()
        else:
            store = InMemoryRunStore()
        try:
            assert await store.get(uuid4()) is None
            response = await DecisionAgent(InMemoryRunStore()).run(sample())
            record = RunRecord(owner_id=owner, request=sample(), response=response)
            await store.save(record)
            assert await store.get(response.run_id, owner) == record
            assert await store.get(response.run_id, "stranger") is None
            with pytest.raises(ValueError, match="owner"):
                await store.save(record.model_copy(update={"owner_id": "stranger"}))
            assert await store.list(owner) == [record]
            assert await store.list(owner, offset=1) == []
            for limit, offset in [(0, 0), (101, 0), (1, -1)]:
                with pytest.raises(ValueError):
                    await store.list(owner, limit, offset)
            record.response.message = "updated"
            await store.save(record)
            assert (
                await store.get(response.run_id, owner)
            ).response.message == "updated"
            records = [
                RunRecord(
                    owner_id=owner,
                    request=sample(),
                    response=await DecisionAgent(InMemoryRunStore()).run(sample()),
                )
                for _ in range(30)
            ]
            await asyncio.gather(*(store.save(item) for item in records))
            assert len(await store.list(owner, 100)) == 31
            if backend == "postgres":
                await store.close()
                store = PostgresRunStore(DSN)
                await store.open()
                assert (
                    await store.get(response.run_id, owner)
                ).response.message == "updated"
        finally:
            if backend == "postgres":
                await store.close()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL required")
def test_postgres_rollback_pool_exhaustion_and_migration_checksum():
    async def scenario():
        await migrate(DSN)
        store = PostgresRunStore(DSN, max_size=1, timeout=0.1)
        await store.open()
        try:
            async with store.pool.connection() as conn:
                with pytest.raises(PoolTimeout):
                    await store.get(uuid4())
                with pytest.raises(UniqueViolation):
                    async with conn.transaction():
                        await conn.execute(
                            "INSERT INTO schema_migrations VALUES ('rollback-test', 'x')"
                        )
                        await conn.execute(
                            "INSERT INTO schema_migrations VALUES ('rollback-test', 'y')"
                        )
                cursor = await conn.execute(
                    "SELECT name FROM schema_migrations WHERE name='rollback-test'"
                )
                assert await cursor.fetchone() is None
                await conn.execute(
                    "UPDATE schema_migrations SET checksum='bad' WHERE name='001_runs.sql'"
                )
            with pytest.raises(ValueError, match="checksum"):
                await migrate(DSN)
        finally:
            import hashlib
            from pathlib import Path

            async with store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE schema_migrations SET checksum=%s WHERE name='001_runs.sql'",
                    (
                        hashlib.sha256(
                            Path("migrations/001_runs.sql").read_bytes()
                        ).hexdigest(),
                    ),
                )
            await store.close()

    asyncio.run(scenario())


def test_history_api():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            response = (
                await client.post("/agent/run", json=sample().model_dump())
            ).json()
            assert (await client.get(f"/agent/runs/{response['run_id']}")).json()[
                "status"
            ] == "completed"
            assert len((await client.get("/agent/runs")).json()) == 1
            assert (await client.get(f"/agent/runs/{uuid4()}")).status_code == 404
            assert (await client.get("/agent/runs?limit=101")).status_code == 422

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL required")
def test_postgres_application_lifecycle(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", DSN)
    with TestClient(create_app()) as client:
        result = client.post("/agent/run", json=sample().model_dump())
        assert result.status_code == 200
        run_id = result.json()["run_id"]
    with TestClient(create_app()) as client:
        assert client.get(f"/agent/runs/{run_id}").status_code == 200
