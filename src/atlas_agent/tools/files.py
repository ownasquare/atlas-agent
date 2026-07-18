"""Workspace-confined file capabilities with bounded, atomic operations."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from typing import Any

from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from langchain.tools import BaseTool, tool
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

ABSENT_FILE_MARKER = "atlas:file:absent:v1"


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=1_000_000)
    overwrite: bool = False


class ListFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default=".", max_length=500)
    pattern: str = Field(default="*", min_length=1, max_length=100)


class SearchFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    path: str = Field(default=".", max_length=500)
    pattern: str = Field(default="*", min_length=1, max_length=100)


class WorkspaceFiles:
    """Perform file operations beneath a single root and nowhere else."""

    def __init__(
        self,
        root: Path,
        *,
        lock_dir: Path | None = None,
        lock_timeout_seconds: int = 120,
        max_file_bytes: int = 1_000_000,
        max_output_chars: int = 12_000,
        max_files_scanned: int = 500,
        max_search_bytes: int = 20_000_000,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        default_lock_dir = self.root.parent / ".atlas-file-locks"
        self.lock_dir = (lock_dir or default_lock_dir).expanduser().resolve()
        if self.lock_dir == self.root or self.root in self.lock_dir.parents:
            raise ValueError("file lock directory must be outside the agent workspace")
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.max_output_chars = max_output_chars
        self.max_files_scanned = max_files_scanned
        self.max_search_bytes = max_search_bytes

    def resolve(self, relative_path: str, *, allow_root: bool = False) -> Path:
        normalized = unicodedata.normalize("NFKC", relative_path).strip()
        requested = Path(normalized)
        if requested.is_absolute():
            raise ValueError("absolute paths are not allowed")
        if any(part in {"..", ""} or part.startswith(".") for part in requested.parts) and not (
            allow_root and requested.parts in {(), (".",)}
        ):
            raise ValueError("hidden files and path traversal are not allowed")
        raw = self.root / requested
        self._reject_symlink_components(raw)
        resolved = raw.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path escapes the configured workspace") from exc
        return resolved

    def read(
        self, path: str, *, start_line: int = 1, end_line: int | None = None
    ) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_file():
            raise FileNotFoundError("file does not exist")
        size = target.stat().st_size
        if size > self.max_file_bytes:
            raise ValueError("file exceeds the configured read limit")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("only UTF-8 text files can be read") from exc
        lines = content.splitlines()
        final_line = end_line if end_line is not None else len(lines)
        if final_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        selected = lines[start_line - 1 : final_line]
        return {
            "path": target.relative_to(self.root).as_posix(),
            "content": "\n".join(selected),
            "start_line": start_line,
            "end_line": min(final_line, len(lines)),
            "total_lines": len(lines),
        }

    def write(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = False,
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise ValueError("content exceeds the configured write limit")
        target = self.resolve(path)
        with self._path_lock(target):
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_symlink_components(target)
            existed_before = target.exists()
            if existed_before and not overwrite:
                raise FileExistsError("file already exists; set overwrite=true to replace it")
            if overwrite and expected_fingerprint is not None:
                current_fingerprint = self.fingerprint(path)
                if current_fingerprint != expected_fingerprint:
                    raise FileExistsError("file changed since overwrite approval")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".atlas-write-",
                dir=target.parent,
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if overwrite:
                    if (
                        expected_fingerprint is not None
                        and self.fingerprint(path) != expected_fingerprint
                    ):
                        raise FileExistsError("file changed since overwrite approval")
                    os.replace(temporary, target)
                else:
                    try:
                        os.link(temporary, target)
                    except FileExistsError as exc:
                        raise FileExistsError(
                            "file was created concurrently; set overwrite=true to replace it"
                        ) from exc
            finally:
                temporary.unlink(missing_ok=True)
            return {
                "path": target.relative_to(self.root).as_posix(),
                "bytes_written": len(encoded),
                "overwritten": overwrite and existed_before,
            }

    @contextmanager
    def _path_lock(self, target: Path) -> Iterator[None]:
        """Hold a cross-process lock for the complete state-check-and-write transaction."""
        canonical_root = unicodedata.normalize("NFKC", os.fspath(self.root)).casefold()
        canonical_relative = unicodedata.normalize(
            "NFKC",
            target.relative_to(self.root).as_posix(),
        ).casefold()
        digest = hashlib.sha256(f"{canonical_root}\x00{canonical_relative}".encode()).hexdigest()
        lock = FileLock(self.lock_dir / f"{digest}.lock")
        try:
            lock.acquire(timeout=self.lock_timeout_seconds)
        except FileLockTimeout as exc:
            raise TimeoutError("timed out waiting for another write to this file") from exc
        try:
            yield
        finally:
            lock.release()

    def list(self, path: str = ".", *, pattern: str = "*", limit: int = 200) -> dict[str, Any]:
        base = self.root if path.strip() in {"", "."} else self.resolve(path)
        if not base.is_dir():
            raise NotADirectoryError("directory does not exist")
        self._validate_pattern(pattern)
        candidates = list(islice(base.glob(pattern), self.max_files_scanned + 1))
        scan_truncated = len(candidates) > self.max_files_scanned
        entries: list[dict[str, Any]] = []
        for candidate in sorted(candidates[: self.max_files_scanned]):
            try:
                self._reject_symlink_components(candidate)
                candidate.resolve(strict=False).relative_to(self.root)
            except ValueError:
                continue
            if candidate.is_symlink() or any(
                part.startswith(".") for part in candidate.relative_to(self.root).parts
            ):
                continue
            entries.append(
                {
                    "path": candidate.relative_to(self.root).as_posix(),
                    "type": "directory" if candidate.is_dir() else "file",
                    "bytes": candidate.stat().st_size if candidate.is_file() else None,
                }
            )
            if len(entries) >= limit:
                break
        return {
            "path": path,
            "entries": entries,
            "truncated": scan_truncated or len(entries) >= limit,
        }

    def search(
        self,
        query: str,
        path: str = ".",
        *,
        pattern: str = "*",
        limit: int = 50,
    ) -> dict[str, Any]:
        base = self.root if path.strip() in {"", "."} else self.resolve(path)
        if not base.is_dir():
            raise NotADirectoryError("directory does not exist")
        self._validate_pattern(pattern)
        candidates = list(islice(base.rglob(pattern), self.max_files_scanned + 1))
        scan_truncated = len(candidates) > self.max_files_scanned
        matches: list[dict[str, Any]] = []
        bytes_scanned = 0
        for candidate in sorted(candidates[: self.max_files_scanned]):
            try:
                self._reject_symlink_components(candidate)
                candidate.resolve(strict=False).relative_to(self.root)
            except ValueError:
                continue
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            candidate_size = candidate.stat().st_size
            if candidate_size > self.max_file_bytes:
                continue
            if bytes_scanned + candidate_size > self.max_search_bytes:
                scan_truncated = True
                break
            bytes_scanned += candidate_size
            try:
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines, start=1):
                if query.casefold() in line.casefold():
                    matches.append(
                        {
                            "path": relative.as_posix(),
                            "line": line_number,
                            "text": line[:500],
                        }
                    )
                    if len(matches) >= limit:
                        return {"query": query, "matches": matches, "truncated": True}
        return {"query": query, "matches": matches, "truncated": scan_truncated}

    def serialize(self, payload: dict[str, Any]) -> str:
        """Serialize a tool payload inside the shared model-context budget."""
        bounded = json.loads(json.dumps(payload, ensure_ascii=False))
        rendered = json.dumps(bounded, ensure_ascii=False)
        if len(rendered) <= self.max_output_chars:
            return rendered
        if isinstance(bounded.get("content"), str):
            overage = len(rendered) - self.max_output_chars
            bounded["content"] = bounded["content"][
                : max(0, len(bounded["content"]) - overage - 80)
            ]
            bounded["truncated"] = True
        for key in ("matches", "entries"):
            values = bounded.get(key)
            while isinstance(values, list) and values:
                rendered = json.dumps(bounded, ensure_ascii=False)
                if len(rendered) <= self.max_output_chars:
                    break
                values.pop()
                bounded["truncated"] = True
        rendered = json.dumps(bounded, ensure_ascii=False)
        if len(rendered) > self.max_output_chars:
            rendered = json.dumps(
                {"status": "truncated", "message": "Tool output exceeded the context budget."}
            )
        return rendered

    def exists(self, path: str) -> bool:
        return self.resolve(path).exists()

    def fingerprint(self, path: str) -> str | None:
        """Return a stable token for the currently observed regular file state."""
        target = self.resolve(path)
        if not target.exists():
            return None
        if not target.is_file():
            raise ValueError("overwrite targets must be regular files")
        self._reject_symlink_components(target)
        stat = target.stat()
        if stat.st_size > self.max_file_bytes:
            raise ValueError("file exceeds the configured overwrite limit")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        token = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{digest}"
        return hashlib.sha256(token.encode()).hexdigest()

    def _reject_symlink_components(self, target: Path) -> None:
        current = self.root
        try:
            relative = target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path escapes the configured workspace") from exc
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("symbolic links are not allowed")

    @staticmethod
    def _validate_pattern(pattern: str) -> None:
        requested = Path(pattern)
        if requested.is_absolute() or any(
            part == ".." or part.startswith(".") for part in requested.parts
        ):
            raise ValueError("hidden and parent glob patterns are not allowed")


def build_file_tools(
    workspace: WorkspaceFiles,
    *,
    require_overwrite_approval: bool = True,
) -> list[BaseTool]:
    """Bind one workspace service into LangChain tool objects."""

    @tool(args_schema=ReadFileInput)
    def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Read a bounded range from a UTF-8 text file in the agent workspace."""
        return workspace.serialize(
            workspace.read(path, start_line=start_line, end_line=end_line),
        )

    @tool(args_schema=WriteFileInput)
    def write_file(path: str, content: str, overwrite: bool = False) -> str:
        """Create a UTF-8 text artifact in the workspace; overwrites may require approval."""
        final_content = content
        observed_fingerprint = workspace.fingerprint(path) if overwrite else None
        observed_state_token = observed_fingerprint or ABSENT_FILE_MARKER
        if overwrite and require_overwrite_approval:
            response = interrupt(
                {
                    "action": "overwrite_file",
                    "question": f"Approve writing to '{path}' with overwrite intent?",
                    "details": {
                        "path": path,
                        "content_preview": content[:500],
                        "state_token": observed_state_token,
                        "target_exists": observed_fingerprint is not None,
                    },
                }
            )
            if not isinstance(response, dict) or response.get("action") != "approve":
                return json.dumps({"status": "rejected", "path": path})
            if response.get("state_token") != observed_state_token:
                return json.dumps(
                    {
                        "status": "stale",
                        "path": path,
                        "reason": "File state changed since approval was requested.",
                    }
                )
            edited = response.get("edited_arguments")
            if isinstance(edited, dict) and isinstance(edited.get("content"), str):
                final_content = edited["content"]
        result = workspace.write(
            path,
            final_content,
            overwrite=observed_fingerprint is not None,
            expected_fingerprint=observed_fingerprint,
        )
        return workspace.serialize({"status": "succeeded", **result})

    @tool(args_schema=ListFilesInput)
    def list_files(path: str = ".", pattern: str = "*") -> str:
        """List files and directories under a workspace path using a bounded glob."""
        return workspace.serialize(workspace.list(path, pattern=pattern))

    @tool(args_schema=SearchFilesInput)
    def search_files(query: str, path: str = ".", pattern: str = "*") -> str:
        """Search UTF-8 workspace files for literal text and return matching lines."""
        return workspace.serialize(workspace.search(query, path, pattern=pattern))

    return [read_file, write_file, list_files, search_files]
