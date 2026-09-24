"""Async storage seam for a future durable implementation."""

from collections import OrderedDict
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from app.models import AgentRequest, AgentResponse, ResponseModel


class RunRecord(ResponseModel):
    owner_id: str = "local"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["completed"] = "completed"
    request: AgentRequest
    response: AgentResponse


class RunStore(Protocol):
    async def save(self, record: RunRecord) -> None: ...
    async def get(self, run_id: UUID, owner_id: str = "local") -> RunRecord | None: ...
    async def list(
        self, owner_id: str = "local", limit: int = 20, offset: int = 0
    ) -> list[RunRecord]: ...


class InMemoryRunStore:
    """Evict the oldest saved record; overwriting refreshes its eviction order."""

    def __init__(self, capacity: int = 1000) -> None:
        if capacity < 1:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self._records: OrderedDict[UUID, RunRecord] = OrderedDict()

    async def save(self, record: RunRecord) -> None:
        run_id = record.response.run_id
        existing = self._records.get(run_id)
        if existing and existing.owner_id != record.owner_id:
            raise ValueError("Run owner cannot change")
        self._records[run_id] = record.model_copy(deep=True)
        self._records.move_to_end(run_id)
        while len(self._records) > self.capacity:
            self._records.popitem(last=False)

    async def get(self, run_id: UUID, owner_id: str = "local") -> RunRecord | None:
        record = self._records.get(run_id)
        return (
            record.model_copy(deep=True)
            if record and record.owner_id == owner_id
            else None
        )

    async def list(
        self, owner_id: str = "local", limit: int = 20, offset: int = 0
    ) -> list[RunRecord]:
        validate_page(limit, offset)
        records = sorted(
            (
                record
                for record in self._records.values()
                if record.owner_id == owner_id
            ),
            key=lambda r: (r.created_at, r.response.run_id),
            reverse=True,
        )
        return [
            record.model_copy(deep=True) for record in records[offset : offset + limit]
        ]


def validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Invalid pagination")
