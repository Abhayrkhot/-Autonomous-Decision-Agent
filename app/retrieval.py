"""Per-request bag-of-words cosine retrieval; no external index."""

import math
import re
from collections import Counter

from app.models import Document, RetrievedDocument

STOP_WORDS = {"a", "an", "the", "to", "and", "or", "is", "for", "of", "in", "with"}


def tokens(text: str) -> list[str]:
    return [
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if word not in STOP_WORDS
    ]


def retrieve(
    query: str, documents: list[Document], limit: int = 3
) -> list[RetrievedDocument]:
    if limit < 0:
        raise ValueError("Retrieval limit cannot be negative")
    query_counts = Counter(tokens(query))
    matches: list[RetrievedDocument] = []
    for document in documents:
        counts = Counter(tokens(document.text))
        dot = sum(value * counts[word] for word, value in query_counts.items())
        norm = math.sqrt(
            sum(v * v for v in query_counts.values())
            * sum(v * v for v in counts.values())
        )
        score = min(1.0, dot / norm) if norm else 0.0
        if score > 0:
            matches.append(
                RetrievedDocument(
                    document_id=document.id,
                    excerpt=document.text[:500],
                    score=round(score, 6),
                )
            )
    return sorted(matches, key=lambda item: (-item.score, item.document_id))[:limit]
