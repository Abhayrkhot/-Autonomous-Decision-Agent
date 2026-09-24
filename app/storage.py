"""Async storage seam for a future durable implementation."""

from collections import OrderedDict
from typing import Protocol
from uuid import UUID

from app.models import AgentRequest, AgentResponse, Model


class RunRecord(Model):
    request: AgentRequest
    response: AgentResponse


class RunStore(Protocol):
    async def save(self, record: RunRecord) -> None: ...
    async def get(self, run_id: UUID) -> RunRecord | None: ...


class InMemoryRunStore:
    def __init__(self, capacity: int = 1000) -> None:
        if capacity < 1:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self._records: OrderedDict[UUID, RunRecord] = OrderedDict()

    async def save(self, record: RunRecord) -> None:
        run_id = record.response.run_id
        self._records[run_id] = record.model_copy(deep=True)
        self._records.move_to_end(run_id)
        while len(self._records) > self.capacity:
            self._records.popitem(last=False)

    async def get(self, run_id: UUID) -> RunRecord | None:
        record = self._records.get(run_id)
        return record.model_copy(deep=True) if record else None
