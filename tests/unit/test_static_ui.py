"""Static product contracts for the dependency-free Atlas workspace UI."""

import re
from html.parser import HTMLParser
from pathlib import Path

STATIC_ROOT = Path(__file__).parents[2] / "src" / "atlas_agent" / "static"
UI_FIXTURE = Path(__file__).parents[1] / "ui_fixture.py"


def _read(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


class _MarkupAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.inline_handlers: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)
            if name.lower().startswith("on"):
                self.inline_handlers.append(name)


def _function_body(source: str, name: str) -> str:
    """Return a JavaScript function body without depending on its formatting."""
    declaration = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        source,
    )
    assert declaration is not None, f"Missing JavaScript function: {name}"
    opening_brace = source.find("{", declaration.start())
    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening_brace + 1 : index]
    raise AssertionError(f"Unclosed JavaScript function: {name}")


def test_workspace_shell_uses_plain_language_and_progressive_disclosure() -> None:
    html = _read("index.html")

    assert "<title>Atlas · Workspace</title>" in html
    assert 'id="recentTaskList"' in html
    assert 'id="clearRecentTasksButton"' in html
    assert 'id="newThreadButton"' in html
    assert 'id="workspaceSettings"' in html
    assert 'id="setupNotice"' in html
    assert 'id="setupMessage"' in html
    assert 'id="themeButton"' in html
    assert 'id="statusAnnouncer"' in html
    assert 'data-task-view="empty"' in html
    assert 'id="capabilitiesDisclosure"' in html
    assert 'class="workflow-orientation"' in html
    assert all(label in html for label in ("Describe", "Work", "Review"))
    assert all(
        f'data-task-panel="{panel}"' in html for panel in ("plan", "activity", "result", "files")
    )
    assert "Agent control room" not in html
    assert "LANGGRAPH · DURABLE EXECUTION" not in html
    assert "User namespace" not in html
    assert "Give Atlas a mission" not in html
    assert "Run mission" not in html
    assert 'href="/static/styles.css?v=0.3.1"' in html
    assert 'src="/static/app.js?v=0.3.1"' in html


def test_setup_guidance_matches_the_default_and_optional_model_providers() -> None:
    html = _read("index.html")

    assert "Connect a model to start tasks" in html
    assert "Your credential stays in local configuration and is never shown here." in html
    assert "OPENAI_API_KEY" in html
    assert "ATLAS_MODEL" in html
    assert "ANTHROPIC_API_KEY" in html
    assert "uv run atlas doctor" in html
    assert re.search(r'<details\s+class="setup-provider-details">', html)
    assert "Provider examples" in html
    setup_notice = re.search(
        r'<section\s+id="setupNotice".*?</section>',
        html,
        re.S,
    )
    assert setup_notice is not None
    assert "API docs" not in setup_notice.group(0)
    assert "Technical reference" not in html

    guidance = _function_body(_read("app.js"), "renderSetupGuidance")
    assert all(provider in guidance for provider in ("openai", "anthropic", "selected"))
    assert all(key in guidance for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"))
    assert "replaceChildren()" in guidance
    assert "document.createTextNode" in guidance


def test_starter_tasks_work_with_the_safe_default_toolset() -> None:
    html = _read("index.html")

    assert "Plan a budget" in html
    assert "12% contingency" in html
    assert "Create a sample quarterly sales CSV" not in html
    assert "Analyze data" not in html


def test_workspace_shell_exposes_results_files_and_accessible_landmarks() -> None:
    html = _read("index.html")

    assert 'href="#mainContent"' in html
    assert 'id="mainContent"' in html
    assert 'id="workspaceFileList"' in html
    assert 'id="workspacePreview"' in html
    assert 'id="refreshWorkspaceButton"' in html
    assert 'id="copyFileButton"' in html
    assert 'id="approvalPanel"' in html
    assert re.search(r"<dialog\b[^>]*\bid=['\"]approvalPanel['\"]", html)
    assert "<details" in html
    assert "Saved context" in html
    assert "Developer settings" in html
    assert "Engineering details" not in html
    assert html.count('aria-live="polite"') == 1


def test_workspace_markup_has_unique_ids_and_no_inline_event_handlers() -> None:
    audit = _MarkupAudit()
    audit.feed(_read("index.html"))

    assert len(audit.ids) == len(set(audit.ids))
    assert audit.inline_handlers == []


def test_workspace_styles_support_two_themes_focus_and_reduced_motion() -> None:
    css = _read("styles.css")

    assert ':root[data-theme="dark"]' in css
    assert ":focus-visible" in css
    assert "@media (max-width: 700px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "--control-height: 44px" in css
    assert "--control-border:" in css
    assert "min-width: 320px" not in css
    assert ".main-workspace:focus-visible" not in css
    assert "font-size: 11px" not in css
    recent_clear = re.search(r"\.section-heading \.recent-clear\s*\{(?P<body>.*?)\}", css, re.S)
    assert recent_clear is not None
    assert "min-height: var(--control-height)" in recent_clear.group("body")


def test_task_views_progressively_disclose_work_without_hiding_safety_feedback() -> None:
    html = _read("index.html")
    css = _read("styles.css")
    javascript = _read("app.js")

    assert 'body[data-task-view="empty"] [data-task-panel]' in css
    assert 'body[data-task-view="active"] [data-task-panel="files"]' in css
    assert '[data-task-panel="result"]:not(.has-content)' in css
    assert 'id="setupNotice"' in html
    assert 'setAttribute("role", "alert")' in _function_body(javascript, "showTaskError")

    set_view = _function_body(javascript, "setTaskView")
    assert all(view in set_view for view in ("empty", "active", "complete"))
    assert 'setTaskView("active")' in _function_body(javascript, "setRunning")
    assert 'setTaskView("empty")' in _function_body(javascript, "startNewTask")
    assert 'setTaskView(result.status === "interrupted" ? "active" : "complete")' in _function_body(
        javascript, "renderResult"
    )


def test_compact_information_disclosures_are_keyboard_and_touch_ready() -> None:
    html = _read("index.html")
    css = _read("styles.css")
    javascript = _read("app.js")

    assert re.search(r'<details\s+id="capabilitiesDisclosure"', html)
    assert re.search(r'<details\s+id="workspaceSettings"', html)
    assert html.count('class="info-mark"') >= 2
    assert 'aria-label="View Atlas capabilities"' in html
    assert "touch-action: manipulation" in css
    capability_summary = re.search(
        r"\.capability-disclosure\s*>\s*summary\s*\{(?P<body>.*?)\}", css, re.S
    )
    assert capability_summary is not None
    assert "min-height: var(--control-height)" in capability_summary.group("body")
    disclosure = _function_body(javascript, "connectDisclosure")
    assert 'event.key !== "Escape"' in disclosure
    assert "summary.focus()" in disclosure
    assert 'content: "+"' not in css
    assert f'content: "{chr(0x2212)}"' not in css


def test_workspace_script_has_recent_task_file_and_theme_behaviors() -> None:
    javascript = _read("app.js")

    assert "atlas-recent-tasks-v2" in javascript
    assert "loadRecentTasks" in javascript
    assert "function clearRecentTasks()" in javascript
    assert "Durable task data and files will stay available." in javascript
    assert "restoreThread" in javascript
    assert "loadWorkspace" in javascript
    assert "openWorkspaceFile" in javascript
    assert "applyTheme" in javascript
    assert "document.activeElement !== document.body" in javascript
    assert ": elements.runButton;" in javascript
    assert "innerHTML" not in javascript


def test_result_markdown_uses_safe_dom_nodes_and_fixture_exercises_supported_blocks() -> None:
    javascript = _read("app.js")
    renderer = _function_body(javascript, "renderMarkdown")
    inline = _function_body(javascript, "appendInlineMarkdown")
    fixture = UI_FIXTURE.read_text(encoding="utf-8")

    assert "container.replaceChildren()" in renderer
    assert 'node("pre")' in renderer
    assert 'node("code"' in renderer
    assert 'node("h" + String(level))' in renderer
    assert "shallowestHeading" in renderer
    assert "3 + heading[1].length - shallowestHeading" in renderer
    assert "const list = node(tag)" in renderer
    assert "appendInlineMarkdown" in renderer
    assert "document.createTextNode" in inline
    assert "parsedLink" in inline
    assert "innerHTML" not in javascript

    assert '"## Fixture result\\n\\n"' in fixture
    assert "[LangGraph overview]" in fixture
    assert '"```text\\nstatus: complete\\n```"' in fixture


def test_task_focus_is_preserved_during_work_and_moves_to_the_next_action() -> None:
    html = _read("index.html")
    javascript = _read("app.js")
    set_running = _function_body(javascript, "setRunning")
    render_result = _function_body(javascript, "renderResult")
    fail_run = _function_body(javascript, "failRun")
    focus_trap = _function_body(javascript, "trapApprovalFocus")

    assert re.search(r'id="answerPanel"[^>]*\btabindex="-1"', html, re.S)
    assert "elements.task.readOnly = running" in set_running
    assert 'setAttribute("aria-readonly", String(running))' in set_running
    assert "elements.task.focus" in set_running
    assert "elements.answerPanel" in render_result and ".focus(" in render_result
    assert "elements.task.focus" in fail_run
    assert 'event.key !== "Tab"' in focus_trap
    assert "elements.approval.contains(active)" in focus_trap
    assert 'elements.approval.addEventListener("keydown", trapApprovalFocus)' in javascript


def test_task_errors_are_announced_once_and_cleared_for_a_new_task() -> None:
    javascript = _read("app.js")
    task_error = _function_body(javascript, "showTaskError")
    new_task = _function_body(javascript, "startNewTask")

    assert 'setAttribute("role", "alert")' in task_error
    assert "announce(" not in task_error
    assert "clearTaskError()" in new_task
    assert new_task.index("clearTaskError()") < new_task.index("resetRun()")


def test_browser_storage_failures_do_not_break_initialization_or_task_flow() -> None:
    javascript = _read("app.js")

    for wrapper in ("safeStorageGet", "safeStorageSet", "safeStorageRemove"):
        body = _function_body(javascript, wrapper)
        assert "try" in body
        assert "catch" in body

    # Raw browser storage access belongs only inside the guarded wrappers.
    direct_calls = re.findall(r"\blocalStorage\.(getItem|setItem|removeItem)\s*\(", javascript)
    assert sorted(direct_calls) == ["getItem", "removeItem", "setItem"]

    thread_assignments = re.findall(r"elements\.thread\.value\s*=\s*([^;]+);", javascript)
    user_assignments = re.findall(r"elements\.user\.value\s*=\s*([^;]+);", javascript)
    assert any(
        "safeStorageGet" in assignment and "newThreadId" in assignment
        for assignment in thread_assignments
    )
    assert any(
        "safeStorageGet" in assignment and "local-user" in assignment
        for assignment in user_assignments
    )

    assert "safeStorageSet" in _function_body(javascript, "persistIdentity")
    assert "safeStorageSet" in _function_body(javascript, "writeRecentTasks")


def test_stream_requires_a_terminal_result_or_error_event() -> None:
    stream = _function_body(_read("app.js"), "streamRequest")

    terminal_names = set(re.findall(r"\b[A-Za-z_$][\w$]*terminal[\w$]*\b", stream, re.I))
    assert terminal_names, "streamRequest must track whether a terminal event arrived"
    assert re.search(r"event\s*===\s*['\"]result['\"]", stream)
    assert re.search(r"event\s*===\s*['\"]error['\"]", stream)
    assert any(re.search(rf"!\s*{re.escape(name)}\b", stream) for name in terminal_names)
    assert "part.done" in stream
    assert "throw new Error" in stream or "failRun(" in stream


def test_stream_appends_text_and_renders_markdown_once_at_completion() -> None:
    javascript = _read("app.js")
    append_token = _function_body(javascript, "appendToken")
    render_result = _function_body(javascript, "renderResult")

    assert "document.createTextNode" in append_token
    assert "is-streaming" in append_token
    assert "renderMarkdown(" not in append_token
    assert "finalAnswer" in render_result
    assert render_result.count("renderMarkdown(") == 1


def test_workspace_preview_explains_limits_and_exposes_selected_file() -> None:
    javascript = _read("app.js")
    preview = _function_body(javascript, "openWorkspaceFile")
    file_list = _function_body(javascript, "renderWorkspaceFiles")

    assert re.search(r"response\.status\s*===\s*413", preview)
    assert "too large" in preview.lower()
    assert re.search(r"response\.status\s*===\s*415", preview)
    assert "text" in preview.lower() and "preview" in preview.lower()
    assert "state.selectedFile" in file_list
    assert any(
        attribute in file_list for attribute in ("aria-current", "aria-selected", "aria-pressed")
    )


def test_approval_uses_the_native_dialog_lifecycle() -> None:
    html = _read("index.html")
    javascript = _read("app.js")

    assert re.search(r"<dialog\b[^>]*\bid=['\"]approvalPanel['\"]", html)
    assert re.search(r"elements\.approval\.showModal\s*\(\s*\)", javascript)
    assert re.search(r"elements\.approval\.close\s*\(\s*\)", javascript)
    assert "elements.approval.hidden" not in javascript


def test_default_summary_uses_plan_length_without_internal_review_metrics() -> None:
    html = _read("index.html")
    javascript = _read("app.js")
    render_result = _function_body(javascript, "renderResult")

    plan_metric = re.search(r"elements\.iterations\.textContent\s*=\s*([^;]+);", render_result)
    assert plan_metric is not None
    assert "plan" in plan_metric.group(1) and "length" in plan_metric.group(1)
    assert "Checks" not in html
    assert 'id="reviewMetric"' not in html
    assert "reviewMetric" not in javascript
    assert "elements.reviews" not in javascript
    assert "review_cycles" not in render_result


def test_recent_tasks_are_a_semantic_list() -> None:
    html = _read("index.html")
    recent_tasks = _function_body(_read("app.js"), "loadRecentTasks")

    assert re.search(r"<(?:ul|ol)\b[^>]*\bid=['\"]recentTaskList['\"]", html)
    assert re.search(r"node\(\s*['\"]li['\"]", recent_tasks)
    assert "recent-task-row" in recent_tasks
