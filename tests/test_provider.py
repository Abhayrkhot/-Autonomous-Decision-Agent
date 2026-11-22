import asyncio
import json
import os

import httpx
import pytest

from app.agent import DecisionAgent
from app.main import create_app
from app.models import AgentRequest
from app.provider import (
    DEFAULT_BASE_URL,
    RESPONSE_SCHEMA,
    GeneratedDecision,
    OpenAIProvider,
    ProviderError,
    provider_from_env,
)
from app.storage import InMemoryRunStore


def sample():
    return AgentRequest(objective="help", context={"details": "help"})


def body(decision=None):
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            decision
                            or {
                                "plan": ["Review", "Draft"],
                                "action": "Offer help",
                                "message": "What help do you need?",
                                "citations": [],
                            }
                        ),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def test_provider_payload_and_integration():
    async def scenario():
        def handler(req):
            payload = json.loads(req.content)
            assert payload["store"] is False
            assert payload["text"]["format"]["strict"] is True
            assert "tools" not in payload
            assert "untrusted" in payload["instructions"]
            return httpx.Response(200, json=body())

        provider = OpenAIProvider("fake", "test-model", httpx.MockTransport(handler))
        result = await DecisionAgent(InMemoryRunStore(), provider=provider).run(
            sample()
        )
        assert result.message == "What help do you need?"
        assert result.generation["input_tokens"] == 10
        assert result.generation["cost_usd"] is None
        assert result.tool_results[0].tool == "model_draft"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,expected_calls", [(429, 3), (500, 3), (401, 1), (400, 1)]
)
def test_bounded_retry_and_errors(status, expected_calls):
    async def scenario():
        calls = []

        def handler(req):
            calls.append(req)
            return httpx.Response(status, text="secret provider error")

        provider = OpenAIProvider("fake", "test", httpx.MockTransport(handler))
        with pytest.raises(ProviderError) as caught:
            await provider.generate(sample(), [])
        assert "secret" not in str(caught.value)
        assert len(calls) == expected_calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"status": "incomplete"},
        {"status": "completed", "output": []},
        body({"plan": [], "action": "x", "message": "x", "citations": []}),
        body({"plan": ["x"], "action": "x", "message": "x", "citations": ["invented"]}),
        body({"plan": ["x"], "action": "x", "message": "x" * 4001, "citations": []}),
        body(
            {
                "plan": ["x"],
                "action": "x",
                "message": "x",
                "citations": [],
                "tool": "shell",
            }
        ),
    ],
)
def test_invalid_outputs(invalid):
    async def scenario():
        provider = OpenAIProvider(
            "fake",
            "test",
            httpx.MockTransport(lambda req: httpx.Response(200, json=invalid)),
        )
        with pytest.raises(ProviderError, match="invalid"):
            await provider.generate(sample(), [])

    asyncio.run(scenario())


def test_transport_timeout_cancellation_and_recovery():
    async def scenario():
        calls = 0

        async def handler(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json=body())

        provider = OpenAIProvider("fake", "test", httpx.MockTransport(handler))
        assert (await provider.generate(sample(), []))[0].message
        assert calls == 2

        async def slow(req):
            await asyncio.sleep(10)

        provider = OpenAIProvider(
            "fake", "test", httpx.MockTransport(slow), deadline=0.01
        )
        with pytest.raises(ProviderError, match="unavailable"):
            await provider.generate(sample(), [])
        task = asyncio.create_task(provider.generate(sample(), []))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_configuration_and_api_error(monkeypatch):
    for key, model, attempts in [
        ("", "x", 1),
        ("x", "", 1),
        ("x", "x", 4),
        ("x", "x", True),
        ("x", "x", 2.0),
    ]:
        with pytest.raises(ValueError):
            OpenAIProvider(key, model, attempts=attempts)
    for base_url in ["ftp://models.example", "https://", "models.example/v1"]:
        with pytest.raises(ValueError, match="base URL"):
            OpenAIProvider("x", "x", base_url=base_url)
    monkeypatch.setenv("AGENT_MODE", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        create_app()

    async def scenario():
        provider = OpenAIProvider(
            "fake", "test", httpx.MockTransport(lambda req: httpx.Response(401))
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(provider=provider)),
            base_url="http://test",
        ) as client:
            assert (
                await client.post("/agent/run", json=sample().model_dump())
            ).status_code == 502

    asyncio.run(scenario())


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_PROVIDER_TEST") != "1",
    reason="explicit paid smoke opt-in required",
)
def test_live_provider():
    async def scenario():
        provider = OpenAIProvider(
            os.environ["OPENAI_API_KEY"], os.environ["OPENAI_MODEL"], attempts=1
        )
        decision, metadata = await provider.generate(sample(), [])
        assert decision.plan and metadata.output_tokens > 0

    asyncio.run(scenario())


def test_configured_endpoint_and_environment(monkeypatch):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=body())

    async def scenario():
        provider = OpenAIProvider(
            "fake",
            "local-model",
            httpx.MockTransport(handler),
            base_url="http://model-stub:8080/v1/",
        )
        await provider.generate(sample(), [])

    asyncio.run(scenario())
    assert seen == ["http://model-stub:8080/v1/responses"]

    monkeypatch.delenv("AGENT_MODE", raising=False)
    assert provider_from_env() is None
    monkeypatch.setenv("AGENT_MODE", "typo")
    with pytest.raises(ValueError, match="AGENT_MODE"):
        provider_from_env()
    monkeypatch.setenv("AGENT_MODE", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("OPENAI_MODEL", "test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert provider_from_env().url == DEFAULT_BASE_URL + "/responses"
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    assert provider_from_env().url == DEFAULT_BASE_URL + "/responses"
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:9000/v1")
    assert provider_from_env().url == "http://localhost:9000/v1/responses"


def test_wire_schema_stays_within_strict_structured_output_subset():
    allowed = {"type", "properties", "required", "additionalProperties", "items"}

    def keywords(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                if key == "properties":
                    for child in value.values():
                        yield from keywords(child)
                else:
                    yield from keywords(value)

    assert set(keywords(RESPONSE_SCHEMA)) <= allowed
    assert set(RESPONSE_SCHEMA["required"]) == set(GeneratedDecision.model_fields)
    assert set(RESPONSE_SCHEMA["properties"]) == set(GeneratedDecision.model_fields)
