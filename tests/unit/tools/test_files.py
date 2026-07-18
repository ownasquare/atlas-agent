"""Tests for workspace-confined, bounded file operations."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from atlas_agent.tools.files import WorkspaceFiles, build_file_tools


class ToolState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def test_nested_create_read_list_and_search(tmp_path: Path) -> None:
    workspace = WorkspaceFiles(tmp_path / "workspace")

    write_result = workspace.write("reports/summary.txt", "Alpha\nBeta finding\nGamma")
    read_result = workspace.read("reports/summary.txt", start_line=2, end_line=2)
    list_result = workspace.list("reports", pattern="*.txt")
    search_result = workspace.search("beta", "reports", pattern="*.txt")

    assert write_result == {
        "path": "reports/summary.txt",
        "bytes_written": 24,
        "overwritten": False,
    }
    assert read_result == {
        "path": "reports/summary.txt",
        "content": "Beta finding",
        "start_line": 2,
        "end_line": 2,
        "total_lines": 3,
    }
    assert list_result["entries"] == [{"path": "reports/summary.txt", "type": "file", "bytes": 24}]
    assert search_result == {
        "query": "beta",
        "matches": [{"path": "reports/summary.txt", "line": 2, "text": "Beta finding"}],
        "truncated": False,
    }


def test_write_is_create_new_by_default_and_atomic_on_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    workspace.write("answer.txt", "first")

    with pytest.raises(FileExistsError, match="overwrite=true"):
        workspace.write("answer.txt", "second")

    result = workspace.write("answer.txt", "second", overwrite=True)

    assert result == {"path": "answer.txt", "bytes_written": 6, "overwritten": True}
    assert (root / "answer.txt").read_text(encoding="utf-8") == "second"
    assert list(root.glob(".atlas-write-*")) == []


def test_create_new_cannot_clobber_a_concurrent_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    original_link = __import__("os").link

    def racing_link(source: str | Path, destination: str | Path) -> None:
        Path(destination).write_text("competitor", encoding="utf-8")
        original_link(source, destination)

    monkeypatch.setattr("atlas_agent.tools.files.os.link", racing_link)

    with pytest.raises(FileExistsError, match="concurrently"):
        workspace.write("shared.txt", "atlas")

    assert (root / "shared.txt").read_text(encoding="utf-8") == "competitor"
    assert list(root.glob(".atlas-write-*")) == []


def test_overwrite_rejects_a_file_changed_after_state_was_observed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    workspace.write("shared.txt", "reviewed version")
    approved_fingerprint = workspace.fingerprint("shared.txt")
    assert approved_fingerprint is not None
    (root / "shared.txt").write_text("changed after review", encoding="utf-8")

    with pytest.raises(FileExistsError, match="changed since overwrite approval"):
        workspace.write(
            "shared.txt",
            "replacement",
            overwrite=True,
            expected_fingerprint=approved_fingerprint,
        )

    assert (root / "shared.txt").read_text(encoding="utf-8") == "changed after review"


@pytest.mark.parametrize(
    "second_path",
    ["shared.txt", "SHARED.txt"],
    ids=["same-spelling", "case-alias"],
)
def test_two_workspace_instances_cannot_spend_the_same_overwrite_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_path: str,
) -> None:
    root = tmp_path / "workspace"
    first_workspace = WorkspaceFiles(root)
    second_workspace = WorkspaceFiles(root)
    first_workspace.write("shared.txt", "approved baseline")
    if second_path != "shared.txt":
        alias = root / second_path
        if not alias.exists() or not os.path.samefile(root / "shared.txt", alias):
            pytest.skip("filesystem is case-sensitive")
    approved_fingerprint = first_workspace.fingerprint("shared.txt")
    assert approved_fingerprint is not None

    original_replace = os.replace
    first_replace_started = Event()
    allow_first_replace = Event()
    call_count = 0
    count_lock = Lock()

    def pause_first_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal call_count
        with count_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_replace_started.set()
            if not allow_first_replace.wait(timeout=2):
                raise TimeoutError("test did not release the first writer")
        original_replace(source, destination)

    monkeypatch.setattr("atlas_agent.tools.files.os.replace", pause_first_replace)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            first_workspace.write,
            "shared.txt",
            "first approved replacement",
            overwrite=True,
            expected_fingerprint=approved_fingerprint,
        )
        assert first_replace_started.wait(timeout=2)
        second = pool.submit(
            second_workspace.write,
            second_path,
            "second stale replacement",
            overwrite=True,
            expected_fingerprint=approved_fingerprint,
        )
        try:
            assert not second.done()
        finally:
            allow_first_replace.set()
        first_result = first.result(timeout=2)
        with pytest.raises(FileExistsError, match="changed since overwrite approval"):
            second.result(timeout=2)

    assert first_result["overwritten"] is True
    assert (root / "shared.txt").read_text(encoding="utf-8") == "first approved replacement"


async def test_overwrite_interrupt_rejects_state_changed_before_resume(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    workspace.write("shared.txt", "reviewed version")
    write_tool = next(tool for tool in build_file_tools(workspace) if tool.name == "write_file")
    builder = StateGraph(ToolState)
    builder.add_node("tools", ToolNode([write_tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "overwrite-state-token"}}
    tool_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {
                    "path": "shared.txt",
                    "content": "approved replacement",
                    "overwrite": True,
                },
                "id": "write-1",
                "type": "tool_call",
            }
        ],
    )

    paused = await graph.ainvoke(
        {"messages": [tool_call]},
        config,
        durability="sync",
        version="v2",
    )
    assert len(paused.interrupts) == 1
    state_token = paused.interrupts[0].value["details"]["state_token"]
    (root / "shared.txt").write_text("changed while paused", encoding="utf-8")

    resumed = await graph.ainvoke(
        Command(
            resume={
                paused.interrupts[0].id: {
                    "action": "approve",
                    "state_token": state_token,
                    "edited_arguments": None,
                }
            }
        ),
        config,
        durability="sync",
        version="v2",
    )
    tool_message = next(
        message for message in resumed.value["messages"] if isinstance(message, ToolMessage)
    )

    assert json.loads(str(tool_message.content))["status"] == "stale"
    assert (root / "shared.txt").read_text(encoding="utf-8") == "changed while paused"


async def test_overwrite_interrupt_rejects_target_deleted_before_resume(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    workspace.write("shared.txt", "reviewed version")
    write_tool = next(tool for tool in build_file_tools(workspace) if tool.name == "write_file")
    builder = StateGraph(ToolState)
    builder.add_node("tools", ToolNode([write_tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "overwrite-deleted-target"}}
    tool_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {
                    "path": "shared.txt",
                    "content": "must not be recreated",
                    "overwrite": True,
                },
                "id": "write-delete-race",
                "type": "tool_call",
            }
        ],
    )

    paused = await graph.ainvoke(
        {"messages": [tool_call]},
        config,
        durability="sync",
        version="v2",
    )
    state_token = paused.interrupts[0].value["details"]["state_token"]
    (root / "shared.txt").unlink()

    resumed = await graph.ainvoke(
        Command(
            resume={
                paused.interrupts[0].id: {
                    "action": "approve",
                    "state_token": state_token,
                    "edited_arguments": None,
                }
            }
        ),
        config,
        durability="sync",
        version="v2",
    )
    tool_message = next(
        message for message in resumed.value["messages"] if isinstance(message, ToolMessage)
    )

    assert json.loads(str(tool_message.content))["status"] == "stale"
    assert not (root / "shared.txt").exists()


async def test_overwrite_interrupt_rejects_target_created_before_resume(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    write_tool = next(tool for tool in build_file_tools(workspace) if tool.name == "write_file")
    builder = StateGraph(ToolState)
    builder.add_node("tools", ToolNode([write_tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "overwrite-created-target"}}
    tool_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {
                    "path": "shared.txt",
                    "content": "must not replace competitor",
                    "overwrite": True,
                },
                "id": "write-create-race",
                "type": "tool_call",
            }
        ],
    )

    paused = await graph.ainvoke(
        {"messages": [tool_call]},
        config,
        durability="sync",
        version="v2",
    )
    state_token = paused.interrupts[0].value["details"]["state_token"]
    assert paused.interrupts[0].value["details"]["target_exists"] is False
    (root / "shared.txt").write_text("competitor", encoding="utf-8")

    resumed = await graph.ainvoke(
        Command(
            resume={
                paused.interrupts[0].id: {
                    "action": "approve",
                    "state_token": state_token,
                    "edited_arguments": None,
                }
            }
        ),
        config,
        durability="sync",
        version="v2",
    )
    tool_message = next(
        message for message in resumed.value["messages"] if isinstance(message, ToolMessage)
    )

    assert json.loads(str(tool_message.content))["status"] == "stale"
    assert (root / "shared.txt").read_text(encoding="utf-8") == "competitor"


@pytest.mark.parametrize(
    "path",
    [
        "/escape.txt",
        "../escape.txt",
        "nested/../../escape.txt",
        ".env",
        "nested/.secret",
    ],
)
def test_resolve_rejects_absolute_traversal_and_hidden_paths(tmp_path: Path, path: str) -> None:
    workspace = WorkspaceFiles(tmp_path / "workspace")

    with pytest.raises(ValueError):
        workspace.resolve(path)


def test_resolve_rejects_prefix_confusion_absolute_path(tmp_path: Path) -> None:
    workspace = WorkspaceFiles(tmp_path / "workspace")
    confusing_path = tmp_path / "workspace-elsewhere" / "loot.txt"

    with pytest.raises(ValueError, match="absolute paths"):
        workspace.resolve(str(confusing_path))


def test_read_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    link = root / "shortcut.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {type(exc).__name__}")

    with pytest.raises(ValueError, match="symbolic links"):
        workspace.read("shortcut.txt")


def test_read_rejects_binary_and_oversized_files(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root, max_file_bytes=4)
    (root / "binary.bin").write_bytes(b"\xff\xfe")
    (root / "large.txt").write_text("12345", encoding="utf-8")

    with pytest.raises(ValueError, match="UTF-8"):
        workspace.read("binary.bin")
    with pytest.raises(ValueError, match="read limit"):
        workspace.read("large.txt")


def test_write_enforces_encoded_byte_limit(tmp_path: Path) -> None:
    workspace = WorkspaceFiles(tmp_path / "workspace", max_file_bytes=4)

    with pytest.raises(ValueError, match="write limit"):
        workspace.write("too-large.txt", "ééé")


def test_list_and_search_skip_hidden_and_symlinked_content(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    (root / "visible.txt").write_text("needle", encoding="utf-8")
    (root / ".hidden.txt").write_text("needle", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("needle", encoding="utf-8")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {type(exc).__name__}")

    listed_paths = [entry["path"] for entry in workspace.list()["entries"]]
    matched_paths = [match["path"] for match in workspace.search("needle")["matches"]]

    assert listed_paths == ["visible.txt"]
    assert matched_paths == ["visible.txt"]


def test_list_and_search_never_follow_a_symlinked_directory_pattern(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not leak this needle", encoding="utf-8")
    link = root / "leak"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {type(exc).__name__}")

    listed = workspace.list(pattern="leak/*")
    searched = workspace.search("needle", pattern="leak/*")

    assert listed["entries"] == []
    assert searched["matches"] == []


def test_read_rejects_inverted_line_range(tmp_path: Path) -> None:
    workspace = WorkspaceFiles(tmp_path / "workspace")
    workspace.write("lines.txt", "one\ntwo\nthree")

    with pytest.raises(ValueError, match="end_line"):
        workspace.read("lines.txt", start_line=3, end_line=2)


def test_file_tool_serialization_enforces_shared_context_budget(tmp_path: Path) -> None:
    workspace = WorkspaceFiles(
        tmp_path / "workspace",
        max_file_bytes=10_000,
        max_output_chars=1_000,
    )
    workspace.write("large.txt", "x" * 5_000)

    rendered = workspace.serialize(workspace.read("large.txt"))
    payload: dict[str, Any] = json.loads(rendered)

    assert len(rendered) <= 1_000
    assert payload["truncated"] is True
    assert len(payload["content"]) < 5_000


def test_list_scan_and_glob_patterns_are_bounded(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace = WorkspaceFiles(root, max_files_scanned=3)
    for index in range(8):
        (root / f"file-{index}.txt").write_text("value", encoding="utf-8")

    result = workspace.list(pattern="*.txt")

    assert len(result["entries"]) == 3
    assert result["truncated"] is True
    with pytest.raises(ValueError, match="glob patterns"):
        workspace.search("value", pattern="../*.txt")
