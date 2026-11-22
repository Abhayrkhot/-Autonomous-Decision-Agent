import asyncio
import json
import logging
import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.operations import OperationalMiddleware


def test_limits_redaction_metrics_and_configuration(monkeypatch, caplog):
    monkeypatch.setenv("DEPLOYMENT_MODE", "staging")
    monkeypatch.delenv("API_TOKENS_JSON", raising=False)
    with pytest.raises(ValueError):
        create_app()
    token = "secret" * 6
    monkeypatch.setenv(
        "API_TOKENS_JSON",
        json.dumps({token: {"owner_id": "test", "actor": "test", "role": "reviewer"}}),
    )
    monkeypatch.setenv("MAX_BODY_BYTES", "300")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    caplog.set_level(logging.INFO, logger="agent.requests")
    with TestClient(
        create_app(), headers={"Authorization": f"Bearer {token}"}
    ) as client:
        assert client.get("/ready").status_code == 200
        response = client.post(
            "/agent/run",
            json={"objective": "private-secret", "context": {"details": 5}},
        )
        assert response.status_code == 422
        assert "private-secret" not in response.text
        assert response.headers["x-request-id"]
        assert client.post("/agent/run", content=b"x" * 301).status_code == 413
        metrics = client.get("/metrics").json()
        assert metrics["requests"] == 3
        assert metrics["duration_seconds_total"] > 0
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 429
    assert token not in caplog.text and "private-secret" not in caplog.text
    monkeypatch.delenv("API_TOKENS_JSON")
    monkeypatch.setenv("DEPLOYMENT_MODE", "local")
    with TestClient(create_app()) as client:
        assert client.get("/metrics").status_code == 403
    monkeypatch.setenv("AGENT_MODE", "typo")
    with pytest.raises(ValueError):
        create_app()


def test_asgi_streaming_disconnect_nonhttp_and_bounds(monkeypatch):
    async def scenario():
        called = []

        async def app(scope, receive, send):
            called.append(scope["type"])
            if scope["type"] == "http":
                await receive()
                await receive()
                await send(
                    {"type": "http.response.start", "status": 200, "headers": []}
                )

        state = {"requests": 0, "errors": 0, "duration_seconds_total": 0.0}
        middleware = OperationalMiddleware(app, state, body_limit=4)
        with pytest.raises(ValueError):
            OperationalMiddleware(app, state, rate_limit=0)

        async def receive():
            return {"type": "http.request", "body": b"x", "more_body": False}

        async def send(message):
            pass

        await middleware({"type": "lifespan"}, receive, send)
        assert called == ["lifespan"]
        await middleware({"type": "http", "client": None}, receive, send)

        async def disconnected():
            return {"type": "http.disconnect"}

        await middleware({"type": "http"}, disconnected, send)
        packets = iter(
            [
                {"type": "http.request", "body": b"xxx", "more_body": True},
                {"type": "http.request", "body": b"xxx", "more_body": False},
            ]
        )

        async def streamed():
            return next(packets)

        await middleware({"type": "http"}, streamed, send)

        async def timedout():
            raise TimeoutError()

        await middleware({"type": "http"}, timedout, send)
        for i in range(1030):
            await middleware({"type": "http", "client": (str(i), 0)}, receive, send)
        assert len(middleware.clients) == 1024
        monkeypatch.setattr("app.operations.time.monotonic", lambda: 120000)
        await middleware({"type": "http", "client": ("1", 0)}, receive, send)
        monkeypatch.setattr("app.operations.time.monotonic", lambda: 120061)
        await middleware({"type": "http", "client": ("1", 0)}, receive, send)
        assert middleware.clients["1"][1] == 1

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="real database required")
def test_readiness_database_failure(monkeypatch):
    from app.postgres import PostgresRunStore

    store = PostgresRunStore(os.environ["TEST_DATABASE_URL"], timeout=0.01)
    with TestClient(create_app(store)) as client:
        assert client.get("/ready").status_code == 200

        async def unavailable():
            await store.close()

        client.portal.call(unavailable)
        assert client.get("/ready").status_code == 503
