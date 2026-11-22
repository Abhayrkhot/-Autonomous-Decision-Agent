"""Run locally with uvicorn app.main:app --reload."""

import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import Error
from psycopg_pool import PoolClosed, PoolTimeout

from app.actions import Action, ActionError, ActionService, Approval
from app.agent import DecisionAgent
from app.auth import Principal, auth_dependency
from app.errors import http_status
from app.feedback import FeedbackError, FeedbackService, HumanReview, Outcome
from app.guardrails import GuardrailViolation
from app.jobs import JobError, JobService, JobStatus, QueueFull
from app.knowledge import PostgresKnowledge
from app.models import AgentRequest, AgentResponse, Document
from app.operations import OperationalMiddleware
from app.postgres import PostgresRunStore
from app.provider import DecisionProvider, ProviderError, provider_from_env
from app.storage import InMemoryRunStore, RunRecord, RunStore
from app.tools import ToolRegistry


def create_app(
    store: RunStore | None = None,
    registry: ToolRegistry | None = None,
    provider: DecisionProvider | None = None,
) -> FastAPI:
    if os.getenv("DEPLOYMENT_MODE", "local") == "staging" and not os.getenv(
        "API_TOKENS_JSON"
    ):
        raise ValueError("Staging requires configured authentication")
    configured_provider = provider if provider is not None else provider_from_env()
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
    metrics = {"requests": 0, "errors": 0, "duration_seconds_total": 0.0}
    application.add_middleware(
        OperationalMiddleware,
        state=metrics,
        body_limit=int(os.getenv("MAX_BODY_BYTES", "262144")),
        rate_limit=int(os.getenv("RATE_LIMIT_PER_MINUTE", "1000")),
    )
    authenticate = auth_dependency()

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(error["loc"]), "type": error["type"]}
                    for error in exc.errors()
                ]
            },
        )

    @application.get("/ready")
    async def readiness():
        if isinstance(selected, PostgresRunStore):
            try:
                async with selected.pool.connection() as conn:
                    await conn.execute("SELECT 1 FROM schema_migrations LIMIT 1")
            except (Error, PoolTimeout, PoolClosed):
                raise HTTPException(503, "Database unavailable") from None
        return {"status": "ready"}

    @application.get("/metrics")
    async def operational_metrics(principal: Principal = Depends(authenticate)):
        if principal.role != "reviewer":
            raise HTTPException(403, "Reviewer role required")
        return dict(metrics)

    knowledge = (
        PostgresKnowledge(selected) if isinstance(selected, PostgresRunStore) else None
    )
    agent = DecisionAgent(
        selected,
        registry=registry,
        provider=configured_provider,
        knowledge=knowledge,
    )

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

    async def domain_error(request, exc):
        # Not found 404, wrong role 403, invalid 422, full queue 429, conflicts 409.
        default = 429 if isinstance(exc, QueueFull) else 409
        return JSONResponse(
            status_code=http_status(exc, default), content={"detail": str(exc)}
        )

    for error in (ActionError, JobError, FeedbackError):
        application.add_exception_handler(error, domain_error)

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

    feedback = (
        FeedbackService(selected) if isinstance(selected, PostgresRunStore) else None
    )

    def feedback_service() -> FeedbackService:
        if feedback is None:
            raise HTTPException(503, "Feedback requires PostgreSQL")
        return feedback

    @application.post("/agent/runs/{run_id}/reviews", response_model=HumanReview)
    async def review_run(
        run_id: UUID, review: HumanReview, principal: Principal = Depends(authenticate)
    ):
        return await feedback_service().review(run_id, review, principal)

    @application.post("/agent/runs/{run_id}/outcomes", response_model=Outcome)
    async def record_outcome(
        run_id: UUID, outcome: Outcome, principal: Principal = Depends(authenticate)
    ):
        return await feedback_service().outcome(run_id, outcome, principal)

    @application.get("/agent/runs/{run_id}/evaluation")
    async def run_evaluation(
        run_id: UUID, principal: Principal = Depends(authenticate)
    ):
        return await feedback_service().report(run_id, principal)

    return application


app = create_app()
