"""Run locally with uvicorn app.main:app --reload."""

import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from app.actions import Action, ActionError, ActionService, Approval
from app.agent import DecisionAgent
from app.auth import Principal, auth_dependency
from app.guardrails import GuardrailViolation
from app.jobs import JobError, JobService, JobStatus, QueueFull
from app.knowledge import PostgresKnowledge
from app.models import AgentRequest, AgentResponse, Document
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

    jobs = (
        JobService(selected, os.getenv("REDIS_URL"))
        if isinstance(selected, PostgresRunStore)
        else None
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if isinstance(selected, PostgresRunStore):
            await selected.open()
        try:
            yield
        finally:
            if jobs:
                await jobs.close()
            if isinstance(selected, PostgresRunStore):
                await selected.close()

    application = FastAPI(
        title="Autonomous Decision Agent", version="0.2.0", lifespan=lifespan
    )
    authenticate = auth_dependency()
    configured_provider = provider
    if (
        os.getenv("AGENT_MODE", "deterministic") == "openai"
        and configured_provider is None
    ):
        configured_provider = OpenAIProvider(
            os.environ.get("OPENAI_API_KEY", ""), os.environ.get("OPENAI_MODEL", "")
        )
    knowledge = (
        PostgresKnowledge(selected) if isinstance(selected, PostgresRunStore) else None
    )
    agent = DecisionAgent(selected, provider=configured_provider, knowledge=knowledge)

    @application.post("/knowledge/documents")
    async def ingest_document(
        document: Document, principal: Principal = Depends(authenticate)
    ):
        if knowledge is None:
            raise HTTPException(503, "Persistent knowledge requires DATABASE_URL")
        try:
            count = await knowledge.ingest(principal.owner_id, document)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"document_id": document.id, "chunks": count}

    @application.get("/agent/runs", response_model=list[RunRecord])
    async def list_runs(
        principal: Principal = Depends(authenticate),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return await selected.list(
            owner_id=principal.owner_id, limit=limit, offset=offset
        )

    @application.get("/agent/runs/{run_id}", response_model=RunRecord)
    async def get_run(run_id: UUID, principal: Principal = Depends(authenticate)):
        record = await selected.get(run_id, principal.owner_id)
        if record is None:
            raise HTTPException(404, "Run not found")
        return record

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/agent/run", response_model=AgentResponse)
    async def run_agent(
        request: AgentRequest, principal: Principal = Depends(authenticate)
    ) -> AgentResponse:
        try:
            return await agent.run(request, owner_id=principal.owner_id)
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        except GuardrailViolation as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    actions = (
        ActionService(selected, os.getenv("ACTION_WEBHOOK_URL"))
        if isinstance(selected, PostgresRunStore)
        else None
    )

    def action_service() -> ActionService:
        if actions is None:
            raise HTTPException(503, "Actions require PostgreSQL")
        return actions

    @application.exception_handler(ActionError)
    async def action_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.post("/agent/runs/{run_id}/actions", response_model=Action)
    async def propose_action(
        run_id: UUID, principal: Principal = Depends(authenticate)
    ):
        return await action_service().propose(run_id, principal)

    @application.get("/actions/{action_id}", response_model=Action)
    async def get_action(action_id: UUID, principal: Principal = Depends(authenticate)):
        return await action_service().get(action_id, principal)

    @application.post("/actions/{action_id}/approval", response_model=Action)
    async def approve_action(
        action_id: UUID,
        approval: Approval,
        principal: Principal = Depends(authenticate),
    ):
        return await action_service().approve(action_id, approval, principal)

    @application.post("/actions/{action_id}/deliver", response_model=Action)
    async def deliver_action(
        action_id: UUID, principal: Principal = Depends(authenticate)
    ):
        return await action_service().deliver(action_id, principal)

    def job_service() -> JobService:
        if jobs is None:
            raise HTTPException(503, "Jobs require PostgreSQL")
        return jobs

    @application.exception_handler(JobError)
    async def job_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=429 if isinstance(exc, QueueFull) else 409,
            content={"detail": str(exc)},
        )

    @application.post("/agent/jobs", response_model=JobStatus, status_code=202)
    async def submit_job(
        request: AgentRequest,
        principal: Principal = Depends(authenticate),
        idempotency_key: str = Header(min_length=1, max_length=200),
    ):
        try:
            return await job_service().submit(
                request, principal.owner_id, idempotency_key
            )
        except GuardrailViolation as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.get("/agent/jobs/{job_id}", response_model=JobStatus)
    async def get_job(job_id: UUID, principal: Principal = Depends(authenticate)):
        return await job_service().get(job_id, principal.owner_id)

    @application.post("/agent/jobs/{job_id}/cancel", response_model=JobStatus)
    async def cancel_job(job_id: UUID, principal: Principal = Depends(authenticate)):
        return await job_service().cancel(job_id, principal.owner_id)

    return application


app = create_app()
