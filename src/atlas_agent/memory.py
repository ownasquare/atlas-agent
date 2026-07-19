"""Persistent, user-scoped vector memory backed by local SQLite."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
import unicodedata
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Protocol, runtime_checkable

from filelock import FileLock

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
_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_VECTOR_DIMENSIONS = 384
_VECTOR_STRUCT = struct.Struct(f"!{_VECTOR_DIMENSIONS}d")
_BUSY_TIMEOUT_MS = 5_000


def redact_sensitive_text(text: str) -> str:
    """Remove common credential forms before content reaches durable storage."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted.strip()


def _vectorize_text(text: str) -> tuple[float, ...]:
    """Create a stable, network-free feature vector for lightweight local recall."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = [token[:64] for token in _WORD.findall(normalized)[:256]]
    vector = [0.0] * _VECTOR_DIMENSIONS

    def add_feature(feature: str, weight: float) -> None:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") % _VECTOR_DIMENSIONS
        vector[bucket] += weight

    for token in tokens:
        add_feature(f"word:{token}", 3.0)
        padded = f"^{token}$"
        for width in (3, 4):
            for start in range(max(0, len(padded) - width + 1)):
                add_feature(f"char:{padded[start : start + width]}", 0.6)
    for left, right in pairwise(tokens):
        add_feature(f"pair:{left}:{right}", 1.5)

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        vector[0] = 1.0
        return tuple(vector)
    return tuple(value / norm for value in vector)


def _cosine_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    similarity = sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    )
    return 1.0 - max(-1.0, min(1.0, similarity))


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
    """Small local vector index with durable storage and explicit tenant boundaries."""

    def __init__(self, path: Path, *, collection_name: str = "atlas_memories") -> None:
        if not _COLLECTION_NAME.fullmatch(collection_name):
            raise ValueError(
                "collection_name must contain only letters, numbers, underscores, or dashes"
            )
        path.mkdir(parents=True, exist_ok=True)
        self.database_path = path / f"{collection_name}.sqlite3"
        initialization_lock = path / f".{collection_name}.initialize.lock"
        with FileLock(str(initialization_lock), timeout=_BUSY_TIMEOUT_MS / 1_000):
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=_BUSY_TIMEOUT_MS / 1_000)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
                    source_thread TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    vector BLOB NOT NULL CHECK (length(vector) = {_VECTOR_STRUCT.size})
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS memories_user_created
                ON memories (user_id, created_at DESC)
                """
            )

    def add(
        self, *, user_id: str, thread_id: str, candidate: MemoryCandidate
    ) -> MemoryRecord | None:
        content = redact_sensitive_text(candidate.content)
        if not content or content == "[REDACTED]":
            return None
        identity = hashlib.sha256(f"{user_id}\x00{content.casefold()}".encode()).hexdigest()
        now = datetime.now(UTC)
        vector = _VECTOR_STRUCT.pack(*_vectorize_text(content))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (
                    id, user_id, content, category, importance, source_thread, created_at, vector
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_id = excluded.user_id,
                    content = excluded.content,
                    category = excluded.category,
                    importance = excluded.importance,
                    source_thread = excluded.source_thread,
                    created_at = excluded.created_at,
                    vector = excluded.vector
                """,
                (
                    identity,
                    user_id,
                    content,
                    candidate.category,
                    candidate.importance,
                    thread_id,
                    now.isoformat(),
                    vector,
                ),
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
        if limit <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, content, category, importance, source_thread, created_at, vector
                FROM memories
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchall()
        if not rows:
            return []

        query_vector = _vectorize_text(query)
        ranked: list[MemoryRecord] = []
        for row in rows:
            stored_vector = _VECTOR_STRUCT.unpack(row["vector"])
            ranked.append(
                self._record(
                    row,
                    distance=_cosine_distance(query_vector, stored_vector),
                )
            )
        ranked.sort(
            key=lambda record: (
                record.distance if record.distance is not None else math.inf,
                -record.created_at.timestamp(),
                record.id,
            )
        )
        return ranked[:limit]

    def list(self, *, user_id: str, limit: int = 100) -> list[MemoryRecord]:
        if limit <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, content, category, importance, source_thread, created_at
                FROM memories
                WHERE user_id = ?
                ORDER BY created_at DESC, id
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def delete(self, *, user_id: str, memory_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM memories WHERE user_id = ? AND id = ?",
                (user_id, memory_id),
            )
        return cursor.rowcount == 1

    def clear(self, *, user_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
        return max(0, cursor.rowcount)

    @staticmethod
    def _record(row: sqlite3.Row, *, distance: float | None = None) -> MemoryRecord:
        created_at = datetime.fromisoformat(str(row["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return MemoryRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            content=str(row["content"]),
            category=str(row["category"]),
            importance=int(row["importance"]),
            source_thread=str(row["source_thread"]),
            created_at=created_at,
            distance=distance,
        )
