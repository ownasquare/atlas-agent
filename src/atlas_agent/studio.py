"""LangGraph Studio entrypoint with development-local persistence."""

from langgraph.checkpoint.memory import InMemorySaver

from atlas_agent.brain import LangChainBrain
from atlas_agent.config import get_settings
from atlas_agent.graph import build_graph
from atlas_agent.memory import VectorMemory
from atlas_agent.tools.registry import build_tool_bundle

settings = get_settings()
settings.ensure_directories()
tool_bundle = build_tool_bundle(settings)
memory = (
    VectorMemory(settings.vector_path, collection_name=settings.memory_collection)
    if settings.memory_enabled
    else None
)
brain = LangChainBrain(settings, tool_bundle.tools)
graph = build_graph(
    settings=settings,
    brain=brain,
    tools=tool_bundle.tools,
    memory=memory,
    checkpointer=InMemorySaver(),
    evidence_extractors=tool_bundle.evidence_extractors,
)
