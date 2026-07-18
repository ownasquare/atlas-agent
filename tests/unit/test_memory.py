import hashlib
from pathlib import Path
from typing import Any

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from atlas_agent.memory import MemoryStore, VectorMemory, redact_sensitive_text
from atlas_agent.schemas import MemoryCandidate


class DeterministicEmbedding(EmbeddingFunction[Documents]):
    """Small deterministic embedding with useful keyword neighborhoods."""

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        embeddings: list[list[float]] = []
        for text in input:
            vector = [0.0] * 12
            for token in text.casefold().split():
                index = int(hashlib.sha256(token.encode()).hexdigest(), 16) % len(vector)
                vector[index] += 1.0
            if not any(vector):
                vector[0] = 1.0
            embeddings.append(vector)
        return embeddings

    @staticmethod
    def name() -> str:
        return "atlas-test-embedding"

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "DeterministicEmbedding":
        return DeterministicEmbedding()

    def get_config(self) -> dict[str, Any]:
        return {}


def memory(path: Path) -> VectorMemory:
    return VectorMemory(
        path,
        collection_name="test_memories",
        embedding_function=DeterministicEmbedding(),
    )


def test_vector_memory_implements_the_public_memory_store_protocol(tmp_path: Path) -> None:
    assert isinstance(memory(tmp_path / "vectors"), MemoryStore)


def candidate(content: str, category: str = "preference") -> MemoryCandidate:
    return MemoryCandidate(content=content, category=category, importance=4)


def test_memory_persists_across_service_restart(tmp_path: Path) -> None:
    first = memory(tmp_path / "vectors")
    stored = first.add(
        user_id="alice",
        thread_id="thread-1",
        candidate=candidate("Python reports should use concise tables"),
    )
    assert stored is not None

    reopened = memory(tmp_path / "vectors")
    records = reopened.list(user_id="alice")

    assert [record.content for record in records] == ["Python reports should use concise tables"]
    assert records[0].source_thread == "thread-1"


def test_memory_search_and_list_are_user_scoped(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")
    store.add(user_id="alice", thread_id="a", candidate=candidate("prefers Python reports"))
    store.add(user_id="bob", thread_id="b", candidate=candidate("prefers Java reports"))

    alice = store.search(user_id="alice", query="Python report", limit=5)

    assert alice
    assert {record.user_id for record in alice} == {"alice"}
    assert store.list(user_id="bob")[0].content == "prefers Java reports"


def test_duplicate_content_is_upserted_not_appended(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")
    fact = candidate("Use Canadian dollars", category="constraint")
    store.add(user_id="alice", thread_id="first", candidate=fact)
    store.add(user_id="alice", thread_id="second", candidate=fact)

    records = store.list(user_id="alice")

    assert len(records) == 1
    assert records[0].source_thread == "second"


def test_secrets_are_redacted_before_persistence(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")
    record = store.add(
        user_id="alice",
        thread_id="thread",
        candidate=candidate(
            "The api_key=" + "fixture-value must never be logged", category="constraint"
        ),
    )

    assert record is not None
    assert "fixture-value" not in record.content
    assert "[REDACTED]" in record.content
    assert "fixture-value" not in store.list(user_id="alice")[0].content


def test_fully_sensitive_memory_is_not_stored(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")

    record = store.add(
        user_id="alice",
        thread_id="thread",
        candidate=candidate("sk-" + "fixture-token-value"),
    )

    assert record is None
    assert store.list(user_id="alice") == []


def test_delete_and_clear_cannot_cross_users(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")
    alice = store.add(user_id="alice", thread_id="a", candidate=candidate("Alice memory"))
    bob = store.add(user_id="bob", thread_id="b", candidate=candidate("Bob memory"))
    assert alice is not None and bob is not None

    assert store.delete(user_id="bob", memory_id=alice.id) is False
    assert store.delete(user_id="alice", memory_id=alice.id) is True
    assert store.clear(user_id="bob") == 1
    assert store.list(user_id="alice") == []
    assert store.list(user_id="bob") == []


def test_redactor_handles_bearer_and_private_key_shapes() -> None:
    text = "Bearer " + "fixture-token-value"
    assert redact_sensitive_text(text) == "[REDACTED]"
