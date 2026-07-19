"""FastAPI and server-sent-event interface for Atlas Agent."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any, cast

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from atlas_agent import __version__
from atlas_agent.config import Settings, get_settings
from atlas_agent.runtime import AtlasRuntime, ThreadConflictError, open_runtime
from atlas_agent.schemas import (
    ChatRequest,
    HealthResponse,
    ResumeRequest,
    RunResult,
    StreamEvent,
)
from atlas_agent.tools.files import WorkspaceFiles


def _runtime(request: Request) -> AtlasRuntime:
    return cast(AtlasRuntime, request.app.state.runtime)


RuntimeDependency = Annotated[AtlasRuntime, Depends(_runtime)]


def _workspace_files(runtime: AtlasRuntime) -> WorkspaceFiles:
    tool_bundle = getattr(runtime, "tool_bundle", None)
    workspace = getattr(tool_bundle, "workspace", None)
    if not isinstance(workspace, WorkspaceFiles):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace files are unavailable.",
        )
    return workspace


def _require_model_setup(settings: Settings) -> None:
    """Return stable, secret-safe setup guidance before task execution."""
    action = settings.model_setup_action
    if action is None:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "type": "ModelSetupRequired",
            "message": "Atlas needs model setup before it can start or resume a task.",
            "action": action,
            "doctor": "uv run atlas doctor",
        },
    )


def _sse(event: StreamEvent) -> str:
    return f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


def create_app(
    settings: Settings | None = None,
    *,
    runtime_override: AtlasRuntime | Any | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if runtime_override is not None:
            app.state.runtime = runtime_override
            yield
            return
        async with open_runtime(active_settings) as runtime:
            app.state.runtime = runtime
            yield

    app = FastAPI(
        title="Atlas Agent API",
        version=__version__,
        description="Plan, execute, verify, persist, and resume complex tool-using agent tasks.",
        lifespan=lifespan,
    )
    static_directory = files("atlas_agent").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_directory)), name="static")

    @app.get("/", include_in_schema=False)
    async def control_room() -> FileResponse:
        return FileResponse(str(static_directory.joinpath("index.html")))

    @app.get("/api/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            version=__version__,
            model=active_settings.model,
            model_configured=active_settings.model_is_configured,
            memory_enabled=active_settings.memory_enabled,
            code_backend=active_settings.code_execution_backend,
        )

    @app.get("/api/graph", tags=["system"])
    async def graph(runtime: RuntimeDependency) -> dict[str, str]:
        return {"mermaid": runtime.graph_mermaid()}

    @app.post("/api/chat", response_model=RunResult, tags=["agent"])
    async def chat(
        request: ChatRequest,
        runtime: RuntimeDependency,
    ) -> RunResult:
        _require_model_setup(active_settings)
        try:
            return await runtime.run(
                message=request.message,
                user_id=request.user_id,
                thread_id=request.thread_id,
            )
        except ThreadConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"type": type(exc).__name__, "message": str(exc)},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "type": type(exc).__name__,
                    "message": "The agent could not complete the run.",
                },
            ) from exc

    @app.post("/api/chat/stream", tags=["agent"])
    async def chat_stream(
        request: ChatRequest,
        runtime: RuntimeDependency,
    ) -> StreamingResponse:
        _require_model_setup(active_settings)

        async def generate() -> AsyncIterator[str]:
            async for event in runtime.stream(
                message=request.message,
                user_id=request.user_id,
                thread_id=request.thread_id,
            ):
                yield _sse(event)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/resume", response_model=RunResult, tags=["agent"])
    async def resume(
        request: ResumeRequest,
        runtime: RuntimeDependency,
    ) -> RunResult:
        _require_model_setup(active_settings)
        try:
            return await runtime.resume(
                user_id=request.user_id,
                thread_id=request.thread_id,
                response=request.response,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"type": type(exc).__name__, "message": "The paused run could not resume."},
            ) from exc

    @app.post("/api/resume/stream", tags=["agent"])
    async def resume_stream(
        request: ResumeRequest,
        runtime: RuntimeDependency,
    ) -> StreamingResponse:
        _require_model_setup(active_settings)

        async def generate() -> AsyncIterator[str]:
            async for event in runtime.stream(
                message=None,
                user_id=request.user_id,
                thread_id=request.thread_id,
                response=request.response,
            ):
                yield _sse(event)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/api/threads/{thread_id}", tags=["agent"])
    async def thread_state(
        thread_id: str,
        runtime: RuntimeDependency,
        user_id: str = Query(default="local-user", min_length=1, max_length=100),
    ) -> dict[str, Any]:
        return await runtime.state(user_id=user_id, thread_id=thread_id)

    @app.get("/api/workspace", tags=["workspace"])
    async def list_workspace(
        runtime: RuntimeDependency,
        path: str = Query(default=".", max_length=500),
        pattern: str = Query(default="**/*", min_length=1, max_length=100),
        limit: int = Query(default=200, ge=1, le=200),
    ) -> dict[str, Any]:
        workspace = _workspace_files(runtime)
        try:
            return await asyncio.to_thread(
                workspace.list,
                path=path,
                pattern=pattern,
                limit=limit,
            )
        except NotADirectoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The requested workspace folder was not found.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The requested workspace path is not allowed.",
            ) from exc

    @app.get("/api/workspace/file", tags=["workspace"])
    async def read_workspace_file(
        runtime: RuntimeDependency,
        path: str = Query(min_length=1, max_length=500),
    ) -> dict[str, Any]:
        workspace = _workspace_files(runtime)
        try:
            return await asyncio.to_thread(workspace.read, path)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The requested workspace file was not found.",
            ) from exc
        except ValueError as exc:
            reason = str(exc)
            if "read limit" in reason:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="The requested workspace file exceeds the preview limit.",
                ) from exc
            if "UTF-8" in reason:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Only UTF-8 text files can be previewed.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The requested workspace path is not allowed.",
            ) from exc

    @app.get("/api/workspace/download", tags=["workspace"])
    async def download_workspace_file(
        runtime: RuntimeDependency,
        path: str = Query(min_length=1, max_length=500),
    ) -> FileResponse:
        workspace = _workspace_files(runtime)
        try:
            target = await asyncio.to_thread(workspace.resolve, path)
            if not await asyncio.to_thread(target.is_file):
                raise FileNotFoundError("workspace file does not exist")
            file_size = (await asyncio.to_thread(target.stat)).st_size
            if file_size > workspace.max_file_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="The requested workspace file exceeds the download limit.",
                )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The requested workspace file was not found.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The requested workspace path is not allowed.",
            ) from exc
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename=target.name,
            content_disposition_type="attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/memories", tags=["memory"])
    async def list_memories(
        runtime: RuntimeDependency,
        user_id: str = Query(default="local-user", min_length=1, max_length=100),
    ) -> list[dict[str, Any]]:
        if runtime.memory is None:
            return []
        records = await asyncio.to_thread(runtime.memory.list, user_id=user_id)
        return [record.model_dump(mode="json") for record in records]

    @app.delete("/api/memories/{memory_id}", status_code=204, tags=["memory"])
    async def delete_memory(
        memory_id: str,
        runtime: RuntimeDependency,
        user_id: str = Query(default="local-user", min_length=1, max_length=100),
    ) -> Response:
        deleted = (
            await asyncio.to_thread(
                runtime.memory.delete,
                user_id=user_id,
                memory_id=memory_id,
            )
            if runtime.memory is not None
            else False
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory not found")
        return Response(status_code=204)

    @app.delete("/api/memories", tags=["memory"])
    async def clear_memories(
        runtime: RuntimeDependency,
        user_id: str = Query(default="local-user", min_length=1, max_length=100),
    ) -> dict[str, int]:
        deleted = (
            await asyncio.to_thread(runtime.memory.clear, user_id=user_id)
            if runtime.memory is not None
            else 0
        )
        return {"deleted": deleted}

    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "atlas_agent.api:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
