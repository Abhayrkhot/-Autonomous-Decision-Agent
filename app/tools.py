"""Allowlisted, typed tools. No network, shell execution, or message sending."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.models import AgentRequest, RetrievedDocument, ToolResult


@dataclass(frozen=True)
class ToolInput:
    request: AgentRequest
    action: str
    retrieved: list[RetrievedDocument]


Tool = Callable[[ToolInput], Awaitable[str]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, tool: Tool) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    async def execute(self, name: str, data: ToolInput) -> ToolResult:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return ToolResult(tool=name, output=await self._tools[name](data))


async def draft_message(data: ToolInput) -> str:
    request = data.request
    message = f"Hi {request.context.name}, your goal is: {request.objective}. "
    message += f"Given your context ({request.context.details[:300]}), I suggest: {data.action.lower()}."
    if data.retrieved:
        source = data.retrieved[0]
        message += (
            f" Supporting reference [{source.document_id}]: {source.excerpt[:300]}"
        )
    else:
        message += " No relevant supporting document was found."
    message += " What would help you take the next step?"
    return message


async def create_follow_up(data: ToolInput) -> str:
    # This task description becomes a local record when the run is saved.
    return f"Pending human review: {data.action} for {data.request.context.name}."


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("draft_message", draft_message)
    registry.register("create_follow_up", create_follow_up)
    return registry
