import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.actions import ActionError, ActionService, Approval
from app.agent import DecisionAgent
from app.auth import Principal
from app.main import create_app
from app.models import AgentRequest
from app.postgres import PostgresRunStore, migrate

DSN = os.getenv("TEST_DATABASE_URL")
USER, REVIEWER, OTHER = "u" * 32, "r" * 32, "o" * 32


def payload():
    return {"objective": "help", "context": {"details": "help"}}


def configure(monkeypatch, owner):
    monkeypatch.setenv(
        "API_TOKENS_JSON",
        json.dumps(
            {
                USER: {"owner_id": owner, "actor": "user"},
                REVIEWER: {"owner_id": owner, "actor": "reviewer", "role": "reviewer"},
                OTHER: {"owner_id": "other", "actor": "other", "role": "reviewer"},
            }
        ),
    )


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_authentication_and_isolation(monkeypatch):
    configure(monkeypatch, str(uuid4()))
    with TestClient(create_app()) as client:
        for token in [None, "bad", "🔑"]:
            response = client.get(
                "/agent/runs",
                headers=headers(token) if token and token.isascii() else {},
            )
            assert response.status_code == 401
        result = client.post("/agent/run", json=payload(), headers=headers(USER)).json()
        assert (
            client.get(
                f"/agent/runs/{result['run_id']}", headers=headers(OTHER)
            ).status_code
            == 404
        )
        assert client.get("/agent/runs", headers=headers(OTHER)).json() == []
        assert (
            client.post(
                f"/agent/runs/{result['run_id']}/actions", headers=headers(USER)
            ).status_code
            == 503
        )
    for value in ["{}", '{"short":{"owner_id":"x","actor":"x"}}']:
        monkeypatch.setenv("API_TOKENS_JSON", value)
        with pytest.raises(ValueError):
            create_app()


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL required")
def test_actions_approval_delivery_races_and_audit():
    async def scenario():
        await migrate(DSN)
        store = PostgresRunStore(DSN)
        await store.open()
        principal = Principal(owner_id=str(uuid4()), actor="reviewer", role="reviewer")
        stranger = Principal(owner_id="stranger", actor="x", role="reviewer")
        calls = []

        def sink(req):
            calls.append(req)
            return httpx.Response(204)

        service = ActionService(
            store, "https://sandbox.invalid", httpx.MockTransport(sink)
        )

        async def propose():
            result = await DecisionAgent(store).run(
                AgentRequest.model_validate(payload()), principal.owner_id
            )
            return await service.propose(result.run_id, principal)

        try:
            with pytest.raises(ValueError):
                ActionService(store, "http://unsafe")
            with pytest.raises(ActionError):
                await service.propose(uuid4(), principal)
            action = await propose()
            assert await service.propose(action.run_id, principal) == action
            assert await service.get(action.action_id, principal) == action
            for fn in [
                service.get,
                lambda id, who: service.transition(id, who, "pending", "approved"),
            ]:
                with pytest.raises(ActionError):
                    await fn(action.action_id, stranger)
            with pytest.raises(ActionError):
                await service.deliver(action.action_id, principal)
            with pytest.raises(ActionError):
                await service.approve(
                    action.action_id,
                    Approval(digest=action.digest, decision="approve"),
                    principal.model_copy(update={"role": "user"}),
                )
            with pytest.raises(ActionError):
                await service.approve(
                    action.action_id,
                    Approval(digest="0" * 64, decision="approve"),
                    principal,
                )
            approval = Approval(digest=action.digest, decision="approve")
            outcomes = await asyncio.gather(
                *(
                    service.approve(action.action_id, approval, principal)
                    for _ in range(8)
                ),
                return_exceptions=True,
            )
            assert sum(not isinstance(item, Exception) for item in outcomes) == 1
            outcomes = await asyncio.gather(
                *(service.deliver(action.action_id, principal) for _ in range(8)),
                return_exceptions=True,
            )
            assert sum(not isinstance(item, Exception) for item in outcomes) == 1
            assert len(calls) == 1
            assert calls[0].headers["Idempotency-Key"] == str(action.action_id)
            assert (await service.get(action.action_id, principal)).status == "sent"
            async with store.pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT event FROM action_audit WHERE action_id=%s ORDER BY event_id",
                    (action.action_id,),
                )
                assert [r[0] for r in await cursor.fetchall()] == [
                    "proposed",
                    "approved",
                    "dispatching",
                    "sent",
                ]
            expired = await propose()
            async with store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE actions SET expires_at=now()-interval '1 second' WHERE action_id=%s",
                    (expired.action_id,),
                )
            with pytest.raises(ActionError, match="expired"):
                await service.approve(
                    expired.action_id,
                    Approval(digest=expired.digest, decision="approve"),
                    principal,
                )
            rejected = await propose()
            assert (
                await service.approve(
                    rejected.action_id,
                    Approval(digest=rejected.digest, decision="reject"),
                    principal,
                )
            ).status == "rejected"
            for code in [500, None]:
                uncertain = await propose()
                await service.approve(
                    uncertain.action_id,
                    Approval(digest=uncertain.digest, decision="approve"),
                    principal,
                )

                def failure(req):
                    if code is None:
                        raise httpx.ReadTimeout("ambiguous")
                    return httpx.Response(code)

                failed = ActionService(
                    store, "https://sandbox.invalid", httpx.MockTransport(failure)
                )
                assert (
                    await failed.deliver(uncertain.action_id, principal)
                ).status == "unknown"
                with pytest.raises(ActionError):
                    await failed.deliver(uncertain.action_id, principal)
            with pytest.raises(ActionError, match="webhook"):
                await ActionService(store).deliver(uuid4(), principal)
        finally:
            await store.close()

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL required")
def test_action_api_and_document_access(monkeypatch):
    configure(monkeypatch, str(uuid4()))
    monkeypatch.setenv("DATABASE_URL", DSN)
    with TestClient(create_app()) as client:
        run_id = client.post(
            "/agent/run", json=payload(), headers=headers(USER)
        ).json()["run_id"]
        action = client.post(
            f"/agent/runs/{run_id}/actions", headers=headers(USER)
        ).json()
        action_id = action["action_id"]
        assert (
            client.get(f"/actions/{action_id}", headers=headers(USER)).status_code
            == 200
        )
        approval = {"digest": action["digest"], "decision": "approve"}
        assert (
            client.post(
                f"/actions/{action_id}/approval", json=approval, headers=headers(OTHER)
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/actions/{action_id}/approval", json=approval, headers=headers(USER)
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/actions/{action_id}/approval",
                json=approval,
                headers=headers(REVIEWER),
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/actions/{action_id}/deliver", headers=headers(USER)
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/knowledge/documents",
                json={"id": "secret", "text": "alpha private"},
                headers=headers(USER),
            ).status_code
            == 200
        )
        other = client.post(
            "/agent/run",
            json={"objective": "alpha", "context": {"details": "private"}},
            headers=headers(OTHER),
        ).json()
        assert other["retrieved_context"] == []
