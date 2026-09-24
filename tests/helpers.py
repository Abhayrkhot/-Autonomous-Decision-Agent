from app.models import AgentRequest


def make_request(**changes) -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "objective": "alpha beta",
            "context": {"details": "alpha"},
            **changes,
        }
    )
