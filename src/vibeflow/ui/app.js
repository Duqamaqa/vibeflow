"use strict";

const state = {
  repo: "",
  bootstrap: null,
  task: null,
  pollTimer: null,
  activeTab: "summary",
  selectedSkills: new Set(),
};

const elements = {
  repoInput: document.querySelector("#repo-input"),
  browseRepo: document.querySelector("#browse-repo"),
  loadRepo: document.querySelector("#load-repo"),
  setupRepo: document.querySelector("#setup-repo"),
  setupRepoInline: document.querySelector("#setup-repo-inline"),
  repoSetupNotice: document.querySelector("#repo-setup-notice"),
  repoSetupTitle: document.querySelector("#repo-setup-title"),
  repoSetupCopy: document.querySelector("#repo-setup-copy"),
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
  engineList: document.querySelector("#engine-list"),
  skillList: document.querySelector("#skill-list"),
  emptySkills: document.querySelector("#empty-skills"),
  skillCount: document.querySelector("#skill-count"),
  selectedSkillCount: document.querySelector("#selected-skill-count"),
  skillErrors: document.querySelector("#skill-errors"),
  importSkill: document.querySelector("#import-skill"),
  createSkill: document.querySelector("#create-skill"),
  skillDialog: document.querySelector("#skill-dialog"),
  skillForm: document.querySelector("#skill-form"),
  closeSkillDialog: document.querySelector("#close-skill-dialog"),
  cancelSkill: document.querySelector("#cancel-skill"),
  skillName: document.querySelector("#skill-name"),
  skillDescription: document.querySelector("#skill-description"),
  skillTriggers: document.querySelector("#skill-triggers"),
  skillCost: document.querySelector("#skill-cost"),
  skillRisk: document.querySelector("#skill-risk"),
  skillInstructions: document.querySelector("#skill-instructions"),
  statusBadge: document.querySelector("#task-status-badge"),
  taskAlert: document.querySelector("#task-alert"),
  taskAlertKicker: document.querySelector("#task-alert-kicker"),
  taskAlertTitle: document.querySelector("#task-alert-title"),
  taskAlertReason: document.querySelector("#task-alert-reason"),
  taskAlertAction: document.querySelector("#task-alert-action"),
  viewTaskDetails: document.querySelector("#view-task-details"),
  pipelinePanel: document.querySelector("#pipeline"),
  pipelineSteps: [...document.querySelectorAll("#pipeline-steps li")],
  taskComplete: document.querySelector("#task-complete"),
  taskCompleteTitle: document.querySelector("#task-complete-title"),
  taskCompleteSummary: document.querySelector("#task-complete-summary"),
  taskCompleteMeta: document.querySelector("#task-complete-meta"),
  viewFinishedOutput: document.querySelector("#view-finished-output"),
  evidencePanel: document.querySelector("#evidence"),
  evidenceKicker: document.querySelector("#evidence-kicker"),
  evidenceTitle: document.querySelector("#evidence-title"),
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
  for (const control of [
    elements.planButton,
    elements.runButton,
    elements.repoInput,
    elements.browseRepo,
    elements.loadRepo,
    elements.setupRepo,
    elements.setupRepoInline,
    elements.importSkill,
    elements.createSkill,
  ]) {
    control.disabled = busy;
  }
}

async function loadBootstrap(repo = "") {
  const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";
  setSystemLoading();
  try {
    const payload = await api(`/api/bootstrap${query}`);
    const repositoryChanged = Boolean(state.repo && state.repo !== payload.repo_root);
    if (repositoryChanged) {
      state.selectedSkills.clear();
      state.task = null;
      resetEvidence();
    }
    state.bootstrap = payload;
    state.repo = payload.repo_root;
    elements.repoInput.value = payload.repo_root;
    elements.footerRepo.textContent = payload.repo_name.toUpperCase();
    renderSystem(payload);
    renderModels(payload.routing);
    renderEngines(payload.engines || []);
    renderSkills(payload.skills || { items: [], errors: [] });
    renderRepositorySetup(payload);
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

function repositoryNeedsSetup(payload = state.bootstrap) {
  return Boolean(payload && (payload.setup_required || !(payload.git && payload.git.is_repository)));
}

function renderRepositorySetup(payload) {
  const needsSetup = repositoryNeedsSetup(payload);
  const gitReady = payload.git && payload.git.is_repository;
  elements.setupRepo.hidden = !needsSetup;
  elements.repoSetupNotice.hidden = !needsSetup;
  const label = gitReady ? "Set up Vibeflow" : "Prepare this folder";
  elements.setupRepo.textContent = label;
  elements.setupRepoInline.textContent = label;
  if (!needsSetup) return;
  if (gitReady) {
    elements.repoSetupTitle.textContent = "Add Vibeflow settings before your first task.";
    elements.repoSetupCopy.textContent = "This creates the missing .ai configuration files only. Existing project files are not overwritten.";
  } else {
    elements.repoSetupTitle.textContent = "Prepare this folder before your first task.";
    elements.repoSetupCopy.textContent = "Vibeflow will create local Git tracking and its .ai settings. It will not commit, push, publish, or change your existing project files.";
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

function renderEngines(engines) {
  elements.engineList.replaceChildren();
  for (const engine of engines) {
    const card = document.createElement("article");
    card.className = `engine-card ${engine.activation || "always-on"}`;
    const heading = document.createElement("div");
    heading.className = "engine-card-heading";
    const name = document.createElement("strong");
    name.textContent = engine.name;
    const status = document.createElement("span");
    status.className = "engine-status";
    status.textContent = engine.activation === "automatic" ? "AUTO" : "ON";
    const description = document.createElement("p");
    description.textContent = engine.description;
    const timing = document.createElement("small");
    timing.textContent = `Used: ${engine.when}`;
    heading.append(name, status);
    card.append(heading, description, timing);
    elements.engineList.append(card);
  }
}

function renderSkills(skillState) {
  const skills = Array.isArray(skillState.items) ? skillState.items : [];
  const available = new Set(skills.map((skill) => skill.name));
  state.selectedSkills = new Set([...state.selectedSkills].filter((name) => available.has(name)));
  elements.skillList.replaceChildren();
  elements.emptySkills.hidden = skills.length > 0;
  elements.skillCount.textContent = `${skills.length} prompt skill${skills.length === 1 ? "" : "s"} available`;
  updateSelectedSkillCount();
  const errors = Array.isArray(skillState.errors) ? skillState.errors : [];
  elements.skillErrors.hidden = errors.length === 0;
  elements.skillErrors.textContent = errors.length ? `Some skills could not be loaded: ${errors.join(" · ")}` : "";

  for (const skill of skills) {
    const card = document.createElement("article");
    card.className = `skill-card${state.selectedSkills.has(skill.name) ? " selected" : ""}`;
    const checkbox = document.createElement("input");
    const checkboxId = `skill-${skill.name.replace(/[^A-Za-z0-9_-]/g, "-")}`;
    checkbox.id = checkboxId;
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedSkills.has(skill.name);
    const label = document.createElement("label");
    label.className = "skill-toggle";
    label.htmlFor = checkboxId;
    const check = document.createElement("span");
    check.className = "skill-check";
    check.textContent = "✓";
    const copy = document.createElement("span");
    copy.className = "skill-copy";
    const name = document.createElement("strong");
    name.textContent = skill.name;
    const description = document.createElement("p");
    description.textContent = skill.description;
    const tags = document.createElement("span");
    tags.className = "skill-tags";
    for (const value of [...(skill.triggers || []).slice(0, 3), `${skill.risk || "low"} risk`]) {
      const tag = document.createElement("span");
      tag.className = "skill-tag";
      tag.textContent = value;
      tags.append(tag);
    }
    copy.append(name, description, tags);
    label.append(check, copy);
    const remove = document.createElement("button");
    remove.className = "skill-remove";
    remove.type = "button";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${skill.name}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedSkills.add(skill.name);
      else state.selectedSkills.delete(skill.name);
      card.classList.toggle("selected", checkbox.checked);
      updateSelectedSkillCount();
    });
    card.addEventListener("click", (event) => {
      if (event.target.closest("button, label, input")) return;
      checkbox.click();
    });
    remove.addEventListener("click", () => removeSkill(skill.name));
    card.append(checkbox, label, remove);
    elements.skillList.append(card);
  }
}

function updateSelectedSkillCount() {
  const count = state.selectedSkills.size;
  elements.selectedSkillCount.textContent = `${count} selected for next prompt`;
}

async function submitTask(action) {
  if (repositoryNeedsSetup()) {
    elements.repoSetupNotice.scrollIntoView({ behavior: "smooth", block: "center" });
    showToast("Prepare this folder before running a task.", true);
    return;
  }
  const prompt = elements.promptInput.value.trim();
  if (!prompt) {
    elements.promptInput.focus();
    showToast("Write a coding request first.", true);
    return;
  }
  setBusy(true);
  resetPipeline();
  elements.statusBadge.textContent = action === "plan" ? "starting plan" : "starting safely";
  elements.statusBadge.className = "status-pill running";
  markStep(elements.pipelineSteps[0], "active", "•••");
  window.requestAnimationFrame(() => {
    elements.pipelinePanel.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  try {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        action,
        prompt,
        repo: state.repo || elements.repoInput.value.trim(),
        approved: elements.approvalInput.checked,
        skills: [...state.selectedSkills],
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
  elements.taskAlert.hidden = true;
  elements.taskAlert.className = "task-alert";
  elements.taskComplete.hidden = true;
  for (const step of elements.pipelineSteps) {
    step.className = "";
    step.querySelector(".step-state").textContent = "—";
  }
}

function resetEvidence() {
  elements.emptyEvidence.hidden = false;
  elements.evidenceContent.hidden = true;
  elements.copyOutput.disabled = true;
  elements.evidenceKicker.textContent = "FINISHED TASK OUTPUT";
  elements.evidenceTitle.textContent = "Your result, files, checks, and code";
  resetPipeline();
}

function renderTask(task) {
  const status = task.status || "queued";
  elements.statusBadge.textContent = status === "blocked" ? "stopped safely" : status.replaceAll("-", " ");
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
  if (success) {
    const finishIndex = planned ? 3 : 7;
    elements.pipelineSteps.forEach((step, index) => {
      if (index < finishIndex) markStep(step, "complete", "✓");
    });
    showTaskComplete(task);
  } else {
    const failure = explainTaskFailure(task);
    elements.pipelineSteps.forEach((step, index) => {
      if (index < failure.stageIndex) markStep(step, "complete", "✓");
      else if (index === failure.stageIndex) markStep(step, "failed", "!");
    });
    showTaskAlert(status, failure);
  }
  renderEvidence(task.result, task.error, task);
}

function showTaskComplete(task) {
  const data = task.result || {};
  const resolution = data.resolution || {};
  const worker = resolution.worker || {};
  const review = resolution.review || {};
  const changedFiles = worker.changed_files || [];
  const planned = task.action === "plan" || task.status === "planned";
  elements.taskComplete.hidden = false;
  elements.taskCompleteTitle.textContent = planned
    ? "Plan finished. Your output is ready."
    : "Task finished. Your output is ready.";
  elements.taskCompleteSummary.textContent = worker.summary || review.feedback || data.summary || (planned
    ? "Vibeflow prepared the contract and route without changing files."
    : "Vibeflow completed, verified, reviewed, and safely applied the task.");
  elements.taskCompleteMeta.textContent = planned
    ? "No files changed · plan only"
    : `${changedFiles.length} changed file${changedFiles.length === 1 ? "" : "s"} · verification ${resolution.verification?.accepted === true ? "passed" : "reported"} · review ${review.approved === true ? "passed" : "reported"}`;
}

function taskFailureText(task) {
  const data = task.result || {};
  const resolution = data.resolution || {};
  const worker = resolution.worker || {};
  const review = resolution.review || {};
  return task.error || data.blocker || resolution.blocker || review.feedback || worker.summary || "Vibeflow stopped before it could safely finish the task.";
}

function explainTaskFailure(task) {
  const status = task.status || "blocked";
  const data = task.result || {};
  const resolution = data.resolution || {};
  const worker = resolution.worker || {};
  const review = resolution.review || {};
  const verification = resolution.verification || {};
  const reason = taskFailureText(task);
  const normalized = reason.toLowerCase();

  if (status === "needs-approval") {
    return { stageIndex: 0, reason, action: "Review the requested scope, enable the approval checkbox near your prompt, and run the task again." };
  }
  if (normalized.includes("live web research") || normalized.includes("browser/research backend")) {
    return {
      stageIndex: 1,
      reason,
      action: "Vibeflow cannot safely invent prospects or contact details. First provide a verified business list, then ask it to build one website prototype per task. Live research will require a connected browser backend.",
      kind: "capability",
    };
  }
  if (normalized.includes("routing config is missing") || normalized.includes(".ai/routing.toml")) {
    return { stageIndex: 1, reason, action: "Use Prepare this folder near the prompt. Vibeflow will create the missing local setup, then you can retry." };
  }
  if (normalized.includes("fcc") || normalized.includes("model") || normalized.includes("provider") || normalized.includes("route")) {
    return { stageIndex: 1, reason, action: "Check that FCC is running and that the configured model provider is connected, then retry." };
  }
  if (normalized.includes("worktree") || normalized.includes("workspace") || normalized.includes("git repository") || normalized.includes("isolate")) {
    return { stageIndex: 2, reason, action: "Confirm the selected folder is an accessible Git repository, then retry." };
  }
  if (worker.success === false || normalized.includes("structured proposal") || normalized.includes("invalid json") || normalized.includes("worker")) {
    return { stageIndex: 3, reason, action: "The coding model did not return a safe structured change. Retry or let routing escalate to a stronger model." };
  }
  if (verification.accepted === false || /\b(test|tests|lint|typecheck|verification|build)\b/.test(normalized)) {
    return { stageIndex: 4, reason, action: "Open the task details to see which deterministic check failed, then ask Vibeflow to fix that failure." };
  }
  if (review.approved === false || normalized.includes("review") || normalized.includes("resolver") || normalized.includes("iteration")) {
    return { stageIndex: 5, reason, action: "The independent reviewer rejected the change and the repair limit was reached. Use its feedback in a new prompt." };
  }
  if (normalized.includes("promot") || normalized.includes("apply") || normalized.includes("hash") || normalized.includes("dirty") || normalized.includes("stale")) {
    return { stageIndex: 6, reason, action: "Your project changed while Vibeflow was working, so safe apply was cancelled. Review local changes and retry." };
  }
  return { stageIndex: 1, reason, action: "Open the full details below for the exact technical report. Your project was not changed." };
}

function showTaskAlert(status, failure) {
  const approval = status === "needs-approval";
  const capability = failure.kind === "capability";
  elements.taskAlert.hidden = false;
  elements.taskAlert.classList.toggle("needs-approval", approval);
  elements.taskAlertKicker.textContent = approval ? "YOUR APPROVAL IS NEEDED" : capability ? "RESEARCH CONNECTION NEEDED" : "STOPPED SAFELY";
  elements.taskAlertTitle.textContent = approval
    ? "Vibeflow paused before making changes."
    : capability
      ? "Live web research is not connected."
      : "Vibeflow did not apply unverified changes.";
  elements.taskAlertReason.textContent = failure.reason;
  elements.taskAlertAction.textContent = `Next step: ${failure.action}`;
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
  const stopped = task.status === "blocked";
  const approval = task.status === "needs-approval";
  elements.evidenceKicker.textContent = approval ? "TASK PAUSED" : stopped ? "TASK STOPPED SAFELY" : "FINISHED TASK OUTPUT";
  elements.evidenceTitle.textContent = approval
    ? "Approval is needed before anything changes"
    : stopped
      ? "Why Vibeflow stopped and what to do next"
      : "Your result, files, checks, and code";

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
  addSummarySection("Outcome", error || data.blocker || resolution.blocker || worker.summary || review.feedback || (task.action === "plan" ? "Plan prepared without changing files." : "Task completed."));
  if (typeof review.approved === "boolean") {
    addSummarySection("Reviewer", reviewState + (review.feedback ? ` — ${review.feedback}` : ""));
  }
  addSummarySection("Skills", (data.skills || task.selected_skills || []).join(", ") || "No reusable skills selected.");
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

async function browseRepository() {
  setBusy(true);
  showToast("Opening the folder chooser…");
  try {
    const result = await api("/api/picker", {
      method: "POST",
      body: JSON.stringify({ purpose: "repository", current: state.repo }),
    });
    if (result.selected) {
      await loadBootstrap(result.path);
      showToast("Repository selected.");
    } else {
      showToast("Folder selection cancelled.");
    }
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function setupRepository() {
  setBusy(true);
  try {
    const initializeGit = !(state.bootstrap && state.bootstrap.git && state.bootstrap.git.is_repository);
    const result = await api("/api/repositories/init", {
      method: "POST",
      body: JSON.stringify({ repo: state.repo, initialize_git: initializeGit }),
    });
    await loadBootstrap(state.repo);
    showToast(result.git_initialized ? "Git and Vibeflow setup are ready." : result.status === "created" ? "Repository is ready for Vibeflow." : "Repository was already ready.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function importSkill() {
  setBusy(true);
  showToast("Choose a folder containing SKILL.md…");
  try {
    const picked = await api("/api/picker", {
      method: "POST",
      body: JSON.stringify({ purpose: "skill", current: state.repo }),
    });
    if (!picked.selected) {
      showToast("Skill import cancelled.");
      return;
    }
    const result = await api("/api/skills/import", {
      method: "POST",
      body: JSON.stringify({ repo: state.repo, source: picked.path }),
    });
    await loadBootstrap(state.repo);
    state.selectedSkills.add(result.skill.name);
    renderSkills(state.bootstrap.skills);
    showToast(`${result.skill.name} imported and selected.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function removeSkill(name) {
  if (!window.confirm(`Remove the ${name} skill from this repository?`)) return;
  setBusy(true);
  try {
    await api("/api/skills/remove", {
      method: "POST",
      body: JSON.stringify({ repo: state.repo, name }),
    });
    state.selectedSkills.delete(name);
    await loadBootstrap(state.repo);
    showToast(`${name} removed.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

function openSkillDialog() {
  elements.skillForm.reset();
  if (typeof elements.skillDialog.showModal === "function") elements.skillDialog.showModal();
  else elements.skillDialog.setAttribute("open", "");
  window.setTimeout(() => elements.skillName.focus(), 0);
}

function closeSkillDialog() {
  if (typeof elements.skillDialog.close === "function") elements.skillDialog.close();
  else elements.skillDialog.removeAttribute("open");
}

async function createSkill(event) {
  event.preventDefault();
  const triggers = elements.skillTriggers.value.split(",").map((value) => value.trim()).filter(Boolean);
  setBusy(true);
  try {
    const result = await api("/api/skills/create", {
      method: "POST",
      body: JSON.stringify({
        repo: state.repo,
        name: elements.skillName.value.trim(),
        description: elements.skillDescription.value.trim(),
        triggers,
        instructions: elements.skillInstructions.value,
        cost: elements.skillCost.value,
        risk: elements.skillRisk.value,
      }),
    });
    closeSkillDialog();
    await loadBootstrap(state.repo);
    state.selectedSkills.add(result.skill.name);
    renderSkills(state.bootstrap.skills);
    showToast(`${result.skill.name} created and selected.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
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
elements.browseRepo.addEventListener("click", browseRepository);
elements.setupRepo.addEventListener("click", setupRepository);
elements.setupRepoInline.addEventListener("click", setupRepository);
elements.repoInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadBootstrap(elements.repoInput.value.trim());
});
elements.refresh.addEventListener("click", () => loadBootstrap(state.repo));
elements.importSkill.addEventListener("click", importSkill);
elements.createSkill.addEventListener("click", openSkillDialog);
elements.skillForm.addEventListener("submit", createSkill);
elements.closeSkillDialog.addEventListener("click", closeSkillDialog);
elements.cancelSkill.addEventListener("click", closeSkillDialog);
elements.viewTaskDetails.addEventListener("click", () => {
  document.querySelector("#evidence").scrollIntoView({ behavior: "smooth" });
});
elements.viewFinishedOutput.addEventListener("click", () => {
  switchTab("summary");
  elements.evidencePanel.scrollIntoView({ behavior: "smooth", block: "start" });
});
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
