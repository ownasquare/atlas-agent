"""Register and run a harmless custom Atlas tool without a model or network call."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from atlas_agent.config import Settings
from atlas_agent.schemas import SearchResult, ToolEvidence
from atlas_agent.tools import build_tool_bundle


class TextStatsInput(BaseModel):
    """Strict, bounded input exposed to the model as the tool schema."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20_000)


@tool(args_schema=TextStatsInput)
def text_stats(text: str) -> str:
    """Count characters, words, and lines in supplied text."""
    return json.dumps(
        {
            "characters": len(text),
            "words": len(text.split()),
            "lines": text.count("\n") + 1,
        },
        ensure_ascii=False,
    )


def extract_text_stats_evidence(payload: Mapping[str, Any]) -> ToolEvidence:
    """Validate the result while explicitly declaring that it creates no evidence."""
    for field in ("characters", "words", "lines"):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"text_stats returned an invalid {field} value")
    return ToolEvidence()


class OfflineSearch:
    """Construction-only search dependency; this example never calls it."""

    name = "offline-example"

    def search(self, query: str, *, max_results: int, topic: str) -> list[SearchResult]:
        del query, max_results, topic
        return []


def main() -> None:
    with TemporaryDirectory(prefix="atlas-extension-") as temporary_directory:
        root = Path(temporary_directory)
        settings = Settings(
            _env_file=None,
            data_dir=root / "data",
            workspace_dir=root / "workspace",
            memory_enabled=False,
            code_execution_backend="disabled",
        )
        bundle = build_tool_bundle(
            settings,
            search_provider=OfflineSearch(),
            extra_tools=[text_stats],
            evidence_extractors={"text_stats": extract_text_stats_evidence},
        )
        registered_tool = next(item for item in bundle.tools if item.name == "text_stats")
        result = json.loads(registered_tool.invoke({"text": "Atlas keeps extensions clear."}))
        evidence = bundle.evidence_extractors[registered_tool.name](result)
        print(
            json.dumps(
                {
                    "registered_tool": registered_tool.name,
                    "result": result,
                    "evidence": evidence.model_dump(mode="json"),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
