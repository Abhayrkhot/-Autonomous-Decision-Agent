"""Validated public contracts; unknown fields are rejected."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
DocumentText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Context(Model):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = "customer"
    details: ShortText


class Document(Model):
    id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    text: DocumentText


class OutcomeSignals(Model):
    # Observations influence the recommendation; they are not predicted outcomes.
    engagement: Literal["low", "medium", "high"] = "medium"
    last_action_success: bool | None = None


class AgentRequest(Model):
    objective: ShortText
    context: Context
    documents: list[Document] = Field(default_factory=list, max_length=20)
    outcome_signals: OutcomeSignals = Field(default_factory=OutcomeSignals)


class RetrievedDocument(Model):
    document_id: str
    excerpt: str
    score: float = Field(ge=0, le=1)


class ToolResult(Model):
    tool: str
    output: str


class Evaluation(Model):
    objective_token_coverage: float = Field(ge=0, le=1)
    retrieved_document_count: int = Field(ge=0)
    has_message: bool
    within_message_limit: bool


class AgentResponse(Model):
    run_id: UUID
    interpreted_objective: str
    plan: list[str]
    recommended_action: str
    message: str
    retrieved_context: list[RetrievedDocument]
    tool_results: list[ToolResult]
    evaluation: Evaluation
