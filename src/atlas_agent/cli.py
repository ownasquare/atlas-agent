"""Typer/Rich command-line experience for Atlas Agent."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from typing import Annotated, Any

import typer
import uvicorn
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from atlas_agent import __version__
from atlas_agent.config import Settings, get_settings
from atlas_agent.runtime import AtlasRuntime, open_runtime
from atlas_agent.schemas import ApprovalResponse, RunResult, RunStatus
from atlas_agent.tools.python_exec import PythonExecutor

app = typer.Typer(
    name="atlas",
    help="Durable LangGraph agent for researched, calculated, and artifact-producing work.",
    no_args_is_help=True,
)
memory_app = typer.Typer(help="Inspect or clear user-scoped saved vector context.")
app.add_typer(memory_app, name="memory")
console = Console()


def _doctor_report(settings: Settings) -> dict[str, Any]:
    """Build a credential-safe local readiness report."""
    provider = settings.model_provider

    next_actions: list[str] = []
    model_setup_action = settings.model_setup_action
    if model_setup_action is not None:
        next_actions.append(model_setup_action)

    docker_path = shutil.which("docker") if settings.code_execution_backend == "docker" else None
    if settings.code_execution_backend == "disabled":
        python_status = "disabled"
        python_summary = "disabled (safe default)"
    elif docker_path is None:
        python_status = "docker-not-found"
        python_summary = "Docker selected, but the command is unavailable"
        next_actions.append("Install Docker or set ATLAS_CODE_EXECUTION_BACKEND=disabled.")
    elif not PythonExecutor(backend="docker").docker_is_ready():
        python_status = "docker-not-ready"
        python_summary = "Docker selected, but its daemon or Atlas image is unavailable"
        next_actions.append(
            "Start Docker and pull python:3.12-alpine, or set "
            "ATLAS_CODE_EXECUTION_BACKEND=disabled."
        )
    else:
        python_status = "ready"
        python_summary = "Docker daemon and Atlas image are ready"

    ready = settings.model_is_configured and python_status not in {
        "docker-not-found",
        "docker-not-ready",
    }
    return {
        "version": __version__,
        "ready": ready,
        "model": {
            "provider": provider,
            "configured": settings.model_is_configured,
            "credential_configured": settings.model_credential_is_configured,
            "integration_available": settings.model_integration_is_available,
            "model": settings.model_name,
        },
        "search": {
            "mode": "tavily" if settings.tavily_api_key is not None else "ddgs",
            "configured": True,
        },
        "memory": {
            "enabled": settings.memory_enabled,
            "mode": "local-vector-store" if settings.memory_enabled else "conversation-only",
        },
        "python": {
            "backend": settings.code_execution_backend,
            "status": python_status,
            "summary": python_summary,
        },
        "paths": {
            "data": str(settings.data_dir.resolve()),
            "workspace": str(settings.workspace_dir.resolve()),
        },
        "next_actions": next_actions,
    }


def _render_doctor(report: dict[str, Any]) -> None:
    """Render the small human-facing version of a doctor report."""
    table = Table(title="Atlas setup", show_header=False, box=None, pad_edge=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_row("Core", "Ready" if report["ready"] else "Action required")
    model = report["model"]
    if model["configured"]:
        model_status = "configured"
    elif not model["credential_configured"]:
        model_status = "credential missing"
    else:
        model_status = "integration missing"
    table.add_row("Model", f"{model['provider']} · {model['model']} · {model_status}")
    search = report["search"]
    search_status = "Tavily" if search["mode"] == "tavily" else "DDGS fallback"
    table.add_row("Research", search_status)
    memory = report["memory"]
    table.add_row("Memory", "on" if memory["enabled"] else "off")
    table.add_row("Python", report["python"]["summary"])
    table.add_row("Workspace", report["paths"]["workspace"])
    console.print(table)

    actions = report["next_actions"]
    if actions:
        console.print("\n[bold]Next:[/bold]")
        for action in actions:
            console.print(f"  • {action}")
    else:
        console.print("\n[green]Atlas is ready for a task.[/green]")


def _new_thread_id() -> str:
    return f"thread-{uuid.uuid4().hex[:10]}"


def _require_model_setup(settings: Settings | None = None) -> None:
    """Stop task commands before runtime construction when setup is incomplete."""
    action = (settings or get_settings()).model_setup_action
    if action is None:
        return
    typer.echo("Atlas needs model setup.")
    typer.echo(action)
    typer.echo("Run `uv run atlas doctor` to review readiness, then retry.")
    raise typer.Exit(code=2)


def _render_result(result: RunResult) -> None:
    if result.plan:
        table = Table(title="Execution plan", show_header=True, header_style="bold magenta")
        table.add_column("Step", style="cyan", no_wrap=True)
        table.add_column("Action")
        table.add_column("Proof")
        for step in result.plan:
            table.add_row(step.id, step.description, step.success_criteria)
        console.print(table)
    if result.answer:
        console.print(Panel(Markdown(result.answer), title="Atlas", border_style="magenta"))
    if result.artifacts:
        console.print("[bold]Artifacts:[/bold] " + ", ".join(result.artifacts))
    if result.sources:
        console.print("[bold]Sources:[/bold]")
        for source in result.sources:
            console.print(f"  • {source}")
    console.print(
        f"[dim]thread={result.thread_id} · iterations={result.iterations} · "
        f"reviews={result.review_cycles} · status={result.status}[/dim]"
    )


async def _finish_with_approvals(runtime: AtlasRuntime, result: RunResult) -> RunResult:
    current = result
    while current.status == RunStatus.INTERRUPTED and current.interrupt is not None:
        details = json.dumps(current.interrupt.details, indent=2, ensure_ascii=False)
        console.print(Panel(details, title=current.interrupt.question, border_style="yellow"))
        approved = typer.confirm("Approve this action?", default=False)
        current = await runtime.resume(
            user_id=current.user_id,
            thread_id=current.thread_id,
            response=ApprovalResponse(
                interrupt_id=current.interrupt.id,
                action="approve" if approved else "reject",
                state_token=current.interrupt.details.get("state_token"),
            ),
        )
    return current


async def _run_once(message: str, user_id: str, thread_id: str) -> None:
    async with open_runtime() as runtime:
        with console.status("[bold magenta]Atlas is planning and executing…"):
            result = await runtime.run(message=message, user_id=user_id, thread_id=thread_id)
        result = await _finish_with_approvals(runtime, result)
        _render_result(result)


@app.command("run")
def run_command(
    message: Annotated[str, typer.Argument(help="The complex task Atlas should complete.")],
    user_id: Annotated[
        str, typer.Option("--user", help="Long-term memory namespace.")
    ] = "local-user",
    thread_id: Annotated[
        str | None, typer.Option("--thread", help="Conversation thread to resume.")
    ] = None,
) -> None:
    """Run one task, pausing in the terminal for risky tool approval."""
    _require_model_setup()
    asyncio.run(_run_once(message, user_id, thread_id or _new_thread_id()))


async def _chat(user_id: str, thread_id: str) -> None:
    console.print(
        Panel(f"Thread: [bold]{thread_id}[/bold]\nType /exit to leave.", title="Atlas chat")
    )
    async with open_runtime() as runtime:
        while True:
            message = console.input("\n[bold cyan]You >[/bold cyan] ").strip()
            if message in {"/exit", "/quit"}:
                break
            if not message:
                continue
            with console.status("[bold magenta]Working…"):
                result = await runtime.run(message=message, user_id=user_id, thread_id=thread_id)
            result = await _finish_with_approvals(runtime, result)
            _render_result(result)


@app.command("chat")
def chat_command(
    user_id: Annotated[str, typer.Option("--user")] = "local-user",
    thread_id: Annotated[str | None, typer.Option("--thread")] = None,
) -> None:
    """Start an interactive conversation with durable thread memory."""
    _require_model_setup()
    asyncio.run(_chat(user_id, thread_id or _new_thread_id()))


async def _resume_paused(user_id: str, thread_id: str) -> None:
    async with open_runtime() as runtime:
        snapshot = await runtime.state(user_id=user_id, thread_id=thread_id)
        pending = snapshot.get("interrupts", [])
        if not pending:
            console.print(f"[yellow]No paused approval exists for thread {thread_id}.[/yellow]")
            return
        request = pending[0]
        question = str(request.get("question", "Approve the paused action?"))
        details = json.dumps(request.get("details", {}), indent=2, ensure_ascii=False)
        console.print(Panel(details, title=question, border_style="yellow"))
        approved = typer.confirm("Approve this action?", default=False)
        result = await runtime.resume(
            user_id=user_id,
            thread_id=thread_id,
            response=ApprovalResponse(
                interrupt_id=str(request["id"]),
                action="approve" if approved else "reject",
                state_token=request.get("details", {}).get("state_token"),
            ),
        )
        result = await _finish_with_approvals(runtime, result)
        _render_result(result)


@app.command("resume")
def resume_command(
    thread_id: Annotated[str, typer.Option("--thread", help="Paused durable thread.")],
    user_id: Annotated[str, typer.Option("--user", help="Thread owner namespace.")] = "local-user",
) -> None:
    """Inspect and approve or reject a durable paused action."""
    _require_model_setup()
    asyncio.run(_resume_paused(user_id, thread_id))


@app.command("serve")
def serve_command(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    """Launch the FastAPI task workspace and OpenAPI interface."""
    settings = get_settings()
    uvicorn.run(
        "atlas_agent.api:create_app",
        factory=True,
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command("doctor")
def doctor_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print a machine-readable report."),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Exit with status 1 when required setup is incomplete."),
    ] = False,
) -> None:
    """Check local setup without exposing credential values."""
    report = _doctor_report(get_settings())
    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        _render_doctor(report)
    if strict and not report["ready"]:
        raise typer.Exit(code=1)


async def _print_graph() -> None:
    async with open_runtime() as runtime:
        console.print(runtime.graph_mermaid())


@app.command("graph")
def graph_command() -> None:
    """Print the exact Mermaid diagram generated from the compiled graph."""
    asyncio.run(_print_graph())


async def _list_memories(user_id: str) -> None:
    async with open_runtime() as runtime:
        records = runtime.memory.list(user_id=user_id) if runtime.memory is not None else []
        table = Table(title=f"Long-term memory · {user_id}")
        table.add_column("ID", style="cyan")
        table.add_column("Category")
        table.add_column("Memory")
        table.add_column("Created")
        for record in records:
            table.add_row(
                record.id[:10], record.category, record.content, record.created_at.isoformat()
            )
        console.print(table)


@memory_app.command("list")
def memory_list(user_id: Annotated[str, typer.Option("--user")] = "local-user") -> None:
    """List memories visible to one user namespace."""
    asyncio.run(_list_memories(user_id))


async def _clear_memories(user_id: str) -> None:
    async with open_runtime() as runtime:
        deleted = runtime.memory.clear(user_id=user_id) if runtime.memory is not None else 0
        console.print(f"Deleted {deleted} memories for {user_id}.")


@memory_app.command("clear")
def memory_clear(user_id: Annotated[str, typer.Option("--user")] = "local-user") -> None:
    """Delete every saved memory for one user namespace."""
    if not typer.confirm(f"Delete all memories for {user_id}?", default=False):
        raise typer.Abort()
    asyncio.run(_clear_memories(user_id))


if __name__ == "__main__":  # pragma: no cover
    app()
