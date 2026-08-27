"use strict";

const state = {
  repo: "",
  bootstrap: null,
  task: null,
  pollTimer: null,
  activeTab: "summary",
};

const elements = {
  repoInput: document.querySelector("#repo-input"),
  loadRepo: document.querySelector("#load-repo"),
  refresh: document.querySelector("#refresh-button"),
  promptForm: document.querySelector("#prompt-form"),
  promptInput: document.querySelector("#prompt-input"),
  approvalInput: document.querySelector("#approval-input"),
  planButton: document.querySelector("#plan-button"),
  runButton: document.querySelector("#run-button"),
  characterCount: document.querySelector("#character-count"),
  systemDot: document.querySelector("#system-dot"),
  systemLabel: document.querySelector("#system-label"),
  systemDetail: document.querySelector("#system-detail"),
  gitBadge: document.querySelector("#git-badge"),
  modelCards: document.querySelector("#model-cards"),
  statusBadge: document.querySelector("#task-status-badge"),
  pipelineSteps: [...document.querySelectorAll("#pipeline-steps li")],
  emptyEvidence: document.querySelector("#empty-evidence"),
  evidenceContent: document.querySelector("#evidence-content"),
  summaryTab: document.querySelector("#summary-tab"),
  diffTab: document.querySelector("#diff-tab"),
  rawTab: document.querySelector("#raw-tab"),
  tabs: [...document.querySelectorAll(".tab")],
  copyOutput: document.querySelector("#copy-output"),
  footerRepo: document.querySelector("#footer-repo"),
  toast: document.querySelector("#toast"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`Vibeflow returned HTTP ${response.status}`);
  }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function showToast(message, isError = false) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => elements.toast.classList.remove("show"), 2800);
}

function setBusy(busy) {
  elements.planButton.disabled = busy;
  elements.runButton.disabled = busy;
  elements.repoInput.disabled = busy;
  elements.loadRepo.disabled = busy;
}

async function loadBootstrap(repo = "") {
  const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";
  setSystemLoading();
  try {
    const payload = await api(`/api/bootstrap${query}`);
    state.bootstrap = payload;
    state.repo = payload.repo_root;
    elements.repoInput.value = payload.repo_root;
    elements.footerRepo.textContent = payload.repo_name.toUpperCase();
    renderSystem(payload);
    renderModels(payload.routing);
    const liveTask = payload.tasks.find((task) => ["queued", "running"].includes(task.status));
    if (liveTask) {
      state.task = liveTask;
      renderTask(liveTask);
      startPolling(liveTask.task_id);
    } else if (!state.task && payload.last_task) {
      renderPersistedTask(payload.last_task);
    }
  } catch (error) {
    elements.systemDot.className = "status-dot bad";
    elements.systemLabel.textContent = "Repository unavailable";
    elements.systemDetail.textContent = error.message;
    showToast(error.message, true);
  }
}

function setSystemLoading() {
  elements.systemDot.className = "status-dot loading";
  elements.systemLabel.textContent = "Checking local services…";
  elements.systemDetail.textContent = "FCC · Git · routing";
}

function renderSystem(payload) {
  const gitReady = payload.git && payload.git.is_repository;
  const routingReady = payload.routing && !payload.routing.error;
  const allGood = payload.fcc.healthy && gitReady && routingReady;
  elements.systemDot.className = `status-dot ${allGood ? "good" : "bad"}`;
  elements.systemLabel.textContent = allGood ? "Ready for autonomous work" : "Setup needs attention";
  const parts = [payload.fcc.healthy ? "FCC online" : "FCC offline", gitReady ? "Git ready" : "Not a Git repo", routingReady ? "Route ready" : "No route"];
  elements.systemDetail.textContent = parts.join(" · ");
  if (!gitReady) {
    elements.gitBadge.textContent = "Git · unavailable";
    elements.gitBadge.className = "quiet-badge dirty";
  } else if (payload.git.dirty) {
    elements.gitBadge.textContent = `Git · ${payload.git.entries.length} dirty`;
    elements.gitBadge.className = "quiet-badge dirty";
  } else {
    elements.gitBadge.textContent = "Git · clean";
    elements.gitBadge.className = "quiet-badge clean";
  }
}

function renderModels(routing) {
  const labels = {
    cheap: ["Mechanical", "Starts here"],
    standard: ["Builder", "Escalation 1"],
    strong: ["CTO / Review", "Final authority"],
  };
  elements.modelCards.replaceChildren();
  for (const tier of ["cheap", "standard", "strong"]) {
    const card = document.createElement("article");
    card.className = "model-card";
    card.dataset.tier = tier;
    const header = document.createElement("div");
    header.className = "model-card-header";
    const tierLabel = document.createElement("span");
    tierLabel.className = "model-tier";
    tierLabel.textContent = `${tier} / ${labels[tier][0]}`;
    const role = document.createElement("span");
    role.className = "model-role";
    role.textContent = labels[tier][1];
    const name = document.createElement("p");
    name.className = "model-name";
    name.title = routing.tiers[tier] || "Not configured";
    name.textContent = routing.tiers[tier] || "Not configured";
    header.append(tierLabel, role);
    card.append(header, name);
    elements.modelCards.append(card);
  }
}

async function submitTask(action) {
  const prompt = elements.promptInput.value.trim();
  if (!prompt) {
    elements.promptInput.focus();
    showToast("Write a coding request first.", true);
    return;
  }
  setBusy(true);
  resetPipeline();
  try {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        action,
        prompt,
        repo: state.repo || elements.repoInput.value.trim(),
        approved: elements.approvalInput.checked,
      }),
    });
    state.task = task;
    renderTask(task);
    startPolling(task.task_id);
    showToast(action === "plan" ? "Plan started." : "Autonomous task started.");
  } catch (error) {
    setBusy(false);
    showToast(error.message, true);
  }
}

function startPolling(taskId) {
  window.clearTimeout(state.pollTimer);
  const poll = async () => {
    try {
      const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
      state.task = task;
      renderTask(task);
      if (["queued", "running"].includes(task.status)) {
        state.pollTimer = window.setTimeout(poll, 900);
      } else {
        setBusy(false);
        loadBootstrap(state.repo);
      }
    } catch (error) {
      setBusy(false);
      showToast(error.message, true);
    }
  };
  state.pollTimer = window.setTimeout(poll, 350);
}

function resetPipeline() {
  for (const step of elements.pipelineSteps) {
    step.className = "";
    step.querySelector(".step-state").textContent = "—";
  }
}

function renderTask(task) {
  const status = task.status || "queued";
  elements.statusBadge.textContent = status.replaceAll("-", " ");
  elements.statusBadge.className = `status-pill ${status}`;
  resetPipeline();
  if (["queued", "running"].includes(status)) {
    const activeIndex = status === "queued" ? 0 : 3;
    elements.pipelineSteps.forEach((step, index) => {
      if (index < activeIndex) markStep(step, "complete", "✓");
      else if (index === activeIndex) markStep(step, "active", "•••");
    });
    return;
  }
  const planned = task.action === "plan" || status === "planned";
  const success = status === "done" || status === "planned";
  const finishIndex = planned ? 2 : 7;
  elements.pipelineSteps.forEach((step, index) => {
    if (index < finishIndex) markStep(step, success ? "complete" : "failed", success ? "✓" : "!");
  });
  if (!success && finishIndex < elements.pipelineSteps.length) markStep(elements.pipelineSteps[finishIndex], "failed", "!");
  renderEvidence(task.result, task.error, task);
}

function renderPersistedTask(lastTask) {
  const result = lastTask.result || lastTask;
  const task = {
    action: "run",
    status: lastTask.status || result.status || "done",
    result,
    error: lastTask.error || null,
  };
  state.task = task;
  renderTask(task);
}

function markStep(step, className, symbol) {
  step.className = className;
  step.querySelector(".step-state").textContent = symbol;
}

function renderEvidence(result, error, task) {
  elements.emptyEvidence.hidden = true;
  elements.evidenceContent.hidden = false;
  elements.copyOutput.disabled = false;
  const data = result || {};
  const resolution = data.resolution || {};
  const worker = resolution.worker || {};
  const review = resolution.review || {};
  const verification = resolution.verification || {};
  const routing = data.routing || resolution.routing || {};
  const changedFiles = worker.changed_files || [];
  const duration = data.duration_seconds == null ? "—" : `${Number(data.duration_seconds).toFixed(2)}s`;
  const verificationState = verification.accepted === true ? "Passed" : verification.accepted === false ? "Failed" : task.action === "plan" ? "Not run" : "—";
  const reviewState = review.approved === true ? "Passed" : review.approved === false ? "Failed" : task.action === "plan" ? "Not run" : "—";

  const grid = document.createElement("div");
  grid.className = "summary-grid";
  const stats = [
    ["Status", task.status || data.status || "—"],
    ["Route", routing.tier || routing.model || "—"],
    ["Verification", verificationState],
    ["Duration", duration],
  ];
  for (const [label, value] of stats) {
    const item = document.createElement("div");
    item.className = "summary-stat";
    const small = document.createElement("span");
    small.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = String(value).replaceAll("-", " ");
    item.append(small, strong);
    grid.append(item);
  }

  elements.summaryTab.replaceChildren(grid);
  addSummarySection("Outcome", error || data.blocker || worker.summary || review.feedback || (task.action === "plan" ? "Plan prepared without changing files." : "Task completed."));
  addSummarySection("Reviewer", reviewState + (review.feedback ? ` — ${review.feedback}` : ""));
  if (changedFiles.length) addFilesSection(changedFiles);
  const contract = data.contract;
  if (contract && contract.goal) addSummarySection("Contract", contract.goal);

  elements.diffTab.textContent = worker.diff || "No diff was generated for this task.";
  elements.rawTab.textContent = JSON.stringify(task, null, 2);
  switchTab(state.activeTab);
}

function addSummarySection(title, text) {
  const section = document.createElement("section");
  section.className = "summary-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const body = document.createElement("p");
  body.textContent = text || "—";
  section.append(heading, body);
  elements.summaryTab.append(section);
}

function addFilesSection(files) {
  const section = document.createElement("section");
  section.className = "summary-section";
  const heading = document.createElement("h3");
  heading.textContent = `Changed files (${files.length})`;
  const chips = document.createElement("div");
  chips.className = "file-chips";
  for (const file of files) {
    const chip = document.createElement("span");
    chip.className = "file-chip";
    chip.textContent = file;
    chips.append(chip);
  }
  section.append(heading, chips);
  elements.summaryTab.append(section);
}

function switchTab(tabName) {
  state.activeTab = tabName;
  elements.tabs.forEach((tab) => {
    const active = tab.dataset.tab === tabName;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  elements.summaryTab.hidden = tabName !== "summary";
  elements.diffTab.hidden = tabName !== "diff";
  elements.rawTab.hidden = tabName !== "raw";
}

elements.promptForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitTask("run");
});
elements.planButton.addEventListener("click", () => submitTask("plan"));
elements.promptInput.addEventListener("input", () => {
  elements.characterCount.textContent = `${elements.promptInput.value.length.toLocaleString()} / 20,000`;
});
elements.promptInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    submitTask("run");
  }
});
elements.loadRepo.addEventListener("click", () => loadBootstrap(elements.repoInput.value.trim()));
elements.repoInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadBootstrap(elements.repoInput.value.trim());
});
elements.refresh.addEventListener("click", () => loadBootstrap(state.repo));
elements.tabs.forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
elements.copyOutput.addEventListener("click", async () => {
  if (!state.task) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(state.task, null, 2));
    showToast("Task summary copied.");
  } catch {
    showToast("Clipboard access was unavailable.", true);
  }
});
document.querySelectorAll("[data-scroll]").forEach((button) => {
  button.addEventListener("click", () => document.querySelector(`#${button.dataset.scroll}`).scrollIntoView({ behavior: "smooth" }));
});

loadBootstrap();
