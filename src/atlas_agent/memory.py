"""Persistent, user-scoped semantic memory backed by Chroma."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction
from chromadb.config import Settings as ChromaSettings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from atlas_agent.schemas import MemoryCandidate, MemoryRecord

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\b"
        r"\s*[:=]\s*['\"]?[^\s,'\"]{6,}"
    ),
)


def redact_sensitive_text(text: str) -> str:
    """Remove common credential forms before content reaches durable storage."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted.strip()


@runtime_checkable
class MemoryStore(Protocol):
    """Storage behavior required by the graph, runtime, API, and alternate backends."""

    def add(
        self,
        *,
        user_id: str,
        thread_id: str,
        candidate: MemoryCandidate,
    ) -> MemoryRecord | None: ...

    def search(self, *, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]: ...

    def list(self, *, user_id: str, limit: int = 100) -> list[MemoryRecord]: ...

    def delete(self, *, user_id: str, memory_id: str) -> bool: ...

    def clear(self, *, user_id: str) -> int: ...


class VectorMemory:
    """Long-term memory with semantic retrieval and explicit tenant boundaries."""

    def __init__(
        self,
        path: Path,
        *,
        collection_name: str = "atlas_memories",
        embedding_function: EmbeddingFunction[Documents] | None = None,
    ) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=cast(Any, embedding_function or DefaultEmbeddingFunction()),
            metadata={"hnsw:space": "cosine", "description": "Atlas user memories"},
        )

    def add(
        self, *, user_id: str, thread_id: str, candidate: MemoryCandidate
    ) -> MemoryRecord | None:
        content = redact_sensitive_text(candidate.content)
        if not content or content == "[REDACTED]":
            return None
        identity = hashlib.sha256(f"{user_id}\x00{content.casefold()}".encode()).hexdigest()
        now = datetime.now(UTC)
        metadata: dict[str, str | int | float | bool] = {
            "user_id": user_id,
            "category": candidate.category,
            "importance": candidate.importance,
            "source_thread": thread_id,
            "created_at": now.isoformat(),
        }
        self._collection.upsert(
            ids=[identity],
            documents=[content],
            metadatas=[metadata],
        )
        return MemoryRecord(
            id=identity,
            user_id=user_id,
            content=content,
            category=candidate.category,
            importance=candidate.importance,
            source_thread=thread_id,
            created_at=now,
        )

    def search(self, *, user_id: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        if self._collection.count() == 0:
            return []
        response = self._collection.query(
            query_texts=[query],
            n_results=min(limit, self._collection.count()),
            where={"user_id": user_id},
            include=["documents", "metadatas", "distances"],
        )
        id_groups = response.get("ids") or [[]]
        document_groups = response.get("documents") or [[]]
        metadata_groups = response.get("metadatas") or [[]]
        distance_groups = response.get("distances") or [[]]
        ids = id_groups[0]
        documents = document_groups[0]
        metadatas = metadata_groups[0]
        distances = distance_groups[0]
        records: list[MemoryRecord] = []
        for index, memory_id in enumerate(ids):
            metadata = metadatas[index] or {}
            records.append(
                self._record(
                    memory_id=memory_id,
                    content=documents[index] or "",
                    metadata=metadata,
                    distance=distances[index] if index < len(distances) else None,
                )
            )
        return records

    def list(self, *, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        response = self._collection.get(
            where={"user_id": user_id},
            limit=limit,
            include=["documents", "metadatas"],
        )
        ids = response.get("ids", [])
        documents = response.get("documents", []) or []
        metadatas = response.get("metadatas", []) or []
        records = [
            self._record(
                memory_id=memory_id,
                content=documents[index] or "",
                metadata=metadatas[index] or {},
            )
            for index, memory_id in enumerate(ids)
        ]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        existing = self._collection.get(ids=[memory_id], where={"user_id": user_id})
        if not existing.get("ids"):
            return False
        self._collection.delete(ids=[memory_id], where={"user_id": user_id})
        return True

    def clear(self, *, user_id: str) -> int:
        existing = self._collection.get(where={"user_id": user_id})
        ids = existing.get("ids", [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    @staticmethod
    def _record(
        *,
        memory_id: str,
        content: str,
        metadata: Mapping[str, Any],
        distance: float | None = None,
    ) -> MemoryRecord:
        created = metadata.get("created_at", datetime.now(UTC).isoformat())
        return MemoryRecord(
            id=memory_id,
            user_id=str(metadata.get("user_id", "")),
            content=content,
            category=str(metadata.get("category", "fact")),
            importance=int(metadata.get("importance", 3)),
            source_thread=str(metadata.get("source_thread", "unknown")),
            created_at=datetime.fromisoformat(str(created)),
            distance=distance,
        )
