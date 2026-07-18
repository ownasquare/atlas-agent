"""Extension contracts for the Atlas tool registry."""

from collections.abc import Mapping
from pathlib import Path

import pytest
from langchain_core.tools import BaseTool, tool

from atlas_agent.config import Settings
from atlas_agent.schemas import SearchResult, ToolEvidence
from atlas_agent.tools.registry import build_tool_bundle
from atlas_agent.tools.web_search import SearchProvider


class NoNetworkSearch(SearchProvider):
    name = "offline"

    def search(self, query: str, *, max_results: int, topic: str) -> list[SearchResult]:
        del query, max_results, topic
        return []


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=False,
        code_execution_backend="disabled",
    )


@tool
def zebra_tool(value: str) -> str:
    """Return a harmless value for registry ordering tests."""
    return value


@tool
def alpha_tool(value: str) -> str:
    """Return a harmless value for registry ordering tests."""
    return value


def alpha_evidence(payload: Mapping[str, object]) -> ToolEvidence:
    del payload
    return ToolEvidence(sources=["https://example.test/reference"])


def test_extra_tools_are_sorted_and_evidence_requires_explicit_registration(
    tmp_path: Path,
) -> None:
    bundle = build_tool_bundle(
        settings(tmp_path),
        search_provider=NoNetworkSearch(),
        extra_tools=[zebra_tool, alpha_tool],
        evidence_extractors={"alpha_tool": alpha_evidence},
    )

    assert [item.name for item in bundle.tools][-2:] == ["alpha_tool", "zebra_tool"]
    assert bundle.evidence_extractors["alpha_tool"] is alpha_evidence
    assert "zebra_tool" not in bundle.evidence_extractors


def test_duplicate_custom_tool_names_fail_clearly(tmp_path: Path) -> None:
    extra_tools: list[BaseTool] = [alpha_tool, alpha_tool]
    with pytest.raises(ValueError, match="duplicate tool name: alpha_tool"):
        build_tool_bundle(
            settings(tmp_path),
            search_provider=NoNetworkSearch(),
            extra_tools=extra_tools,
        )


def test_custom_tool_cannot_replace_a_builtin(tmp_path: Path) -> None:
    @tool("calculator")
    def replacement(value: str) -> str:
        """Attempt to replace a built-in tool."""
        return value

    with pytest.raises(ValueError, match="duplicate tool name: calculator"):
        build_tool_bundle(
            settings(tmp_path),
            search_provider=NoNetworkSearch(),
            extra_tools=[replacement],
        )


def test_evidence_extractor_must_name_a_registered_custom_tool(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown tool: missing_tool"):
        build_tool_bundle(
            settings(tmp_path),
            search_provider=NoNetworkSearch(),
            evidence_extractors={"missing_tool": alpha_evidence},
        )
