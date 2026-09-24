"""Persistent chunk retrieval with deterministic local lexical hash embeddings.

These embeddings are not semantic model embeddings. The bounded exact scan is
intended for a small corpus and can later be replaced behind this interface.
"""

import hashlib
import math
from typing import Protocol

from app.guardrails import validate_request
from app.models import AgentRequest, Document, RetrievedDocument
from app.postgres import PostgresRunStore
from app.retrieval import tokens

DIMENSIONS = 256
MAX_CHUNKS = 1000


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for token in tokens(text):
        index = (
            int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
            % DIMENSIONS
        )
        vector[index] += 1
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def chunks(text: str, size: int = 500, overlap: int = 80) -> list[tuple[int, int, str]]:
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("Invalid chunk settings")
    result = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        result.append((start, end, text[start:end]))
        if end == len(text):
            break
        start = end - overlap
    return result


class KnowledgeStore(Protocol):
    async def ingest(self, owner_id: str, document: Document) -> int: ...
    async def search(self, owner_id: str, query: str) -> list[RetrievedDocument]: ...


class PostgresKnowledge:
    def __init__(self, store: PostgresRunStore):
        self.store = store

    async def ingest(self, owner_id: str, document: Document) -> int:
        validate_request(
            AgentRequest(
                objective="ingest",
                context={"details": "knowledge"},
                documents=[document],
            )
        )
        parts = chunks(document.text)
        async with self.store.pool.connection() as connection:
            # Serialize corpus-size checks per owner, including concurrent ingestion.
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (owner_id,)
            )
            cursor = await connection.execute(
                "SELECT count(*) FROM knowledge_chunks WHERE owner_id=%s AND source_id<>%s",
                (owner_id, document.id),
            )
            count = (await cursor.fetchone())[0]
            if count + len(parts) > MAX_CHUNKS:
                raise ValueError("Knowledge corpus exceeds 1000 chunks")
            await connection.execute(
                "DELETE FROM knowledge_chunks WHERE owner_id=%s AND source_id=%s",
                (owner_id, document.id),
            )
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO knowledge_chunks VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (owner_id, document.id, i, start, end, text, embed(text))
                        for i, (start, end, text) in enumerate(parts)
                    ],
                )
        return len(parts)

    async def search(self, owner_id: str, query: str) -> list[RetrievedDocument]:
        vector = embed(query)
        async with self.store.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT source_id, chunk_number, start_offset, end_offset, text, embedding FROM knowledge_chunks WHERE owner_id=%s ORDER BY source_id, chunk_number LIMIT %s",
                (owner_id, MAX_CHUNKS),
            )
            rows = await cursor.fetchall()
        matches = []
        query_tokens = set(tokens(query))
        for source_id, number, start, end, text, embedding in rows:
            # Lexical intersection prevents hash-only collisions becoming evidence.
            score = min(1.0, sum(a * b for a, b in zip(vector, embedding, strict=True)))
            if score > 0 and query_tokens & set(tokens(text)):
                matches.append(
                    RetrievedDocument(
                        document_id=f"{source_id}#{number}",
                        source_id=source_id,
                        start_offset=start,
                        end_offset=end,
                        excerpt=text,
                        score=round(score, 6),
                    )
                )
        return sorted(matches, key=lambda item: (-item.score, item.document_id))[:3]
