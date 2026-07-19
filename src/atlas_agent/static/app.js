const RECENT_TASKS_KEY = "atlas-recent-tasks-v2";
const THEME_KEY = "atlas-theme-v2";
const MAX_RECENT_TASKS = 8;
const storageFallback = new Map();

function safeStorageGet(key, fallbackValue) {
  try {
    const stored = window.localStorage.getItem(key);
    if (stored !== null) {
      storageFallback.set(key, stored);
      return stored;
    }
  } catch {
    // Browser storage can be blocked; the in-memory fallback keeps this session usable.
  }
  return storageFallback.has(key) ? storageFallback.get(key) : fallbackValue;
}

function safeStorageSet(key, value) {
  const serialized = String(value);
  storageFallback.set(key, serialized);
  try {
    window.localStorage.setItem(key, serialized);
    return true;
  } catch {
    return false;
  }
}

function safeStorageRemove(key) {
  storageFallback.delete(key);
  try {
    window.localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

const elements = {
  form: document.querySelector("#taskForm"),
  task: document.querySelector("#taskInput"),
  runButton: document.querySelector("#runButton"),
  user: document.querySelector("#userId"),
  thread: document.querySelector("#threadId"),
  newThread: document.querySelector("#newThreadButton"),
  health: document.querySelector("#healthBadge"),
  setupNotice: document.querySelector("#setupNotice"),
  setupMessage: document.querySelector("#setupMessage"),
  theme: document.querySelector("#themeButton"),
  main: document.querySelector("#mainContent"),
  planPanel: document.querySelector("#planPanel"),
  planList: document.querySelector("#planList"),
  planCount: document.querySelector("#planCount"),
  answerPanel: document.querySelector("#answerPanel"),
  answer: document.querySelector("#answerContent"),
  copy: document.querySelector("#copyButton"),
  evidence: document.querySelector("#evidenceGrid"),
  sources: document.querySelector("#sourceList"),
  artifacts: document.querySelector("#artifactList"),
  timelinePanel: document.querySelector(".timeline-panel"),
  timeline: document.querySelector("#timeline"),
  runStatus: document.querySelector("#runStatus"),
  runSummary: document.querySelector("#runSummary"),
  iterations: document.querySelector("#iterationMetric"),
  artifactMetric: document.querySelector("#artifactMetric"),
  approval: document.querySelector("#approvalPanel"),
  approvalQuestion: document.querySelector("#approvalQuestion"),
  approvalDetails: document.querySelector("#approvalDetails"),
  approve: document.querySelector("#approveButton"),
  reject: document.querySelector("#rejectButton"),
  recentTasks: document.querySelector("#recentTaskList"),
  clearRecentTasks: document.querySelector("#clearRecentTasksButton"),
  workspaceFiles: document.querySelector("#workspaceFileList"),
  workspacePreview: document.querySelector("#workspacePreview"),
  refreshWorkspace: document.querySelector("#refreshWorkspaceButton"),
  copyFile: document.querySelector("#copyFileButton"),
  memoryList: document.querySelector("#memoryList"),
  refreshMemory: document.querySelector("#refreshMemoryButton"),
  clearMemory: document.querySelector("#clearMemoryButton"),
  pythonCapability: document.querySelector("#pythonCapability"),
  announcer: document.querySelector("#statusAnnouncer"),
  toast: document.querySelector("#toast"),
};

const state = {
  running: false,
  modelConfigured: null,
  answer: "",
  approval: null,
  runIdentity: null,
  taskTitle: "",
  selectedFile: "",
  selectedFileContent: "",
  approvalReturnFocus: null,
};

const stageLabels = {
  recall: "Preparing",
  plan: "Planning",
  agent: "Working",
  act: "Working",
  tools: "Using tools",
  review: "Checking work",
  finalize: "Preparing result",
  remember: "Saving context",
  resume: "Continuing",
};

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = String(text);
  if (className) item.className = className;
  return item;
}

function setTaskView(view) {
  const allowed = new Set(["empty", "active", "complete"]);
  document.body.dataset.taskView = allowed.has(view) ? view : "empty";
}

function appendInlineMarkdown(container, content) {
  const text = String(content || "");
  const tokenPattern = /(`[^`\n]+`|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*)/g;
  let cursor = 0;
  let match;
  while ((match = tokenPattern.exec(text)) !== null) {
    if (match.index > cursor) {
      container.append(document.createTextNode(text.slice(cursor, match.index)));
    }
    if (match[0].startsWith("`")) {
      container.append(node("code", match[0].slice(1, -1)));
    } else if (match[2] && match[3]) {
      const url = parsedLink(match[3]);
      if (url) {
        const link = node("a", match[2]);
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.append(node("span", " (opens in a new tab)", "sr-only"));
        container.append(link);
      } else {
        container.append(document.createTextNode(match[0]));
      }
    } else if (match[4]) {
      container.append(node("strong", match[4]));
    }
    cursor = tokenPattern.lastIndex;
  }
  if (cursor < text.length) {
    container.append(document.createTextNode(text.slice(cursor)));
  }
}

function markdownBlockStart(line) {
  return (
    /^```/.test(line) ||
    /^#{1,3}\s+/.test(line) ||
    /^[-*]\s+/.test(line) ||
    /^\d+\.\s+/.test(line)
  );
}

function renderMarkdown(content, container) {
  const lines = String(content || "")
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n");
  const headingDepths = lines
    .map(function headingDepth(line) {
      const match = line.match(/^(#{1,3})\s+/);
      return match ? match[1].length : null;
    })
    .filter(function knownHeading(depth) {
      return depth !== null;
    });
  const shallowestHeading = headingDepths.length ? Math.min(...headingDepths) : 1;
  container.replaceChildren();
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```([A-Za-z0-9_-]*)\s*$/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const pre = node("pre");
      const code = node("code", codeLines.join("\n"));
      if (fence[1]) code.setAttribute("data-language", fence[1].toLowerCase());
      pre.append(code);
      container.append(pre);
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = Math.min(6, 3 + heading[1].length - shallowestHeading);
      const title = node("h" + String(level));
      appendInlineMarkdown(title, heading[2]);
      container.append(title);
      index += 1;
      continue;
    }

    const unordered = line.match(/^[-*]\s+(.+)$/);
    const ordered = line.match(/^\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      const tag = unordered ? "ul" : "ol";
      const list = node(tag);
      while (index < lines.length) {
        const itemMatch =
          tag === "ul"
            ? lines[index].match(/^[-*]\s+(.+)$/)
            : lines[index].match(/^\d+\.\s+(.+)$/);
        if (!itemMatch) break;
        const item = node("li");
        appendInlineMarkdown(item, itemMatch[1]);
        list.append(item);
        index += 1;
      }
      container.append(list);
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !markdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraph = node("p");
    appendInlineMarkdown(paragraph, paragraphLines.join(" "));
    container.append(paragraph);
  }
}

function announce(message) {
  elements.announcer.textContent = "";
  window.requestAnimationFrame(function updateAnnouncement() {
    elements.announcer.textContent = message;
  });
}

function showToast(message, isError) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", Boolean(isError));
  elements.toast.classList.add("is-visible");
  announce(message);
  window.setTimeout(function hideToast() {
    elements.toast.classList.remove("is-visible");
  }, isError ? 6000 : 2600);
}

function showTaskError(message) {
  let alert = document.querySelector("#taskError");
  if (!alert) {
    alert = node("div", "", "inline-alert");
    alert.id = "taskError";
    alert.setAttribute("role", "alert");
    elements.form.before(alert);
  }
  alert.textContent = message;
  alert.hidden = false;
}

function clearTaskError() {
  const alert = document.querySelector("#taskError");
  if (alert) alert.hidden = true;
}

function newThreadId() {
  return "thread-" + crypto.randomUUID().replaceAll("-", "").slice(0, 10);
}

function currentIdentity() {
  return {
    user_id: elements.user.value.trim() || "local-user",
    thread_id: elements.thread.value.trim() || newThreadId(),
  };
}

function persistIdentity(runIdentity) {
  const selected = runIdentity || currentIdentity();
  elements.user.value = selected.user_id;
  elements.thread.value = selected.thread_id;
  safeStorageSet("atlas-user", selected.user_id);
  safeStorageSet("atlas-thread", selected.thread_id);
}

function setRunAvailability() {
  const unavailable = state.running || state.modelConfigured !== true;
  elements.runButton.disabled = unavailable;
  elements.runButton.setAttribute("aria-disabled", String(unavailable));
}

function setRunning(running, label) {
  if (running) setTaskView("active");
  state.running = running;
  elements.task.disabled = false;
  elements.task.readOnly = running;
  elements.task.setAttribute("aria-readonly", String(running));
  elements.runStatus.textContent = label || (running ? "Working" : "Ready");
  elements.timelinePanel.classList.toggle("is-running", running);
  elements.main.setAttribute("aria-busy", String(running));
  setRunAvailability();
  if (running) elements.task.focus({ preventScroll: true });
}

function setIdentityLocked(locked) {
  elements.user.disabled = locked;
  elements.thread.disabled = locked;
  elements.newThread.disabled = locked;
}

function setPaused() {
  setTaskView("active");
  setRunning(false, "Waiting for your decision");
  elements.task.disabled = true;
  elements.runButton.disabled = true;
  setIdentityLocked(true);
}

function emptyState(container, message, className) {
  container.replaceChildren();
  container.className = className;
  const messageNode = node("p", message);
  if (container.matches("ul, ol")) {
    const item = node("li", undefined, "collection-empty");
    item.append(messageNode);
    container.append(item);
  } else {
    container.append(messageNode);
  }
}

function addTimelinePlaceholder() {
  const item = node("li", undefined, "timeline-placeholder");
  const marker = node("span");
  marker.setAttribute("aria-hidden", "true");
  item.append(marker, node("p", "Task progress will appear here."));
  elements.timeline.append(item);
}

function closeApproval(restoreFocus) {
  if (elements.approval.open) elements.approval.close();
  if (restoreFocus && state.approvalReturnFocus instanceof HTMLElement) {
    state.approvalReturnFocus.focus();
  }
  state.approvalReturnFocus = null;
}

function resetRun() {
  state.answer = "";
  state.approval = null;
  closeApproval(false);
  elements.timeline.replaceChildren();
  addTimelinePlaceholder();
  elements.planPanel.classList.add("is-empty");
  emptyState(
    elements.planList,
    "Atlas will outline the work before it begins.",
    "plan-list empty-state",
  );
  elements.planCount.textContent = "Waiting for a task";
  elements.answerPanel.classList.add("is-empty");
  elements.answerPanel.classList.remove("has-content");
  emptyState(
    elements.answer,
    "Your reviewed result will appear here.",
    "answer-content empty-state",
  );
  elements.evidence.hidden = true;
  elements.sources.replaceChildren();
  elements.artifacts.replaceChildren();
  elements.copy.disabled = true;
  elements.runSummary.hidden = true;
  elements.iterations.textContent = "0";
  elements.artifactMetric.textContent = "0";
}

function readableStage(stage) {
  return stageLabels[String(stage || "").toLowerCase()] || "Working";
}

function readableTool(toolName) {
  const labels = {
    web_search: "Research",
    calculator: "Calculate",
    read_file: "Read file",
    write_file: "Create file",
    list_files: "Find files",
    search_files: "Search files",
    execute_python: "Analyze data",
  };
  return labels[toolName] || "Workspace";
}

function addStage(data) {
  const placeholder = elements.timeline.querySelector(".timeline-placeholder");
  if (placeholder) placeholder.remove();
  const item = node("li");
  const marker = node("span");
  marker.setAttribute("aria-hidden", "true");
  const content = node("p");
  const heading = node("b", readableStage(data.stage));
  content.append(heading, document.createTextNode(data.message || "Making progress"));
  item.append(marker, content);
  elements.timeline.append(item);
  elements.runStatus.textContent = readableStage(data.stage);
}

function appendToken(content) {
  const chunk = String(content || "");
  if (!elements.answer.classList.contains("is-streaming")) {
    elements.answer.replaceChildren();
    elements.answer.className = "answer-content is-streaming";
    elements.answerPanel.classList.remove("is-empty");
    elements.answerPanel.classList.add("has-content");
  }
  state.answer += chunk;
  if (chunk) elements.answer.append(document.createTextNode(chunk));
}

function renderPlan(plan) {
  const steps = plan || [];
  elements.planList.replaceChildren();
  elements.planList.className = "plan-list";
  elements.planPanel.classList.remove("is-empty");
  elements.planCount.textContent = String(steps.length) + (steps.length === 1 ? " step" : " steps");
  if (!steps.length) {
    emptyState(elements.planList, "No plan was saved for this task.", "plan-list empty-state");
    return;
  }
  steps.forEach(function renderStep(step, index) {
    const item = node("li", undefined, "plan-item");
    const number = node("span", String(index + 1).padStart(2, "0"), "plan-number");
    const copy = node("div");
    copy.append(
      node("h3", step.description || "Step " + String(index + 1)),
      node(
        "p",
        step.success_criteria
          ? "Done when: " + step.success_criteria
          : "Completion is checked.",
      ),
    );
    item.append(number, copy);
    if (step.tool_hint) item.append(node("span", readableTool(step.tool_hint), "tool-tag"));
    elements.planList.append(item);
  });
}

function parsedLink(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url : null;
  } catch {
    return null;
  }
}

function workspaceUrl(path, endpoint) {
  const params = new URLSearchParams({ path: path });
  return "/api/workspace/" + (endpoint || "file") + "?" + params.toString();
}

function renderEvidence(result) {
  elements.sources.replaceChildren();
  elements.artifacts.replaceChildren();
  (result.sources || []).forEach(function renderSource(source, index) {
    const url = parsedLink(source);
    if (!url) return;
    const link = node(
      "a",
      String(index + 1) + ". " + url.hostname.replace(/^www\./, ""),
    );
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.title = url.href;
    link.append(node("span", " (opens in a new tab)", "sr-only"));
    const item = node("li");
    item.append(link);
    elements.sources.append(item);
  });
  (result.artifacts || []).forEach(function renderArtifact(path) {
    const row = node("li", undefined, "artifact-row");
    const open = node("button", path, "artifact-button");
    open.type = "button";
    open.addEventListener("click", function openArtifact() {
      openWorkspaceFile(path);
    });
    const download = node("a", "Download", "artifact-download");
    download.href = workspaceUrl(path, "download");
    download.setAttribute("download", "");
    download.setAttribute("aria-label", "Download " + path);
    row.append(open, download);
    elements.artifacts.append(row);
  });
  elements.evidence.hidden =
    elements.sources.children.length === 0 && elements.artifacts.children.length === 0;
}

function statusLabel(status) {
  const labels = {
    completed: "Complete",
    interrupted: "Waiting for your decision",
    partial: "Needs review",
    failed: "Could not finish",
    running: "Working",
  };
  return labels[status] || "Ready";
}

function renderResult(result) {
  setTaskView(result.status === "interrupted" ? "active" : "complete");
  renderPlan(result.plan || []);
  const finalAnswer = result.answer || state.answer;
  elements.answer.classList.remove("is-streaming");
  if (finalAnswer) {
    state.answer = finalAnswer;
    elements.answer.className = "answer-content";
    renderMarkdown(finalAnswer, elements.answer);
    elements.answerPanel.classList.remove("is-empty");
    elements.answerPanel.classList.add("has-content");
    elements.copy.disabled = false;
  }
  renderEvidence(result);
  elements.iterations.textContent = String((result.plan || []).length);
  elements.artifactMetric.textContent = String((result.artifacts || []).length);
  elements.runSummary.hidden = false;
  const runIdentity = state.runIdentity || currentIdentity();
  saveRecentTask({
    user_id: runIdentity.user_id,
    thread_id: runIdentity.thread_id,
    title: state.taskTitle,
    status: result.status,
  });
  if (result.status === "interrupted") {
    setPaused();
  } else {
    const completed = result.status === "completed";
    const restoreApprovalFocus = state.approvalReturnFocus instanceof HTMLElement;
    state.approval = null;
    setRunning(false, statusLabel(result.status));
    setIdentityLocked(false);
    closeApproval(true);
    state.runIdentity = null;
    announce(completed ? "Task complete. The result is ready." : "The task needs review.");
    loadWorkspace();
    if (completed) loadMemories();
    if (!restoreApprovalFocus) {
      (finalAnswer ? elements.answerPanel : elements.task).focus({ preventScroll: true });
    }
  }
  loadRecentTasks();
}

function renderInterrupt(data) {
  setTaskView("active");
  state.approval = data;
  state.approvalReturnFocus =
    document.activeElement instanceof HTMLElement && document.activeElement !== document.body
      ? document.activeElement
      : elements.runButton;
  elements.approvalQuestion.textContent = data.question || "Allow this action?";
  elements.approvalDetails.textContent = JSON.stringify(data.details || {}, null, 2);
  setPaused();
  if (!elements.approval.open) elements.approval.showModal();
  const runIdentity = state.runIdentity || currentIdentity();
  saveRecentTask({
    user_id: runIdentity.user_id,
    thread_id: runIdentity.thread_id,
    title: state.taskTitle,
    status: "interrupted",
  });
  loadRecentTasks();
  announce("Your decision is needed before the task can continue.");
  elements.reject.focus();
}

function failRun(message) {
  setTaskView("active");
  const runIdentity = state.runIdentity || currentIdentity();
  saveRecentTask({
    user_id: runIdentity.user_id,
    thread_id: runIdentity.thread_id,
    title: state.taskTitle,
    status: "failed",
  });
  if (state.approval) {
    setPaused();
    if (!elements.approval.open) elements.approval.showModal();
  } else {
    setRunning(false, "Could not finish");
    setIdentityLocked(false);
    state.runIdentity = null;
  }
  showTaskError(message || "Atlas could not complete this task. You can try again.");
  if (!state.approval) elements.task.focus({ preventScroll: true });
  loadRecentTasks();
}

function handleEvent(event, data) {
  if (event === "stage") addStage(data);
  if (event === "token") appendToken(data.content || "");
  if (event === "interrupt") renderInterrupt(data);
  if (event === "result") renderResult(data);
  if (event === "error") failRun(data.message);
}

function parseStreamFrame(frame) {
  let event = "message";
  let rawData = "{}";
  frame.split("\n").forEach(function parseLine(line) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) rawData = line.slice(5).trim();
  });
  let data;
  try {
    data = JSON.parse(rawData);
  } catch {
    throw new Error("Atlas returned an unreadable progress update.");
  }
  handleEvent(event, data);
  return event;
}

async function streamRequest(url, payload) {
  clearTaskError();
  setRunning(true, "Working");
  announce("Task started.");
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    throw new Error("Request failed (" + String(response.status) + ")");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawTerminalEvent = false;
  function consumeFrame(frame) {
    if (!frame.trim()) return;
    const event = parseStreamFrame(frame);
    if (event === "interrupt" || event === "result" || event === "error") {
      sawTerminalEvent = true;
    }
  }
  while (true) {
    const part = await reader.read();
    buffer += decoder.decode(part.value || new Uint8Array(), { stream: !part.done });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    frames.forEach(consumeFrame);
    if (part.done) break;
  }
  consumeFrame(buffer);
  if (!sawTerminalEvent) {
    throw new Error("Atlas ended the task before returning a result. Please try again.");
  }
}

function taskTitle(task) {
  const value = task.replace(/\s+/g, " ").trim();
  return value.length > 72 ? value.slice(0, 69) + "…" : value;
}

async function launchTask(event) {
  event.preventDefault();
  if (state.running) return;
  if (state.modelConfigured !== true) {
    elements.setupNotice.hidden = false;
    announce("Setup is needed before a task can start.");
    return;
  }
  if (state.approval) {
    showToast("Review the waiting action before starting another task.", true);
    return;
  }
  const task = elements.task.value.trim();
  if (!task) return;
  if (!elements.thread.value.trim()) elements.thread.value = newThreadId();
  state.runIdentity = currentIdentity();
  state.taskTitle = taskTitle(task);
  persistIdentity(state.runIdentity);
  saveRecentTask({
    user_id: state.runIdentity.user_id,
    thread_id: state.runIdentity.thread_id,
    title: state.taskTitle,
    status: "running",
  });
  loadRecentTasks();
  setIdentityLocked(true);
  resetRun();
  try {
    await streamRequest("/api/chat/stream", {
      message: task,
      user_id: state.runIdentity.user_id,
      thread_id: state.runIdentity.thread_id,
    });
  } catch (error) {
    failRun(error.message || "Atlas could not be reached. Check the local service and retry.");
  }
}

async function resume(action) {
  if (!state.approval) return;
  const pending = state.approval;
  if (elements.approval.open) elements.approval.close();
  try {
    await streamRequest("/api/resume/stream", {
      user_id: (state.runIdentity || currentIdentity()).user_id,
      thread_id: (state.runIdentity || currentIdentity()).thread_id,
      response: {
        interrupt_id: pending.id,
        action: action,
        state_token: pending.details && pending.details.state_token
          ? pending.details.state_token
          : null,
        edited_arguments: null,
      },
    });
  } catch (error) {
    setPaused();
    if (!elements.approval.open) elements.approval.showModal();
    showTaskError(error.message || "Atlas could not continue. Your decision is still saved.");
    elements.reject.focus();
  }
}

function readRecentTasks() {
  try {
    const parsed = JSON.parse(safeStorageGet(RECENT_TASKS_KEY, "[]") || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(function validTask(item) {
      return (
        item &&
        typeof item.user_id === "string" &&
        typeof item.thread_id === "string" &&
        typeof item.title === "string" &&
        typeof item.updated_at === "string"
      );
    });
  } catch {
    return [];
  }
}

function writeRecentTasks(tasks) {
  if (!safeStorageSet(RECENT_TASKS_KEY, JSON.stringify(tasks.slice(0, 40)))) {
    showToast("Recent-task shortcuts could not be saved in this browser.", true);
  }
}

function saveRecentTask(task) {
  const existing = readRecentTasks();
  const previous = existing.find(function sameTask(item) {
    return item.user_id === task.user_id && item.thread_id === task.thread_id;
  });
  const record = {
    user_id: task.user_id,
    thread_id: task.thread_id,
    title: task.title || (previous && previous.title) || "Untitled task",
    status: task.status || (previous && previous.status) || "partial",
    updated_at: new Date().toISOString(),
  };
  const remaining = existing.filter(function differentTask(item) {
    return !(item.user_id === record.user_id && item.thread_id === record.thread_id);
  });
  const sameUser = [record]
    .concat(
      remaining.filter(function matchingUser(item) {
        return item.user_id === record.user_id;
      }),
    )
    .slice(0, MAX_RECENT_TASKS);
  const otherUsers = remaining.filter(function otherUser(item) {
    return item.user_id !== record.user_id;
  });
  writeRecentTasks(sameUser.concat(otherUsers));
}

function removeRecentTask(userId, threadId) {
  writeRecentTasks(
    readRecentTasks().filter(function keepTask(item) {
      return !(item.user_id === userId && item.thread_id === threadId);
    }),
  );
  loadRecentTasks();
  showToast("Removed from recent tasks.");
}

function clearRecentTasks() {
  const current = currentIdentity();
  const confirmed = window.confirm(
    "Remove all recent tasks for this profile? Durable task data and files will stay available.",
  );
  if (!confirmed) return;
  writeRecentTasks(
    readRecentTasks().filter(function keepOtherProfiles(item) {
      return item.user_id !== current.user_id;
    }),
  );
  loadRecentTasks();
  showToast("Recent tasks cleared. Your saved task data was not deleted.");
}

function formatDate(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.valueOf())) return "";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function loadRecentTasks() {
  const current = currentIdentity();
  const tasks = readRecentTasks()
    .filter(function currentUser(task) {
      return task.user_id === current.user_id;
    })
    .sort(function newest(a, b) {
      return b.updated_at.localeCompare(a.updated_at);
    })
    .slice(0, MAX_RECENT_TASKS);
  elements.recentTasks.replaceChildren();
  elements.clearRecentTasks.hidden = tasks.length === 0;
  if (!tasks.length) {
    elements.recentTasks.append(node("li", "Your latest work will appear here.", "sidebar-empty"));
    return;
  }
  tasks.forEach(function renderRecentTask(task) {
    const row = node("li", undefined, "recent-task-row");
    const open = node("button", undefined, "recent-task-button");
    open.type = "button";
    if (task.thread_id === current.thread_id) {
      row.classList.add("is-active");
      open.setAttribute("aria-current", "true");
    }
    const meta = node("span");
    meta.append(
      node("span", statusLabel(task.status), "recent-status status-" + task.status),
      document.createTextNode(formatDate(task.updated_at)),
    );
    open.append(node("strong", task.title), meta);
    open.addEventListener("click", function selectRecent() {
      restoreThread(task);
    });
    const remove = node("button", "×", "recent-task-remove");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + task.title + " from recent tasks");
    remove.addEventListener("click", function removeRecent() {
      removeRecentTask(task.user_id, task.thread_id);
    });
    row.append(open, remove);
    elements.recentTasks.append(row);
  });
}

function resultFromSnapshot(snapshot) {
  const values = snapshot.values || {};
  const interrupt = snapshot.interrupts && snapshot.interrupts[0];
  let status = "partial";
  if (interrupt) status = "interrupted";
  else if (values.review && values.review.complete) status = "completed";
  else if (!values.final_answer && !(values.plan || []).length) status = "failed";
  return {
    status: status,
    answer: values.final_answer || "",
    plan: values.plan || [],
    sources: values.sources || [],
    artifacts: values.artifacts || [],
    iterations: values.agent_iterations || 0,
    review_cycles: values.review_cycles || 0,
    interrupt: interrupt,
  };
}

async function restoreThread(recentTask) {
  if (state.running || state.approval) {
    showToast("Finish the current decision before opening another task.", true);
    return;
  }
  clearTaskError();
  persistIdentity({
    user_id: recentTask.user_id,
    thread_id: recentTask.thread_id,
  });
  state.taskTitle = recentTask.title;
  state.runIdentity = currentIdentity();
  resetRun();
  setRunning(true, "Opening task");
  setIdentityLocked(true);
  loadRecentTasks();
  const params = new URLSearchParams({ user_id: recentTask.user_id });
  try {
    const response = await fetch(
      "/api/threads/" + encodeURIComponent(recentTask.thread_id) + "?" + params.toString(),
    );
    if (!response.ok) throw new Error("Task could not be opened (" + response.status + ")");
    const snapshot = await response.json();
    state.taskTitle = (snapshot.values && snapshot.values.task) || recentTask.title;
    elements.task.value = state.taskTitle;
    const restored = resultFromSnapshot(snapshot);
    renderResult(restored);
    if (restored.interrupt) {
      state.runIdentity = currentIdentity();
      renderInterrupt(restored.interrupt);
    } else {
      setIdentityLocked(false);
      state.runIdentity = null;
      announce("Saved task opened.");
    }
  } catch (error) {
    setRunning(false, "Could not open task");
    setIdentityLocked(false);
    state.runIdentity = null;
    showTaskError(error.message || "This saved task could not be opened.");
  }
}

async function restorePausedThread() {
  const current = currentIdentity();
  const params = new URLSearchParams({ user_id: current.user_id });
  try {
    const response = await fetch(
      "/api/threads/" + encodeURIComponent(current.thread_id) + "?" + params.toString(),
    );
    if (!response.ok) return;
    const snapshot = await response.json();
    const pending = snapshot.interrupts && snapshot.interrupts[0];
    if (!pending) return;
    state.runIdentity = current;
    state.taskTitle = (snapshot.values && snapshot.values.task) || "Task awaiting review";
    renderPlan((snapshot.values && snapshot.values.plan) || []);
    addStage({ stage: "resume", message: "Opened the saved decision point." });
    renderInterrupt(pending);
  } catch {
    return;
  }
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return String(bytes) + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function setSelectedWorkspaceFile(path) {
  state.selectedFile = path;
  document.querySelectorAll(".workspace-file-button").forEach(function markSelection(button) {
    const selected = button.dataset.path === path && Boolean(path);
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function renderWorkspaceFiles(entries) {
  elements.workspaceFiles.replaceChildren();
  elements.workspaceFiles.className = "workspace-file-list";
  const files = (entries || []).filter(function fileOnly(entry) {
    return entry.type === "file";
  });
  if (!files.length) {
    emptyState(
      elements.workspaceFiles,
      "No files have been created yet.",
      "workspace-file-list muted",
    );
    return;
  }
  files.forEach(function renderFile(entry) {
    const row = node("li", undefined, "workspace-file-row");
    const open = node("button", undefined, "workspace-file-button");
    open.type = "button";
    open.dataset.path = entry.path;
    const selected = entry.path === state.selectedFile;
    open.classList.toggle("is-selected", selected);
    open.setAttribute("aria-pressed", String(selected));
    open.append(node("strong", entry.path), node("span", formatBytes(entry.bytes)));
    open.addEventListener("click", function previewFile() {
      openWorkspaceFile(entry.path);
    });
    const download = node("a", "Download", "file-download");
    download.href = workspaceUrl(entry.path, "download");
    download.setAttribute("download", "");
    download.setAttribute("aria-label", "Download " + entry.path);
    row.append(open, download);
    elements.workspaceFiles.append(row);
  });
  setSelectedWorkspaceFile(state.selectedFile);
}

async function loadWorkspace() {
  elements.refreshWorkspace.disabled = true;
  try {
    const params = new URLSearchParams({ pattern: "**/*", limit: "200" });
    const response = await fetch("/api/workspace?" + params.toString());
    if (!response.ok) throw new Error("Files are unavailable.");
    const listing = await response.json();
    renderWorkspaceFiles(listing.entries);
  } catch {
    emptyState(
      elements.workspaceFiles,
      "Files are unavailable. Refresh to try again.",
      "workspace-file-list muted",
    );
  } finally {
    elements.refreshWorkspace.disabled = false;
  }
}

async function openWorkspaceFile(path) {
  elements.workspacePreview.textContent = "Opening " + path + "…";
  elements.copyFile.disabled = true;
  try {
    const response = await fetch(workspaceUrl(path, "file"));
    if (!response.ok) {
      let detail = "";
      try {
        const errorBody = await response.json();
        detail = typeof errorBody.detail === "string" ? errorBody.detail : "";
      } catch {
        detail = "";
      }
      if (response.status === 404) {
        throw new Error("This file is no longer available. Refresh the file list to continue.");
      }
      if (response.status === 413) {
        throw new Error("This file is too large to preview here. Download it instead.");
      }
      if (response.status === 415) {
        throw new Error("This file cannot be previewed safely as text. Download it instead.");
      }
      throw new Error(detail || "This file could not be opened.");
    }
    const file = await response.json();
    setSelectedWorkspaceFile(file.path);
    state.selectedFileContent = file.content;
    elements.workspacePreview.textContent = file.content || "This file is empty.";
    elements.workspacePreview.setAttribute("aria-label", "Preview of " + file.path);
    elements.copyFile.disabled = false;
    elements.workspacePreview.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "nearest",
    });
    announce(file.path + " opened in the file preview.");
  } catch (error) {
    setSelectedWorkspaceFile("");
    state.selectedFileContent = "";
    elements.workspacePreview.textContent =
      error.message || "This file could not be previewed safely.";
    elements.workspacePreview.setAttribute("aria-label", "File preview error");
    announce("The file could not be previewed.");
  }
}

async function loadMemories() {
  const params = new URLSearchParams({ user_id: currentIdentity().user_id });
  try {
    const response = await fetch("/api/memories?" + params.toString());
    if (!response.ok) throw new Error();
    const memories = await response.json();
    elements.memoryList.replaceChildren();
    elements.memoryList.className = "memory-list";
    if (!memories.length) {
      emptyState(elements.memoryList, "No saved context yet.", "memory-list muted");
      return;
    }
    memories.slice(0, 8).forEach(function renderMemory(memory) {
      const card = node("li", undefined, "memory-card");
      const copy = node("div");
      copy.append(node("span", memory.category), node("p", memory.content));
      const remove = node("button", "Forget", "memory-remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "Forget saved context: " + memory.content);
      remove.addEventListener("click", function forgetMemory() {
        deleteMemory(memory.id);
      });
      card.append(copy, remove);
      elements.memoryList.append(card);
    });
  } catch {
    emptyState(elements.memoryList, "Saved context is unavailable.", "memory-list muted");
  }
}

async function deleteMemory(memoryId) {
  const params = new URLSearchParams({ user_id: currentIdentity().user_id });
  try {
    const response = await fetch(
      "/api/memories/" + encodeURIComponent(memoryId) + "?" + params.toString(),
      { method: "DELETE" },
    );
    if (!response.ok) throw new Error();
    showToast("Saved context removed.");
    loadMemories();
  } catch {
    showToast("Saved context could not be removed.", true);
  }
}

async function clearMemories() {
  if (!confirm("Clear all saved context for this local profile?")) return;
  const params = new URLSearchParams({ user_id: currentIdentity().user_id });
  try {
    const response = await fetch("/api/memories?" + params.toString(), { method: "DELETE" });
    if (!response.ok) throw new Error();
    const result = await response.json();
    showToast("Removed " + String(result.deleted) + " saved items.");
    loadMemories();
  } catch {
    showToast("Saved context could not be cleared.", true);
  }
}

function renderSetupGuidance(model) {
  if (!elements.setupMessage) return;
  const rawProvider = String(model || "").split(":", 1)[0] || "selected";
  const provider = rawProvider.toLowerCase();
  elements.setupMessage.replaceChildren();
  if (provider === "openai") {
    elements.setupMessage.append(
      document.createTextNode("Add "),
      node("code", "OPENAI_API_KEY"),
      document.createTextNode(
        " to your local .env file, then restart Atlas. Your credential stays local and is never shown here.",
      ),
    );
    return;
  }
  if (provider === "anthropic") {
    elements.setupMessage.append(
      document.createTextNode("Install the Anthropic extra, add "),
      node("code", "ANTHROPIC_API_KEY"),
      document.createTextNode(
        " to your local .env file, then restart Atlas. Your credential stays local and is never shown here.",
      ),
    );
    return;
  }
  elements.setupMessage.append(
    document.createTextNode(
      "Complete the " +
        rawProvider +
        " provider setup in your local .env file, then restart Atlas. Credentials stay local and are never shown here.",
    ),
  );
}

async function checkHealth() {
  const label = elements.health.querySelector("span");
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error();
    const health = await response.json();
    renderSetupGuidance(health.model);
    state.modelConfigured = health.model_configured;
    elements.health.className = health.model_configured
      ? "health-badge is-ready"
      : "health-badge is-setup";
    const healthLabel = health.model_configured ? "Ready" : "Setup needed";
    label.textContent = healthLabel;
    elements.health.setAttribute("aria-label", "Atlas status: " + healthLabel);
    elements.setupNotice.hidden = health.model_configured;
    if (elements.pythonCapability) {
      const description = elements.pythonCapability.querySelector("span");
      const unavailable = health.code_backend === "disabled";
      elements.pythonCapability.classList.toggle("is-unavailable", unavailable);
      elements.pythonCapability.dataset.availability = unavailable ? "unavailable" : "available";
      if (description) {
        description.textContent = unavailable
          ? "Unavailable until guarded Python is enabled."
          : "Run guarded data analysis.";
      }
    }
  } catch {
    renderSetupGuidance("");
    state.modelConfigured = false;
    elements.health.className = "health-badge is-error";
    label.textContent = "Service unavailable";
    elements.health.setAttribute("aria-label", "Atlas status: Service unavailable");
    elements.setupNotice.hidden = false;
  }
  setRunAvailability();
}

function themePreference() {
  return safeStorageGet(THEME_KEY, "system") || "system";
}

function applyTheme(preference) {
  const selected = preference || themePreference();
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = selected === "system" ? (systemDark ? "dark" : "light") : selected;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = selected;
  const label = elements.theme.querySelector("span");
  label.textContent = selected.charAt(0).toUpperCase() + selected.slice(1) + " theme";
  elements.theme.setAttribute(
    "aria-label",
    "Color theme: " + selected + ". Change theme",
  );
}

function cycleTheme() {
  const current = themePreference();
  const next = { system: "light", light: "dark", dark: "system" }[current] || "system";
  safeStorageSet(THEME_KEY, next);
  applyTheme(next);
  showToast(next.charAt(0).toUpperCase() + next.slice(1) + " theme selected.");
}

function connectDisclosure(disclosure) {
  disclosure.addEventListener("keydown", function closeWithEscape(event) {
    if (event.key !== "Escape" || !disclosure.open) return;
    event.preventDefault();
    event.stopPropagation();
    disclosure.open = false;
    const summary = disclosure.querySelector(":scope > summary");
    if (summary) summary.focus();
  });
}

function trapApprovalFocus(event) {
  if (event.key !== "Tab" || !elements.approval.open) return;
  const focusable = Array.from(
    elements.approval.querySelectorAll(
      "summary, button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
    ),
  ).filter(function visibleControl(control) {
    return control.getClientRects().length > 0;
  });
  if (!focusable.length) {
    event.preventDefault();
    elements.approval.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !elements.approval.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

async function copyText(content, successMessage) {
  try {
    await navigator.clipboard.writeText(content);
    showToast(successMessage);
  } catch {
    showToast("Copy failed. Select the text and copy it manually.", true);
  }
}

function startNewTask() {
  if (state.running || state.approval) {
    showToast("Finish the current task before starting a new one.", true);
    return;
  }
  elements.thread.value = newThreadId();
  persistIdentity();
  state.taskTitle = "";
  clearTaskError();
  resetRun();
  setTaskView("empty");
  setRunning(false, "Ready");
  loadRecentTasks();
  elements.task.value = "";
  elements.task.focus();
  announce("New task ready.");
}

elements.thread.value = safeStorageGet("atlas-thread", "") || newThreadId();
elements.user.value = safeStorageGet("atlas-user", "local-user") || "local-user";
persistIdentity();
applyTheme();
setRunAvailability();
elements.form.addEventListener("submit", launchTask);
elements.task.addEventListener("keydown", function submitShortcut(event) {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    elements.form.requestSubmit();
  }
});
elements.newThread.addEventListener("click", startNewTask);
elements.clearRecentTasks.addEventListener("click", clearRecentTasks);
elements.user.addEventListener("change", function changeUser() {
  persistIdentity();
  loadRecentTasks();
  loadMemories();
});
elements.thread.addEventListener("change", function changeThread() {
  persistIdentity();
  loadRecentTasks();
});
elements.approve.addEventListener("click", function approveAction() {
  resume("approve");
});
elements.reject.addEventListener("click", function rejectAction() {
  resume("reject");
});
elements.approval.addEventListener("cancel", function keepDecisionOpen(event) {
  event.preventDefault();
  announce("Choose Don’t allow or Allow once to continue.");
  elements.reject.focus();
});
elements.approval.addEventListener("keydown", trapApprovalFocus);
elements.refreshMemory.addEventListener("click", loadMemories);
elements.clearMemory.addEventListener("click", clearMemories);
elements.refreshWorkspace.addEventListener("click", loadWorkspace);
elements.theme.addEventListener("click", cycleTheme);
elements.copy.addEventListener("click", function copyResult() {
  copyText(state.answer, "Result copied.");
});
elements.copyFile.addEventListener("click", function copyFile() {
  copyText(state.selectedFileContent, (state.selectedFile || "File") + " copied.");
});
document.querySelectorAll("[data-example]").forEach(function connectExample(button) {
  button.addEventListener("click", function useExample() {
    elements.task.value = button.dataset.example;
    elements.task.focus();
  });
});
document
  .querySelectorAll(
    ".capability-disclosure, .utility-disclosure, .developer-settings, .setup-provider-details",
  )
  .forEach(connectDisclosure);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function systemTheme() {
  if (themePreference() === "system") applyTheme("system");
});

loadRecentTasks();
checkHealth();
loadMemories();
loadWorkspace();
restorePausedThread();
