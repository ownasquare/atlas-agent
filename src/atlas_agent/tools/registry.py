"""Construction and dependency injection for the complete tool set."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from langchain.tools import BaseTool
from pydantic import TypeAdapter, ValidationError

from atlas_agent.config import Settings
from atlas_agent.graph import BUILTIN_EVIDENCE_EXTRACTORS
from atlas_agent.schemas import EvidenceExtractor, ToolName
from atlas_agent.tools.calculator import calculator
from atlas_agent.tools.files import WorkspaceFiles, build_file_tools
from atlas_agent.tools.python_exec import PythonExecutor, build_python_tool
from atlas_agent.tools.web_search import (
    DdgsSearchProvider,
    SearchProvider,
    TavilySearchProvider,
    WebSearchService,
    build_web_search_tool,
)


@dataclass(frozen=True)
class ToolBundle:
    tools: list[BaseTool]
    workspace: WorkspaceFiles
    search: WebSearchService
    python: PythonExecutor
    evidence_extractors: Mapping[str, EvidenceExtractor]


def build_tool_bundle(
    settings: Settings,
    *,
    search_provider: SearchProvider | None = None,
    extra_tools: Iterable[BaseTool] = (),
    evidence_extractors: Mapping[str, EvidenceExtractor] | None = None,
) -> ToolBundle:
    workspace = WorkspaceFiles(
        settings.workspace_dir,
        lock_dir=settings.file_lock_dir,
        lock_timeout_seconds=settings.thread_lock_timeout_seconds,
        max_file_bytes=settings.max_file_bytes,
        max_output_chars=settings.max_tool_output_chars,
    )
    if search_provider is None:
        if settings.tavily_api_key is not None:
            search_provider = TavilySearchProvider(settings.tavily_api_key.get_secret_value())
        else:
            search_provider = DdgsSearchProvider()
    search = WebSearchService(
        search_provider,
        result_limit=settings.search_max_results,
        output_limit=settings.max_tool_output_chars,
    )
    python = PythonExecutor(
        backend=settings.code_execution_backend,
        timeout_seconds=settings.code_timeout_seconds,
        memory_mb=settings.code_memory_mb,
        output_limit=settings.max_tool_output_chars,
    )
    tools: list[BaseTool] = [calculator, build_web_search_tool(search)]
    tools.extend(
        build_file_tools(
            workspace,
            require_overwrite_approval=settings.require_overwrite_approval,
        )
    )
    if settings.code_execution_backend != "disabled":
        tools.append(build_python_tool(python, require_approval=settings.require_code_approval))

    custom_tools = sorted(tuple(extra_tools), key=lambda item: item.name)
    seen_names: set[str] = set()
    tool_name_adapter = TypeAdapter(ToolName)
    for item in [*tools, *custom_tools]:
        try:
            name = tool_name_adapter.validate_python(item.name)
        except ValidationError as exc:
            raise ValueError(f"invalid tool name: {item.name}") from exc
        if name in seen_names:
            raise ValueError(f"duplicate tool name: {name}")
        seen_names.add(name)
    tools.extend(custom_tools)

    custom_names = {item.name for item in custom_tools}
    registered_extractors = dict(BUILTIN_EVIDENCE_EXTRACTORS)
    for name, extractor in sorted((evidence_extractors or {}).items()):
        if name not in custom_names:
            raise ValueError(f"evidence extractor registered for unknown tool: {name}")
        registered_extractors[name] = extractor

    return ToolBundle(
        tools=tools,
        workspace=workspace,
        search=search,
        python=python,
        evidence_extractors=MappingProxyType(registered_extractors),
    )
