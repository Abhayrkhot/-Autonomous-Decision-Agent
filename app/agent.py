"""Small orchestrator: validate, plan, retrieve, execute, evaluate, persist."""

from uuid import uuid4

from app.evaluator import evaluate
from app.guardrails import validate_request
from app.models import AgentRequest, AgentResponse, ToolResult
from app.planner import make_plan
from app.provider import DecisionProvider
from app.retrieval import retrieve
from app.storage import RunRecord, RunStore
from app.tools import ToolInput, ToolRegistry, default_registry


class DecisionAgent:
    def __init__(
        self,
        store: RunStore,
        registry: ToolRegistry | None = None,
        provider: DecisionProvider | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.registry = registry or default_registry()

    async def run(self, request: AgentRequest) -> AgentResponse:
        validate_request(request)
        plan, action = make_plan(request)
        retrieved = retrieve(
            f"{request.objective} {request.context.details}", request.documents
        )
        metadata = None
        citations = [item.document_id for item in retrieved[:1]]
        if self.provider:
            generated, metadata = await self.provider.generate(request, retrieved)
            plan, action = generated.plan, generated.action
            citations = generated.citations
            draft = ToolResult(tool="model_draft", output=generated.message)
        else:
            draft = await self.registry.execute(
                "draft_message", ToolInput(request, action, retrieved)
            )
        data = ToolInput(request, action, retrieved)
        follow_up = await self.registry.execute("create_follow_up", data)
        response = AgentResponse(
            run_id=uuid4(),
            interpreted_objective=request.objective,
            plan=plan,
            recommended_action=action,
            message=draft.output,
            retrieved_context=retrieved,
            tool_results=[draft, follow_up],
            generation=metadata.model_dump() if metadata else None,
            citations=citations,
            evaluation=evaluate(request.objective, draft.output, retrieved),
        )
        await self.store.save(RunRecord(request=request, response=response))
        return response
