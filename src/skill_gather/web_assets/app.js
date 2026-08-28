const stages = ["manifest", "media_extract", "frame_extract", "asr", "vision_ocr", "timeline_merge", "distill", "score", "package"];
const stageNames = {
  manifest: "元数据", media_extract: "媒体", frame_extract: "抽帧", asr: "ASR",
  vision_ocr: "视觉", timeline_merge: "证据合并", distill: "蒸馏", score: "评分", package: "打包"
};
const statusNames = { created: "排队中", running: "处理中", paused: "已暂停", completed: "已完成", failed: "失败", passed: "通过", needs_attention: "需要关注", warning: "警告", needs_review: "待复核" };
const difficultyNames = { lenient: "宽松", standard: "标准", strict: "严格", off: "关闭 Judge" };
const RUN_POLL_STATUSES = new Set(["created", "running"]);
const TOPIC_POLL_STATUSES = new Set(["awaiting_plan_confirmation", "processing_sources", "generating", "scoring"]);

const state = { runs: [], selectedId: null, selectedDetailVersion: null, timer: null };
const runPollState = { inFlight: false };
const topicState = { topics: [], selectedId: null, selectedDetailVersion: null };
const topicPollState = { inFlight: false };
let visibleResults = [];
const viewPanels = { operations: document.querySelector("#operations-view"), results: document.querySelector("#results-view") };
const runsList = document.querySelector("#runs-list");
const detailPanel = document.querySelector("#detail-panel");
const topicsList = document.querySelector("#topics-list");
const topicDetail = document.querySelector("#topic-detail");
const topicForm = document.querySelector("#topic-form");
const topicMessage = document.querySelector("#topic-message");
const form = document.querySelector("#video-form");
const formMessage = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const modelsButton = document.querySelector("#models-button");
const modelsPanel = document.querySelector("#models-panel");
const operationProgress = document.querySelector("#operation-progress");
const operationTitle = document.querySelector("#operation-title");
const operationDetail = document.querySelector("#operation-detail");
const operationMeterFill = document.querySelector("#operation-meter-fill");
const operationTime = document.querySelector("#operation-time");
const artifactPreview = document.querySelector("#artifact-preview");
const artifactPreviewTitle = document.querySelector("#artifact-preview-title");
const artifactPreviewMeta = document.querySelector("#artifact-preview-meta");
const artifactPreviewTabs = document.querySelector("#artifact-preview-tabs");
const artifactPreviewContent = document.querySelector("#artifact-preview-content");
const artifactDownloadCourse = document.querySelector("#artifact-download-course");
const artifactDownloadSkill = document.querySelector("#artifact-download-skill");
let operationStartedAt = 0;
let operationTimer = null;
let activeArtifact = null;

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function setOperation({ status = "running", title = "处理中", detail = "", progress = 12 } = {}) {
  if (!operationProgress) return;
  const wasIdle = operationProgress.classList.contains("idle") || operationProgress.classList.contains("finished") || operationProgress.classList.contains("failed");
  if (!operationStartedAt || (status === "running" && wasIdle)) operationStartedAt = Date.now();
  operationProgress.className = `operation-progress ${escapeHtml(status)}`;
  operationTitle.textContent = title;
  operationDetail.textContent = detail;
  operationMeterFill.style.width = `${Math.max(0, Math.min(100, Number(progress) || 0))}%`;
  const renderTime = () => { operationTime.textContent = `${Math.max(0, Math.round((Date.now() - operationStartedAt) / 1000))}s`; };
  renderTime();
  if (status === "running" && !operationTimer) operationTimer = window.setInterval(renderTime, 1000);
  if (status !== "running" && operationTimer) {
    window.clearInterval(operationTimer);
    operationTimer = null;
  }
}

function finishOperation(title, detail = "") {
  setOperation({ status: "finished", title, detail, progress: 100 });
  window.setTimeout(() => {
    operationProgress?.classList.add("idle");
    operationStartedAt = 0;
  }, 2200);
}

function failOperation(title, detail = "") {
  setOperation({ status: "failed", title, detail, progress: 100 });
}

function setView(view) {
  Object.entries(viewPanels).forEach(([name, panel]) => { if (panel) panel.hidden = name !== view; });
  document.querySelectorAll(".research-view").forEach(panel => { panel.hidden = view !== "research"; });
  document.querySelectorAll(".nav-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.view === view));
  if (view === "operations") loadMetrics();
  if (view === "results") loadResults();
}

async function loadMetrics() {
  const grid = document.querySelector("#metrics-grid");
  const queue = document.querySelector("#operations-list");
  if (!grid) return;
  try {
    const [data, work] = await Promise.all([request("/api/metrics"), request("/api/work-items")]);
    const statuses = Object.entries(data.by_status || {}).map(([key, value]) => `<div class="metric"><div class="metric-label">${escapeHtml(key)}</div><div class="metric-value">${Number(value) || 0}</div></div>`).join("");
    grid.innerHTML = `<div class="metric"><div class="metric-label">任务总数</div><div class="metric-value">${Number(data.total) || 0}</div></div><div class="metric"><div class="metric-label">活动作业</div><div class="metric-value">${Number(data.active_jobs) || 0}</div></div><div class="metric"><div class="metric-label">模型可用性</div><div class="metric-value small">${escapeHtml(data.model_availability || "unknown")}</div></div>${statuses}`;
    queue.innerHTML = (work.items || []).map(item => { const elapsed = item.usage?.elapsed_runtime_sec || 0; const maxRuntime = item.budget?.max_runtime_sec || 0; const canPause = !["completed", "failed", "paused"].includes(item.status); const canRetry = ["failed", "paused"].includes(item.status); const action = canPause ? `<button class="button secondary" data-operation="pause" data-kind="${escapeHtml(item.kind)}" data-id="${escapeHtml(item.run_id)}">暂停</button>` : canRetry ? `<button class="button secondary" data-operation="retry" data-kind="${escapeHtml(item.kind)}" data-id="${escapeHtml(item.run_id)}">重试</button>` : ""; return `<div class="run-item"><div class="run-top"><span class="run-meta">${escapeHtml(item.kind)} · ${escapeHtml(item.current_stage || item.status)}</span>${statusBadge(item.status)}</div><div class="run-title">${escapeHtml(item.topic || item.title || item.run_id)}</div><div class="run-meta">耗时 ${elapsed}s / ${maxRuntime || "-"}s${item.failure_reason ? ` · ${escapeHtml(item.failure_reason)}` : ""}</div>${action}</div>`; }).join("") || '<div class="loading">暂无任务。</div>';
    queue.querySelectorAll("[data-operation]").forEach(button => button.addEventListener("click", async () => { const group = button.dataset.kind === "topic" ? "topics" : "runs"; await request(`/api/${group}/${encodeURIComponent(button.dataset.id)}/${button.dataset.operation}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); await loadMetrics(); }));
  } catch (error) { grid.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`; }
}

async function loadResults() {
  const target = document.querySelector("#results-list");
  if (!target) return;
  target.innerHTML = '<div class="loading">正在加载结果...</div>';
  try {
    const params = new URLSearchParams({ type: document.querySelector("#results-type")?.value || "all", status: document.querySelector("#results-status")?.value || "all", min_score: document.querySelector("#results-score")?.value || "0", risk: document.querySelector("#results-risk")?.value || "", source: document.querySelector("#results-source")?.value || "" });
    const data = await request(`/api/results?${params}`);
    visibleResults = data.results || [];
    target.innerHTML = visibleResults.length ? visibleResults.map(item => `<div class="run-item"><div class="run-top"><label><input type="checkbox" data-result-id="${escapeHtml(item.run_id)}"> <span class="run-meta">${escapeHtml(item.kind)}</span></label>${statusBadge(item.result_status)}</div><button class="result-read" type="button" data-read-id="${escapeHtml(item.run_id)}">${escapeHtml(item.title || item.topic || item.run_id)}</button><div class="run-meta">评分 ${Number(item.score) || 0} · 风险 ${(item.risk_flags || []).length}</div><div class="run-meta">${escapeHtml(Object.values(item.artifacts || {}).filter(Boolean).join(" · "))}</div><button class="button secondary result-preview-action" type="button" data-preview-id="${escapeHtml(item.run_id)}">预览产物</button></div>`).join("") : '<div class="loading">暂无匹配结果。</div>';
    target.querySelectorAll("[data-read-id], [data-preview-id]").forEach(button => button.addEventListener("click", async () => openArtifactPreview(await request(`/api/results/${encodeURIComponent(button.dataset.readId || button.dataset.previewId)}`))));
  } catch (error) { target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`; }
}

window.addEventListener("workspace:view", event => setView(event.detail.view));
document.querySelectorAll(".workspace-tabs .nav-tab").forEach(tab => tab.addEventListener("click", () => setView(tab.dataset.view)));
document.querySelector("#metrics-refresh")?.addEventListener("click", loadMetrics);
["#results-type", "#results-status", "#results-score", "#results-risk", "#results-source"].forEach(selector => document.querySelector(selector)?.addEventListener("change", loadResults));
function selectedResultIds() { return [...document.querySelectorAll("[data-result-id]:checked")].map(item => item.dataset.resultId); }
function renderReadiness(result) {
  const panel = document.querySelector("#readiness-panel");
  const statusLabel = { passed: "全部通过", needs_attention: "需要关注", failed: "未开通完整功能" }[result.status] || result.status;
  const checks = result.checks || [];
  panel.hidden = false;
  panel.innerHTML = `
    <div class="readiness-head">
      <div><p class="eyebrow">REAL READINESS</p><h2>${escapeHtml(statusLabel)}</h2></div>
      ${statusBadge(result.status)}
    </div>
    <div class="readiness-summary">
      <span>通过 ${Number(result.summary?.passed) || 0}</span>
      <span>关注 ${Number(result.summary?.warning) || 0}</span>
      <span>失败 ${Number(result.summary?.failed) || 0}</span>
    </div>
    <div class="readiness-current">${escapeHtml(result.current || (result.status === "running" ? "正在检查..." : ""))}</div>
    <div class="readiness-grid">${checks.map(check => `
      <article class="readiness-item ${escapeHtml(check.status)}">
        <div class="run-top"><strong>${escapeHtml(check.label)}</strong>${statusBadge(check.status)}</div>
        <div class="run-meta">${escapeHtml(check.group)} · ${escapeHtml(check.summary || "")}</div>
        ${check.model ? `<div class="run-meta">模型 ${escapeHtml(check.model)}</div>` : ""}
        ${check.engine ? `<div class="run-meta">引擎 ${escapeHtml(check.engine)}</div>` : ""}
        ${check.error_code ? `<div class="run-meta">错误码 ${escapeHtml(check.error_code)}</div>` : ""}
      </article>
    `).join("")}</div>
    <div class="notice">${(result.notes || []).map(escapeHtml).join("<br>")}</div>`;
}

function renderReadinessJob(job) {
  const total = Number(job.total) || 0;
  const index = Number(job.index) || 0;
  const progress = Number(job.progress) || (total ? Math.round(index / total * 100) : 4);
  setOperation({
    status: job.status === "failed" ? "failed" : job.active ? "running" : "finished",
    title: "真实功能检查",
    detail: job.error || (job.current ? `${job.current}（${index}/${total}）` : "正在准备检查项"),
    progress,
  });
  renderReadiness({
    status: job.result?.status || (job.active ? "running" : "failed"),
    checks: job.checks || [],
    summary: job.result?.summary || {
      passed: (job.checks || []).filter(check => check.status === "passed").length,
      warning: (job.checks || []).filter(check => check.status === "warning").length,
      failed: (job.checks || []).filter(check => check.status === "failed").length,
    },
    current: job.current ? `当前进度：${job.current}（${index}/${total}）` : "正在准备检查项",
    notes: job.result?.notes || ["真实功能检查正在后台执行，完成后会显示最终状态。"],
  });
}

async function pollReadinessJob(jobId) {
  const job = await request(`/api/readiness-check/${encodeURIComponent(jobId)}`);
  renderReadinessJob(job);
  if (job.active) {
    await new Promise(resolve => window.setTimeout(resolve, 400));
    return pollReadinessJob(jobId);
  }
  if (job.status === "finished") {
    finishOperation("真实功能检查完成", job.result?.status === "passed" ? "全部检查通过" : "请查看分项结果");
    if (job.result) renderReadiness(job.result);
  } else {
    failOperation("真实功能检查失败", job.error || "请查看错误信息");
  }
  return job;
}

function updateRunOperation(run) {
  if (!run || !RUN_POLL_STATUSES.has(run.status)) return;
  setOperation({
    status: "running",
    title: "视频处理进行中",
    detail: `${stageNames[run.current_stage] || run.current_stage} · ${run.progress || 0}%`,
    progress: run.progress || 4,
  });
}

function updateTopicOperation(topic) {
  if (!topic || !TOPIC_POLL_STATUSES.has(topic.status)) return;
  const stageLabels = {
    awaiting_plan_confirmation: "等待语义确认",
    processing_sources: "处理已选来源",
    generating: "生成课程和 Skill",
    scoring: "评分与发布门槛",
  };
  setOperation({
    status: "running",
    title: "主题任务进行中",
    detail: stageLabels[topic.status] || topic.current_stage || topic.status,
    progress: topic.status === "awaiting_plan_confirmation" ? 18 : topic.status === "processing_sources" ? 42 : topic.status === "generating" ? 72 : 88,
  });
}

function renderResultDetails(items) {
  if (items.length === 1) {
    openArtifactPreview(items[0]);
    return;
  }
  const target = document.querySelector("#results-detail");
  target.innerHTML = items.map(item => {
    const course = item.course ? `<h3>人类课程</h3><pre>${escapeHtml(item.course)}</pre>` : "";
    const skill = item.skill ? `<h3>AI Skill</h3><pre>${escapeHtml(item.skill)}</pre>` : "";
    const fallback = !course && !skill ? `<pre>${escapeHtml(item.knowledge || JSON.stringify(item.evidence || item.fusion || {}, null, 2))}</pre>` : "";
    return `<article class="result-document"><h2>${escapeHtml(item.title)}</h2><div class="run-meta">${escapeHtml(item.kind)} · ${escapeHtml(item.score?.final_status || "")}</div>${course}${skill}${fallback}</article>`;
  }).join("");
}

function artifactDownloadUrl(runId, kind) {
  return `/api/results/${encodeURIComponent(runId)}/download/${kind}`;
}

function setArtifactPreviewMode(mode) {
  if (!activeArtifact || !artifactPreviewContent) return;
  const hasCourse = Boolean(activeArtifact.course);
  const hasSkill = Boolean(activeArtifact.skill);
  const hasPreviewTabs = hasCourse || hasSkill;
  const actualMode = mode === "course" && hasCourse ? "course" : mode === "skill" && hasSkill ? "skill" : hasCourse ? "course" : hasSkill ? "skill" : "summary";
  const content = actualMode === "course" ? activeArtifact.course : actualMode === "skill" ? activeArtifact.skill : activeArtifact.knowledge || JSON.stringify(activeArtifact.evidence || activeArtifact.fusion || {}, null, 2);
  artifactPreviewTitle.textContent = actualMode === "course" ? "给人看的课程" : actualMode === "skill" ? "Agent Skill" : "产物摘要";
  artifactPreviewMeta.textContent = `${activeArtifact.kind || "topic"} · ${activeArtifact.run_id || ""}`;
  artifactPreviewContent.textContent = content || "当前产物尚未生成。";
  artifactPreviewTabs.hidden = !hasPreviewTabs;
  document.querySelectorAll(".artifact-preview-tab").forEach(tab => {
    const active = tab.dataset.artifactMode === actualMode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.hidden = tab.dataset.artifactMode === "course" ? !hasCourse : !hasSkill;
  });
  artifactDownloadCourse.hidden = !hasCourse;
  artifactDownloadSkill.hidden = !hasSkill;
  if (hasCourse) artifactDownloadCourse.href = artifactDownloadUrl(activeArtifact.run_id, "course");
  if (hasSkill) artifactDownloadSkill.href = artifactDownloadUrl(activeArtifact.run_id, "skill");
}

function openArtifactPreview(item) {
  activeArtifact = item;
  if (!artifactPreview) return;
  artifactPreview.hidden = false;
  document.body.classList.add("modal-open");
  setArtifactPreviewMode(item.course ? "course" : "skill");
  document.querySelector("#artifact-preview-close")?.focus();
}

function closeArtifactPreview() {
  if (!artifactPreview) return;
  artifactPreview.hidden = true;
  document.body.classList.remove("modal-open");
  activeArtifact = null;
}

document.querySelectorAll(".artifact-preview-tab").forEach(tab => tab.addEventListener("click", () => setArtifactPreviewMode(tab.dataset.artifactMode)));
document.querySelector("#artifact-preview-close")?.addEventListener("click", closeArtifactPreview);
document.querySelector("#artifact-preview-backdrop")?.addEventListener("click", closeArtifactPreview);
document.addEventListener("keydown", event => { if (event.key === "Escape" && artifactPreview && !artifactPreview.hidden) closeArtifactPreview(); });
document.querySelector("#results-compare")?.addEventListener("click", async () => {
  const ids = selectedResultIds().slice(0, 3);
  if (ids.length < 2) return showToast("至少选择两个结果进行比较");
  renderResultDetails(await Promise.all(ids.map(id => request(`/api/results/${encodeURIComponent(id)}`))));
});
document.querySelector("#results-export")?.addEventListener("click", async () => {
  const selected = new Set([...document.querySelectorAll("[data-result-id]:checked")].map(item => item.dataset.resultId));
  const summaries = visibleResults.filter(item => selected.has(item.run_id));
  if (!summaries.length) return showToast("请先选择要导出的结果");
  const payload = await Promise.all(summaries.map(item => request(`/api/results/${encodeURIComponent(item.run_id)}`)));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
  link.download = "skill-seargen-results.json";
  link.click();
  URL.revokeObjectURL(link.href);
});

function displayVisionReason(reason) {
  if (reason === "newapi API key is not configured") return "未配置 NewAPI API key";
  return reason;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function currentApiKey() {
  return form.elements.api_key.value.trim();
}

function statusBadge(status) {
  return `<span class="status ${escapeHtml(status)}">${escapeHtml(statusNames[status] || status)}</span>`;
}

function canDeleteRun(run) {
  return !["created", "running"].includes(run.status) && !(run.job && run.job.active);
}

function renderModelGroup(title, models) {
  const items = models.length ? models.map(model => `<span class="model-chip">${escapeHtml(model)}</span>`).join("") : '<span class="muted">没有明显候选</span>';
  return `<div class="model-group"><div class="model-title">${escapeHtml(title)}</div><div class="model-chips">${items}</div></div>`;
}

function renderEmptyDetail() {
  detailPanel.innerHTML = `
    <div class="empty-state">
      <span class="empty-icon">◎</span>
      <h2>选择一条处理记录</h2>
      <p>状态、评分与证据摘要会显示在这里。</p>
    </div>`;
}

async function loadTopics(selectFirst = false) {
  if (!topicsList) return;
  try {
    const nextTopics = (await request("/api/topics")).topics || [];
    const changed = JSON.stringify(nextTopics) !== JSON.stringify(topicState.topics);
    topicState.topics = nextTopics;
    if (changed) renderTopics();
    if (topicState.topics.length && (selectFirst || !topicState.selectedId)) {
      selectTopic(topicState.topics[0].run_id);
    } else if (!topicState.topics.length) {
      topicState.selectedId = null;
      topicDetail.innerHTML = '<div class="muted">创建主题后，这里会显示候选来源。</div>';
    }
  } catch (error) {
    topicsList.innerHTML = `<div class="loading">${escapeHtml(error.message)}</div>`;
  }
}

function renderTopics() {
  if (!topicState.topics.length) {
    topicsList.innerHTML = '<div class="loading compact">还没有主题任务。</div>';
    return;
  }
  const labels = { created: "未搜索", planning: "规划中", awaiting_plan_confirmation: "待语义确认", searching: "搜索中", awaiting_selection: "待确认", processing_sources: "处理中", generating: "生成中", scoring: "评分中", paused: "已暂停", completed: "已完成", failed: "失败" };
  topicsList.innerHTML = topicState.topics.map(topic => `
    <div class="topic-item-row">
      <button class="topic-item ${topic.run_id === topicState.selectedId ? "active" : ""}" type="button" data-topic-id="${escapeHtml(topic.run_id)}">
        <div class="topic-item-title">${escapeHtml(topic.topic)}</div>
        <div class="topic-item-meta">${escapeHtml(topic.mode)} · ${escapeHtml(labels[topic.status] || topic.status)} · ${Number(topic.candidate_count) || 0} 条候选</div>
      </button>
      <button class="icon-button topic-delete-button" type="button" data-topic-delete="${escapeHtml(topic.run_id)}" title="归档主题" aria-label="归档主题">×</button>
    </div>`).join("");
  topicsList.querySelectorAll("[data-topic-id]").forEach(button => button.addEventListener("click", () => selectTopic(button.dataset.topicId)));
  topicsList.querySelectorAll("[data-topic-delete]").forEach(button => button.addEventListener("click", () => archiveTopic(button.dataset.topicDelete)));
}

async function archiveTopic(runId) {
  const topic = topicState.topics.find(item => item.run_id === runId);
  if (!topic) return;
  if (!window.confirm(`确认归档主题“${topic.topic}”吗？归档后会从列表移除，但文件仍保留在 .run-archive/topics 中。`)) return;
  try {
    await request(`/api/topics/${encodeURIComponent(runId)}`, { method: "DELETE" });
    const wasSelected = topicState.selectedId === runId;
    topicState.selectedId = wasSelected ? null : topicState.selectedId;
    await loadTopics(wasSelected);
    showToast("主题已归档");
  } catch (error) {
    showToast(error.message);
  }
}

async function selectTopic(runId) {
  topicState.selectedId = runId;
  topicState.selectedDetailVersion = null;
  renderTopics();
  topicDetail.innerHTML = '<div class="loading compact">正在读取主题...</div>';
  try {
    const topic = await request(`/api/topics/${encodeURIComponent(runId)}`);
    if (topicState.selectedId !== runId) return;
    topicState.selectedDetailVersion = topic.updated_at || "";
    renderTopic(topic);
  }
  catch (error) { topicDetail.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`; }
}

function renderTopic(topic) {
  const candidates = topic.candidates || [];
  const selectedSources = topic.selected_sources || [];
  const videoRuns = topic.video_runs || [];
  const job = topic.job || {};
  const fusion = topic.fusion || {};
  const fusionConclusions = fusion.conclusions || [];
  const fusionConflicts = fusion.conflicts || [];
  const fusionGaps = fusion.evidence_gaps || [];
  const budget = topic.budget || {};
  const plan = topic.plan || null;
  const planJob = topic.plan_job || {};
  const cache = topic.cache || {};
  const budgetSummary = `<span class="muted">预算：候选 ${Number(budget.max_candidates) || 0} · 处理来源 ${Number(budget.max_selected_sources) || 0} · 最长 ${Number(budget.max_runtime_sec) || 0} 秒 · 缓存 ${cache.reuse_cache ? "复用" : "关闭"}</span>`;
  const canSearch = topic.status === "created" || topic.status === "awaiting_selection";
  const searchButton = canSearch && topic.execution_mode !== "auto" ? `<button id="topic-search-button" class="button secondary" type="button">${topic.status === "created" ? "搜索候选" : "刷新搜索"}</button>` : "";
  const autoButton = topic.execution_mode === "auto" && canSearch ? '<button id="topic-auto-button" class="button primary" type="button">启动自动执行</button>' : "";
  const selectButton = topic.status === "awaiting_selection" && candidates.length ? '<button id="topic-select-button" class="button primary" type="button">确认选择</button>' : "";
  const retryButton = topic.status === "failed" ? '<button id="topic-retry-button" class="button secondary" type="button">恢复后重试</button>' : "";
  const processButton = topic.status === "processing_sources" && !job.active ? '<button id="topic-process-button" class="button primary" type="button">开始处理已选来源</button>' : "";
  const processControls = topic.status === "processing_sources" && !job.active ? `
    <label class="topic-inline-control">视觉模式<select id="topic-vision-mode"><option value="off">关闭</option><option value="sampled" selected>抽样</option><option value="full">全量</option></select></label>
    <label class="topic-inline-control">视觉帧数<input id="topic-vision-frame-limit" type="number" min="1" max="120" value="12"></label>` : "";
  const jobStatus = job.active ? `<span class="status running">${escapeHtml(job.status === "queued" ? "排队中" : "处理中")}</span>` : (job.error ? `<span class="notice compact-notice">${escapeHtml(job.error)}</span>` : "");
  const videoRunSummary = videoRuns.length ? `<div class="topic-video-runs"><strong>视频子 run</strong>${videoRuns.map(run => `<span>${escapeHtml(run.candidate_id)} · ${escapeHtml(run.status)}${run.vision_status ? ` · 视觉：${escapeHtml(run.vision_status)}${run.vision_reason ? `（${escapeHtml(displayVisionReason(run.vision_reason))}）` : ""}` : ""}${run.failure_reason ? ` · ${escapeHtml(run.failure_reason)}` : ""}</span>`).join("")}</div>` : "";
  const confirmation = topic.status === "processing_sources" ? `
    <section class="topic-confirmation" aria-live="polite">
      <div><strong>已确认 ${selectedSources.length} 条来源</strong><span>网页会生成知识总结，公开视频和 GitHub 仓库会写入主题证据。</span></div>
      <div class="topic-processing-controls">${processControls}${processButton}${jobStatus}</div>
      <ul>${selectedSources.map(source => `<li>${escapeHtml(source.title || source.url)}</li>`).join("")}</ul>
      ${videoRunSummary}
    </section>` : "";
  const outcome = ["completed", "failed"].includes(topic.status) ? `
    <section class="topic-confirmation topic-outcome" aria-live="polite">
      <div><strong>${topic.status === "completed" ? "主题处理完成" : "主题处理失败"}</strong><span>${escapeHtml(topic.status === "failed" ? (topic.failure_reason || job.error || "请查看处理审计文件。") : "处理产物已生成，请查看下方审计文件。")}</span></div>
      ${topic.artifacts?.knowledge ? `<span class="status completed">知识总结：${escapeHtml(topic.artifacts.knowledge)}</span>` : ""}
      ${topic.artifacts?.fusion ? `<span class="status completed">融合证据：${escapeHtml(topic.artifacts.fusion)}</span>` : ""}
      ${topic.artifacts?.video_processing_audit ? `<span class="status completed">视频审计：${escapeHtml(topic.artifacts.video_processing_audit)}</span>` : ""}
      ${topic.artifacts?.github_processing_audit ? `<span class="status completed">GitHub 审计：${escapeHtml(topic.artifacts.github_processing_audit)}</span>` : ""}
      ${topic.artifacts?.fusion ? `<div class="topic-video-runs"><strong>证据融合</strong><span>${fusionConclusions.length} 条结论 · ${fusionConflicts.length} 条冲突 · ${fusionGaps.length} 个缺口</span>${fusionConflicts.map(item => `<span>待复核：${escapeHtml(item.summary || "来源结论冲突")}</span>`).join("")}</div>` : ""}
      ${videoRunSummary}
    </section>` : "";
  const defaultPlanOption = plan?.options?.find(option => option.option_id === plan.recommended_option_id) || plan?.options?.[0];
  const artifactActions = ["completed", "failed"].includes(topic.status) && (topic.artifacts?.course || topic.artifacts?.skill) ? `<div class="topic-artifact-actions"><button id="topic-artifact-preview" class="button secondary" type="button">预览产物</button>${topic.artifacts?.course ? `<a class="button secondary" href="/api/results/${encodeURIComponent(topic.run_id)}/download/course" download>下载课程文档</a>` : ""}${topic.artifacts?.skill ? `<a class="button primary" href="/api/results/${encodeURIComponent(topic.run_id)}/download/skill" download>下载 Skill 包</a>` : ""}</div>` : "";
  const planPanel = topic.status === "awaiting_plan_confirmation" && plan ? `<section class="topic-confirmation"><div><strong>确认研究语义</strong><span>${escapeHtml((plan.ambiguity_reasons || []).join("；"))}${planJob.active ? " · LLM 规划中" : ""}</span>${planJob.active ? '<button id="plan-interrupt" class="button secondary" type="button">中断 LLM 规划</button>' : ""}</div><div class="candidate-list">${(plan.options || []).map(option => `<label class="candidate-card"><input type="radio" name="plan-option" value="${escapeHtml(option.option_id)}" ${option.option_id === plan.recommended_option_id ? "checked" : ""}><span><span class="candidate-title">${escapeHtml(option.label)}</span><span class="candidate-summary">${escapeHtml(option.goal)}</span><span class="candidate-meta">${escapeHtml((option.facets || []).join(" · "))}</span></span></label>`).join("")}</div><div class="plan-editor"><label>目标<input id="plan-goal" value="${escapeHtml(defaultPlanOption?.goal || "")}"></label><label>排除范围<input id="plan-exclusions" value="${escapeHtml((defaultPlanOption?.exclusions || []).join("；"))}"></label><label>研究维度<input id="plan-facets" value="${escapeHtml((defaultPlanOption?.facets || []).join("；"))}"></label><label>查询词<input id="plan-queries" value="${escapeHtml((defaultPlanOption?.queries || []).join("；"))}"></label><button id="plan-confirm" class="button primary" type="button">确认计划</button></div></section>` : "";
  topicDetail.innerHTML = `
    <div class="topic-actions">${searchButton}${autoButton}${selectButton}${retryButton}<span class="muted">${escapeHtml(topic.status)} · ${escapeHtml(topic.execution_mode || "manual")}</span>${budgetSummary}</div>
    ${planPanel}
    ${confirmation}
    ${outcome}
    ${artifactActions}
    ${candidates.length ? `<div class="candidate-list">${candidates.map(candidate => `
      <label class="candidate-card"><input type="checkbox" value="${escapeHtml(candidate.candidate_id)}" ${candidate.selected ? "checked" : ""} ${topic.status !== "awaiting_selection" ? "disabled" : ""}>
        <span><span class="candidate-title">${escapeHtml(candidate.title || candidate.url)}</span><span class="candidate-summary">${escapeHtml(candidate.summary || "暂无摘要")}</span><span class="candidate-meta">${escapeHtml(candidate.source_type)} · ${escapeHtml(candidate.host)} · ${escapeHtml((candidate.providers || []).join(", "))}</span><span class="candidate-meta">${escapeHtml(candidate.relevance_reason || "")}${(candidate.risk_flags || []).length ? ` · 风险：${escapeHtml(candidate.risk_flags.join(", "))}` : ""}</span></span>
        <span class="candidate-score">${Number(candidate.quality_score) || 0}</span>
      </label>`).join("")}</div>` : '<p class="muted">当前没有候选来源。</p>'}`;
  const searchButtonNode = document.querySelector("#topic-search-button");
  document.querySelector("#topic-artifact-preview")?.addEventListener("click", async () => openArtifactPreview(await request(`/api/results/${encodeURIComponent(topic.run_id)}`)));
  if (searchButtonNode) searchButtonNode.addEventListener("click", async () => {
    searchButtonNode.disabled = true;
    searchButtonNode.textContent = "搜索中...";
    try {
      await request(`/api/topics/${encodeURIComponent(topic.run_id)}/search`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fake: document.querySelector("#topic-fake")?.checked || false }) });
      await loadTopics();
      await selectTopic(topic.run_id);
    } catch (error) { topicMessage.textContent = error.message; topicMessage.className = "form-message error"; searchButtonNode.disabled = false; searchButtonNode.textContent = "搜索候选"; }
  });
  document.querySelector("#plan-confirm")?.addEventListener("click", async () => {
    try {
      const split = id => (document.querySelector(id)?.value || "").split("；").map(value => value.trim()).filter(Boolean);
      await request(`/api/topics/${encodeURIComponent(topic.run_id)}/plan/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ option_id: document.querySelector("input[name=plan-option]:checked")?.value || plan.recommended_option_id, edited: { goal: document.querySelector("#plan-goal")?.value || "", exclusions: split("#plan-exclusions"), facets: split("#plan-facets"), queries: split("#plan-queries") } }) });
      await loadTopics();
      await selectTopic(topic.run_id);
    } catch (error) { topicMessage.textContent = error.message; topicMessage.className = "form-message error"; }
  });
  document.querySelector("#plan-interrupt")?.addEventListener("click", async () => {
    await request(`/api/topics/${encodeURIComponent(topic.run_id)}/plan/interrupt`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    await selectTopic(topic.run_id);
  });
  const autoButtonNode = document.querySelector("#topic-auto-button");
  if (autoButtonNode) autoButtonNode.addEventListener("click", async () => {
    autoButtonNode.disabled = true;
    try {
      await request(`/api/topics/${encodeURIComponent(topic.run_id)}/auto`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fake: document.querySelector("#topic-fake")?.checked || false }) });
      await loadTopics();
      await selectTopic(topic.run_id);
    } catch (error) { topicMessage.textContent = error.message; topicMessage.className = "form-message error"; autoButtonNode.disabled = false; }
  });
  const selectButtonNode = document.querySelector("#topic-select-button");
  if (selectButtonNode) selectButtonNode.addEventListener("click", async () => {
    const candidateIds = [...topicDetail.querySelectorAll("input[type=checkbox]:checked")].map(input => input.value);
    try {
      await request(`/api/topics/${encodeURIComponent(topic.run_id)}/select`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ candidate_ids: candidateIds }) });
      await loadTopics();
      await selectTopic(topic.run_id);
    } catch (error) { topicMessage.textContent = error.message; topicMessage.className = "form-message error"; }
  });
  const retryButtonNode = document.querySelector("#topic-retry-button");
  if (retryButtonNode) retryButtonNode.addEventListener("click", async () => {
    retryButtonNode.disabled = true;
    retryButtonNode.textContent = "恢复中...";
    try {
      await request(`/api/topics/${encodeURIComponent(topic.run_id)}/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      await loadTopics();
      await selectTopic(topic.run_id);
    } catch (error) {
      topicMessage.textContent = error.message;
      topicMessage.className = "form-message error";
      retryButtonNode.disabled = false;
      retryButtonNode.textContent = "恢复后重试";
    }
  });
  const processButtonNode = document.querySelector("#topic-process-button");
  if (processButtonNode) processButtonNode.addEventListener("click", async () => {
    processButtonNode.disabled = true;
    processButtonNode.textContent = "已加入处理队列";
    try {
      await request(`/api/topics/${encodeURIComponent(topic.run_id)}/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vision_mode: document.querySelector("#topic-vision-mode")?.value || "sampled",
          vision_frame_limit: Number(document.querySelector("#topic-vision-frame-limit")?.value || 12),
        })
      });
      await loadTopics();
      await selectTopic(topic.run_id);
    } catch (error) { topicMessage.textContent = error.message; topicMessage.className = "form-message error"; processButtonNode.disabled = false; processButtonNode.textContent = "开始处理已选来源"; }
  });
}

function renderModels(payload) {
  const suggestions = payload.suggestions || {};
  const asrHint = (suggestions.asr || []).length ? "" : '<div class="models-total">ASR 是必需本地能力：请在 config.json 里把 asr_model 设为 faster-whisper:base 或 faster-whisper:&lt;本地模型路径&gt;。</div>';
  modelsPanel.hidden = false;
  modelsPanel.innerHTML = `
    ${renderModelGroup("ASR 候选", suggestions.asr || [])}
    ${asrHint}
    ${renderModelGroup("视觉候选", suggestions.vision || [])}
    ${renderModelGroup("文本候选", suggestions.text || [])}
    <div class="models-total">共 ${Number((payload.models || []).length) || 0} 个模型。候选按模型名粗分，请以 IceAPI 后台渠道说明为准。</div>
  `;
}

async function loadRuns(selectFirst = false) {
  if (!state.runs.length) runsList.innerHTML = '<div class="loading">正在读取处理记录...</div>';
  try {
    const nextRuns = (await request("/api/runs")).runs || [];
    const changed = JSON.stringify(nextRuns) !== JSON.stringify(state.runs);
    state.runs = nextRuns;
    if (changed) renderRuns();
    if (state.runs.length && (selectFirst || !state.selectedId)) {
      selectRun(state.runs[0].run_id);
    } else if (!state.runs.length) {
      state.selectedId = null;
      renderEmptyDetail();
    }
  } catch (error) {
    runsList.innerHTML = `<div class="loading">${escapeHtml(error.message)}</div>`;
  }
}

function renderRuns() {
  if (!state.runs.length) {
    runsList.innerHTML = '<div class="loading">还没有处理记录。</div>';
    return;
  }
  runsList.innerHTML = state.runs.map(run => `
    <button class="run-item ${run.run_id === state.selectedId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}" type="button">
      <div class="run-top"><span class="run-meta">${escapeHtml(run.source_id)}</span>${statusBadge(run.status)}</div>
      <div class="run-title">${escapeHtml(run.title)}</div>
      <div class="run-meta">${escapeHtml(stageNames[run.current_stage] || run.current_stage)} · ${run.progress}%</div>
      <div class="progress"><span style="width:${Number(run.progress) || 0}%"></span></div>
    </button>`).join("");
  runsList.querySelectorAll("[data-run-id]").forEach(button => button.addEventListener("click", () => selectRun(button.dataset.runId)));
}

async function selectRun(runId) {
  state.selectedId = runId;
  state.selectedDetailVersion = null;
  renderRuns();
  detailPanel.innerHTML = '<div class="loading">正在读取详情...</div>';
  try {
    const run = await request(`/api/runs/${encodeURIComponent(runId)}`);
    if (state.selectedId !== runId) return;
    state.selectedDetailVersion = run.updated_at || "";
    renderDetail(run);
  } catch (error) {
    detailPanel.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  }
}

function renderDetail(run) {
  const score = run.score || {};
  const scoreStatus = score.final_status || "created";
  const evidence = run.evidence || [];
  const completed = new Set(run.completed_stages || []);
  const risks = run.risk_flags || [];
  const jobError = run.job && run.job.error;
  const deleteLabel = run.status === "failed" ? "清理失败产物" : "清理此 run";
  const deleteDisabled = canDeleteRun(run) ? "" : "disabled";
  const deleteTitle = canDeleteRun(run) ? "清理这条 run 及其产物" : "运行中的任务暂时不能清理";
  detailPanel.innerHTML = `
    <div class="detail-title-row">
      <div>
        <p class="eyebrow">${escapeHtml(run.source_id)}</p>
        <h2 class="detail-title">${escapeHtml(run.title)}</h2>
        <div class="detail-subtitle">${escapeHtml(run.author || run.url || run.run_id)}</div>
      </div>
      <div class="detail-actions">
        <button id="cleanup-button" class="button secondary danger" type="button" ${deleteDisabled} title="${escapeHtml(deleteTitle)}">${escapeHtml(deleteLabel)}</button>
        ${statusBadge(run.status)}
      </div>
    </div>
    ${(run.failure_reason || jobError) ? `<div class="content-section notice">${escapeHtml(jobError || run.failure_reason)}</div>` : ""}
    <div class="detail-grid">
      <div class="metric"><div class="metric-label">最终评分</div><div class="metric-row"><div class="metric-value">${Number(score.final_score) || 0}</div>${statusBadge(scoreStatus)}</div></div>
      <div class="metric"><div class="metric-label">规则 / Judge</div><div class="metric-value small">${Number(score.rule_score) || 0} / ${Number(score.llm_judge_score) || 0}</div></div>
      <div class="metric"><div class="metric-label">证据 / 难度</div><div class="metric-value small">${evidence.length} / ${escapeHtml(difficultyNames[run.judge_difficulty] || run.judge_difficulty || "标准")}</div></div>
    </div>
    <section class="content-section">
      <h3>处理阶段</h3>
      <div class="stage-list">${stages.map(stage => `<div class="stage ${completed.has(stage) ? "done" : ""}">${escapeHtml(stageNames[stage])}</div>`).join("")}</div>
    </section>
    <section class="content-section">
      <h3>证据摘要 <span class="muted">· ${escapeHtml(run.evidence_meta.sampling_strategy || "尚未生成")}</span></h3>
      ${evidence.length ? `<div class="evidence-list">${evidence.slice(0, 12).map(item => `
        <div class="evidence-item">
          <span class="evidence-time">${escapeHtml(item.timestamp)}</span>
          <span class="evidence-type">${escapeHtml(item.type)}</span>
          <span>${escapeHtml(item.claim)}</span>
          <span class="confidence">${Math.round((Number(item.confidence) || 0) * 100)}%</span>
        </div>`).join("")}</div>` : '<p class="muted">当前没有可展示的证据。</p>'}
    </section>
    <section class="content-section">
      <h3>风险标记</h3>
      ${risks.length ? `<div class="risk-list">${risks.map(risk => `<span class="risk">${escapeHtml(risk)}</span>`).join("")}</div>` : '<p class="muted">未记录风险。</p>'}
    </section>`;

  const cleanupButton = document.querySelector("#cleanup-button");
  if (cleanupButton && canDeleteRun(run)) {
    cleanupButton.addEventListener("click", async () => {
      const message = run.status === "failed"
        ? "确定清理这条失败 run 及其全部产物吗？"
        : "确定删除这条 run 及其全部产物吗？";
      if (!window.confirm(message)) return;
      cleanupButton.disabled = true;
      cleanupButton.textContent = "清理中...";
      try {
        await request(`/api/runs/${encodeURIComponent(run.run_id)}`, { method: "DELETE" });
        state.selectedId = null;
        await loadRuns(true);
        showToast("已清理完成");
      } catch (error) {
        showToast(error.message);
      } finally {
        cleanupButton.disabled = false;
        cleanupButton.textContent = deleteLabel;
      }
    });
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  submitButton.disabled = true;
  setOperation({ title: "创建视频任务", detail: "正在提交 URL 和配置", progress: 8 });
  formMessage.className = "form-message";
  formMessage.textContent = "正在创建任务...";
  try {
    const result = await request("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: form.elements.url.value,
        api_key: currentApiKey(),
        judge_difficulty: form.elements.judge_difficulty.value,
        execution_mode: form.elements.execution_mode.value
      })
    });
    form.elements.url.value = "";
    formMessage.textContent = "任务已创建，处理将在后台继续。";
    state.selectedId = result.run_id;
    setOperation({ title: "视频任务已创建", detail: `run ${result.run_id} 已进入后台队列`, progress: 18 });
    await loadRuns();
    await selectRun(result.run_id);
  } catch (error) {
    formMessage.className = "form-message error";
    formMessage.textContent = error.message;
    failOperation("创建视频任务失败", error.message);
  } finally {
    submitButton.disabled = false;
  }
});

topicForm?.addEventListener("submit", async event => {
  event.preventDefault();
  setOperation({ title: "创建主题任务", detail: "正在保存主题和预算", progress: 8 });
  topicMessage.className = "form-message";
  topicMessage.textContent = "正在创建主题...";
  try {
    const result = await request("/api/topics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ topic: topicForm.elements.topic.value, mode: topicForm.elements.mode.value, execution_mode: topicForm.elements.execution_mode.value }) });
    topicForm.elements.topic.value = "";
    topicMessage.textContent = "主题已创建，请在右侧启动搜索。";
    topicState.selectedId = result.run_id;
    finishOperation("主题任务已创建", result.run_id);
    await loadTopics();
    await selectTopic(result.run_id);
  } catch (error) { topicMessage.className = "form-message error"; topicMessage.textContent = error.message; failOperation("创建主题任务失败", error.message); }
});

document.querySelector("#refresh-button").addEventListener("click", async () => {
  await loadRuns();
  if (state.selectedId) await selectRun(state.selectedId);
});

loadTopics();

modelsButton.addEventListener("click", async () => {
  modelsButton.disabled = true;
  setOperation({ title: "查询模型列表", detail: "正在请求 NewAPI /models", progress: 20 });
  modelsButton.textContent = "查询中...";
  modelsPanel.hidden = false;
  modelsPanel.innerHTML = '<div class="loading compact">正在查询 IceAPI 模型...</div>';
  try {
    renderModels(await request("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: currentApiKey() })
    }));
    finishOperation("模型列表已返回", "候选模型已刷新");
  } catch (error) {
    modelsPanel.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
    failOperation("查询模型失败", error.message);
  } finally {
    modelsButton.disabled = false;
    modelsButton.textContent = "查询模型";
  }
});

document.querySelector("#check-button").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  setOperation({ title: "运行 MVP 自检", detail: "正在执行离线 smoke check", progress: 18 });
  button.textContent = "自检中...";
  try {
    const result = await request("/api/mvp-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: currentApiKey() })
    });
    showToast(result.status === "passed" ? "MVP 自检通过" : "MVP 自检未通过");
    finishOperation("MVP 自检完成", result.status === "passed" ? "通过" : "未通过");
  } catch (error) {
    showToast(error.message);
    failOperation("MVP 自检失败", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "运行自检";
  }
});

document.querySelector("#readiness-button").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  setOperation({ title: "真实功能检查", detail: "正在创建后台检查任务", progress: 4 });
  button.textContent = "检查中...";
  try {
    const job = await request("/api/readiness-check/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: currentApiKey(), load_asr_model: true })
    });
    renderReadinessJob(job);
    const finalJob = await pollReadinessJob(job.job_id);
    const result = finalJob.result || {};
    showToast(result.status === "passed" ? "真实功能检查通过" : "真实功能仍有未开通项");
  } catch (error) {
    showToast(error.message);
    failOperation("真实功能检查失败", error.message);
  } finally {
    button.disabled = false;
    button.textContent = "检查真实功能";
  }
});

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2800);
}

async function poll() {
  if (!state.selectedId) return;
  if (runPollState.inFlight) return;
  const current = state.runs.find(run => run.run_id === state.selectedId);
  if (!current || !RUN_POLL_STATUSES.has(current.status)) return;

  runPollState.inFlight = true;
  try {
    await loadRuns();
    const latest = state.runs.find(run => run.run_id === state.selectedId);
    updateRunOperation(latest);
    if (latest && (latest.updated_at || "") !== state.selectedDetailVersion) {
      const runId = state.selectedId;
      const run = await request(`/api/runs/${encodeURIComponent(runId)}`);
      if (state.selectedId === runId) {
        state.selectedDetailVersion = run.updated_at || "";
        renderDetail(run);
      }
    }
    if (latest && !RUN_POLL_STATUSES.has(latest.status)) {
      if (latest.status === "completed") finishOperation("视频任务完成", "结果已生成");
      if (latest.status === "failed") failOperation("视频任务失败", latest.failure_reason || latest.job?.error || "请查看任务详情");
    }
  } finally {
    runPollState.inFlight = false;
  }
}

async function pollTopics() {
  if (!topicState.selectedId) return;
  if (topicPollState.inFlight) return;
  const current = topicState.topics.find(topic => topic.run_id === topicState.selectedId);
  if (!current || !TOPIC_POLL_STATUSES.has(current.status)) return;

  topicPollState.inFlight = true;
  try {
    await loadTopics();
    const latest = topicState.topics.find(topic => topic.run_id === topicState.selectedId);
    updateTopicOperation(latest);
    if (latest && (latest.updated_at || "") !== topicState.selectedDetailVersion) {
      const runId = topicState.selectedId;
      const topic = await request(`/api/topics/${encodeURIComponent(runId)}`);
      if (topicState.selectedId === runId) {
        topicState.selectedDetailVersion = topic.updated_at || "";
        renderTopic(topic);
      }
    }
    if (latest && !TOPIC_POLL_STATUSES.has(latest.status)) {
      if (latest.status === "completed") finishOperation("主题任务完成", "课程和证据已生成");
      if (latest.status === "failed") failOperation("主题任务失败", latest.failure_reason || "请查看主题详情");
    }
  } finally {
    topicPollState.inFlight = false;
  }
}

loadRuns(true);
loadMetrics();
state.timer = window.setInterval(poll, 3000);
window.setInterval(pollTopics, 3000);
