"""Atlas Agent: a durable, tool-using LangGraph portfolio project."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("atlas-agent")
except PackageNotFoundError:  # pragma: no cover - source checkout before installation
    __version__ = "0.3.1"

__all__ = ["__version__"]
