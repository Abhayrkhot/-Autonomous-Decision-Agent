"""Run locally with uvicorn app.main:app --reload."""

from fastapi import FastAPI, HTTPException

from app.agent import DecisionAgent
from app.guardrails import GuardrailViolation
from app.models import AgentRequest, AgentResponse
from app.storage import InMemoryRunStore, RunStore
from app.tools import ToolRegistry


def create_app(
    store: RunStore | None = None, registry: ToolRegistry | None = None
) -> FastAPI:
    application = FastAPI(title="Autonomous Decision Agent", version="0.1.0")
    agent = DecisionAgent(
        store if store is not None else InMemoryRunStore(), registry=registry
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/agent/run", response_model=AgentResponse)
    async def run_agent(request: AgentRequest) -> AgentResponse:
        try:
            return await agent.run(request)
        except GuardrailViolation as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return application


app = create_app()
