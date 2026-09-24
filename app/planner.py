"""A fixed workflow with a small, explicit outcome-based decision rule."""

from app.models import AgentRequest


def make_plan(request: AgentRequest) -> tuple[list[str], str]:
    needs_help = (
        request.outcome_signals.engagement == "low"
        or request.outcome_signals.last_action_success is False
    )
    action = (
        "Offer assistance and ask one clarifying question"
        if needs_help
        else "Suggest a focused next step"
    )
    return [
        "Interpret the stated objective",
        "Retrieve relevant documents using objective and context",
        "Choose an action using supplied outcome signals",
        "Draft a personalized message and create a local follow-up task",
        "Evaluate the result and store the completed run",
    ], action
