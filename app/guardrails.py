"""Conservative input heuristics, not a complete security boundary."""

import re

from app.models import AgentRequest

MAX_INPUT_CHARS = 30000
PATTERNS = (
    r"ignore\b.{0,40}\b(previous|prior|all|system)\b.{0,25}\b(instructions?|prompts?|rules?)",
    r"(reveal|show|print|leak)\b.{0,40}\b(system prompt|secrets?|api keys?|passwords?)",
    r"(override|bypass)\b.{0,30}\b(safety|guardrails?|instructions?|rules?)",
    r"you are now\b",
    r"<\|?(system|assistant)\b",
)


class GuardrailViolation(ValueError):
    pass


def validate_request(request: AgentRequest) -> None:
    fields = [request.objective, request.context.name, request.context.details]
    fields += [value for doc in request.documents for value in (doc.id, doc.text)]
    if sum(map(len, fields)) > MAX_INPUT_CHARS:
        raise GuardrailViolation("Combined text exceeds 30000 characters")
    if len({doc.id for doc in request.documents}) != len(request.documents):
        raise GuardrailViolation("Document IDs must be unique")
    for field in fields:
        normalized = " ".join(field.casefold().split())
        if any(re.search(pattern, normalized) for pattern in PATTERNS):
            raise GuardrailViolation("Potential prompt-injection instruction detected")
