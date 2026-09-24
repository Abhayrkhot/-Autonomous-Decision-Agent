"""Repeatable structural metrics, not a quality or business-impact estimate."""

from app.models import Evaluation, RetrievedDocument
from app.retrieval import tokens


def evaluate(
    objective: str, message: str, retrieved: list[RetrievedDocument]
) -> Evaluation:
    expected = set(tokens(objective))
    coverage = len(expected & set(tokens(message))) / len(expected) if expected else 0.0
    return Evaluation(
        objective_token_coverage=round(coverage, 6),
        retrieved_document_count=len(retrieved),
        has_message=bool(message.strip()),
        within_message_limit=len(message) <= 4000,
    )
