import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from atlas_agent.memory import MemoryStore, VectorMemory, redact_sensitive_text
from atlas_agent.schemas import MemoryCandidate


def memory(path: Path, *, collection_name: str = "test_memories") -> VectorMemory:
    return VectorMemory(path, collection_name=collection_name)


def candidate(content: str, category: str = "preference") -> MemoryCandidate:
    return MemoryCandidate(content=content, category=category, importance=4)


def test_vector_memory_implements_the_public_memory_store_protocol(tmp_path: Path) -> None:
    assert isinstance(memory(tmp_path / "vectors"), MemoryStore)


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

    assert first.database_path == tmp_path / "vectors" / "test_memories.sqlite3"
    assert first.database_path.is_file()
    assert [record.content for record in records] == ["Python reports should use concise tables"]
    assert records[0].source_thread == "thread-1"
    assert records[0].distance is None


def test_memory_search_is_relevant_ordered_limited_and_user_scoped(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")
    store.add(
        user_id="alice",
        thread_id="a1",
        candidate=candidate("Python reports should include concise tables"),
    )
    store.add(
        user_id="alice",
        thread_id="a2",
        candidate=candidate("Use Canadian dollars for every budget"),
    )
    store.add(
        user_id="bob",
        thread_id="b",
        candidate=candidate("Python reports should use long appendices"),
    )

    results = store.search(user_id="alice", query="concise Python report", limit=1)

    assert [record.content for record in results] == [
        "Python reports should include concise tables"
    ]
    assert {record.user_id for record in results} == {"alice"}
    assert results[0].distance is not None
    assert math.isfinite(results[0].distance)
    assert 0.0 <= results[0].distance <= 2.0
    assert store.search(user_id="charlie", query="Python", limit=5) == []


def test_search_order_and_ids_remain_stable_after_reopening(tmp_path: Path) -> None:
    path = tmp_path / "vectors"
    first = memory(path)
    preferred = first.add(
        user_id="alice",
        thread_id="first",
        candidate=candidate("Keep the Python deployment checklist concise"),
    )
    first.add(
        user_id="alice",
        thread_id="second",
        candidate=candidate("Use euros when estimating venue costs"),
    )
    assert preferred is not None

    before = first.search(user_id="alice", query="concise Python checklist", limit=2)
    after = memory(path).search(user_id="alice", query="concise Python checklist", limit=2)

    assert [record.id for record in after] == [record.id for record in before]
    assert after[0].id == preferred.id


def test_duplicate_content_is_upserted_case_insensitively(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")
    store.add(
        user_id="alice",
        thread_id="first",
        candidate=candidate("Use Canadian dollars", category="constraint"),
    )
    store.add(
        user_id="alice",
        thread_id="second",
        candidate=MemoryCandidate(
            content="USE CANADIAN DOLLARS",
            category="project",
            importance=5,
        ),
    )

    records = store.list(user_id="alice")

    assert len(records) == 1
    assert records[0].content == "USE CANADIAN DOLLARS"
    assert records[0].source_thread == "second"
    assert records[0].category == "project"
    assert records[0].importance == 5


def test_list_is_newest_first_and_honors_limit(tmp_path: Path) -> None:
    store = memory(tmp_path / "vectors")
    store.add(user_id="alice", thread_id="first", candidate=candidate("First saved preference"))
    store.add(user_id="alice", thread_id="second", candidate=candidate("Second saved preference"))

    records = store.list(user_id="alice", limit=1)

    assert [record.content for record in records] == ["Second saved preference"]


def test_secrets_are_redacted_before_vectorization_and_persistence(tmp_path: Path) -> None:
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
    with sqlite3.connect(store.database_path) as connection:
        persisted = connection.execute("SELECT content FROM memories").fetchone()
    assert persisted is not None
    assert persisted[0] == record.content
    assert "fixture-value" not in persisted[0]


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


def test_collection_names_are_isolated_within_one_parent(tmp_path: Path) -> None:
    first = memory(tmp_path / "vectors", collection_name="first")
    second = memory(tmp_path / "vectors", collection_name="second")
    first.add(user_id="alice", thread_id="a", candidate=candidate("Only in first"))

    assert first.list(user_id="alice")
    assert second.list(user_id="alice") == []
    assert first.database_path != second.database_path


def test_invalid_collection_name_is_rejected_before_path_creation(tmp_path: Path) -> None:
    path = tmp_path / "vectors"

    with pytest.raises(ValueError, match="collection_name"):
        memory(path, collection_name="../escape")

    assert not path.exists()


def test_independent_store_instances_share_writes_safely(tmp_path: Path) -> None:
    path = tmp_path / "vectors"
    stores = [memory(path), memory(path)]

    def add(index: int) -> None:
        stores[index % 2].add(
            user_id="alice",
            thread_id=f"thread-{index}",
            candidate=candidate(f"Preference number {index}"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(add, range(12)))

    assert len(memory(path).list(user_id="alice")) == 12


@pytest.mark.parametrize("round_number", range(5))
def test_concurrent_first_open_of_one_store_is_safe(tmp_path: Path, round_number: int) -> None:
    path = tmp_path / f"vectors-{round_number}"
    barrier = Barrier(8)

    def initialize(_: int) -> Path:
        barrier.wait()
        return memory(path).database_path

    with ThreadPoolExecutor(max_workers=8) as executor:
        database_paths = list(executor.map(initialize, range(8)))

    assert set(database_paths) == {path / "test_memories.sqlite3"}
    assert database_paths[0].is_file()


def test_redactor_handles_bearer_and_private_key_shapes() -> None:
    text = "Bearer " + "fixture-token-value"
    assert redact_sensitive_text(text) == "[REDACTED]"
