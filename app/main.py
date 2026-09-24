"""Run locally with uvicorn app.main:app --reload."""

import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query

from app.agent import DecisionAgent
from app.guardrails import GuardrailViolation
from app.models import AgentRequest, AgentResponse
from app.postgres import PostgresRunStore
from app.provider import DecisionProvider, OpenAIProvider, ProviderError
from app.storage import InMemoryRunStore, RunRecord, RunStore


def create_app(
    store: RunStore | None = None, provider: DecisionProvider | None = None
) -> FastAPI:
    selected = (
        store
        if store is not None
        else (
            PostgresRunStore(os.environ["DATABASE_URL"])
            if os.getenv("DATABASE_URL")
            else InMemoryRunStore()
        )
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if isinstance(selected, PostgresRunStore):
            await selected.open()
        try:
            yield
        finally:
            if isinstance(selected, PostgresRunStore):
                await selected.close()

    application = FastAPI(
        title="Autonomous Decision Agent", version="0.2.0", lifespan=lifespan
    )
    configured_provider = provider
    if (
        os.getenv("AGENT_MODE", "deterministic") == "openai"
        and configured_provider is None
    ):
        configured_provider = OpenAIProvider(
            os.environ.get("OPENAI_API_KEY", ""), os.environ.get("OPENAI_MODEL", "")
        )
    agent = DecisionAgent(selected, provider=configured_provider)

    @application.get("/agent/runs", response_model=list[RunRecord])
    async def list_runs(
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        return await selected.list(limit=limit, offset=offset)

    @application.get("/agent/runs/{run_id}", response_model=RunRecord)
    async def get_run(run_id: UUID):
        record = await selected.get(run_id)
        if record is None:
            raise HTTPException(404, "Run not found")
        return record

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/agent/run", response_model=AgentResponse)
    async def run_agent(request: AgentRequest) -> AgentResponse:
        try:
            return await agent.run(request)
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        except GuardrailViolation as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return application


app = create_app()
