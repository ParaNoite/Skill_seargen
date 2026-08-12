const stages = ["manifest", "media_extract", "frame_extract", "asr", "vision_ocr", "timeline_merge", "distill", "score", "package"];
const stageNames = {
  manifest: "元数据", media_extract: "媒体", frame_extract: "抽帧", asr: "ASR",
  vision_ocr: "视觉", timeline_merge: "证据合并", distill: "蒸馏", score: "评分", package: "打包"
};
const statusNames = { created: "排队中", running: "处理中", paused: "已暂停", completed: "已完成", failed: "失败", passed: "通过", needs_review: "待复核" };
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

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
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
    target.innerHTML = visibleResults.length ? visibleResults.map(item => `<div class="run-item"><div class="run-top"><label><input type="checkbox" data-result-id="${escapeHtml(item.run_id)}"> <span class="run-meta">${escapeHtml(item.kind)}</span></label>${statusBadge(item.result_status)}</div><button class="result-read" type="button" data-read-id="${escapeHtml(item.run_id)}">${escapeHtml(item.title || item.topic || item.run_id)}</button><div class="run-meta">评分 ${Number(item.score) || 0} · 风险 ${(item.risk_flags || []).length}</div><div class="run-meta">${escapeHtml(Object.values(item.artifacts || {}).filter(Boolean).join(" · "))}</div></div>`).join("") : '<div class="loading">暂无匹配结果。</div>';
    target.querySelectorAll("[data-read-id]").forEach(button => button.addEventListener("click", async () => renderResultDetails([await request(`/api/results/${encodeURIComponent(button.dataset.readId)}`)])));
  } catch (error) { target.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`; }
}

window.addEventListener("workspace:view", event => setView(event.detail.view));
document.querySelectorAll(".workspace-tabs .nav-tab").forEach(tab => tab.addEventListener("click", () => setView(tab.dataset.view)));
document.querySelector("#metrics-refresh")?.addEventListener("click", loadMetrics);
["#results-type", "#results-status", "#results-score", "#results-risk", "#results-source"].forEach(selector => document.querySelector(selector)?.addEventListener("change", loadResults));
function selectedResultIds() { return [...document.querySelectorAll("[data-result-id]:checked")].map(item => item.dataset.resultId); }
function renderResultDetails(items) {
  const target = document.querySelector("#results-detail");
  target.innerHTML = items.map(item => `<article class="result-document"><h2>${escapeHtml(item.title)}</h2><div class="run-meta">${escapeHtml(item.kind)} · ${escapeHtml(item.score?.final_status || "")}</div><pre>${escapeHtml(item.skill || item.knowledge || JSON.stringify(item.evidence || item.fusion || {}, null, 2))}</pre></article>`).join("");
}
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
    <button class="topic-item ${topic.run_id === topicState.selectedId ? "active" : ""}" type="button" data-topic-id="${escapeHtml(topic.run_id)}">
      <div class="topic-item-title">${escapeHtml(topic.topic)}</div>
      <div class="topic-item-meta">${escapeHtml(topic.mode)} · ${escapeHtml(labels[topic.status] || topic.status)} · ${Number(topic.candidate_count) || 0} 条候选</div>
    </button>`).join("");
  topicsList.querySelectorAll("[data-topic-id]").forEach(button => button.addEventListener("click", () => selectTopic(button.dataset.topicId)));
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
  const searchButton = topic.status === "created" || topic.status === "awaiting_selection" ? `<button id="topic-search-button" class="button secondary" type="button">${topic.status === "created" ? "搜索候选" : "刷新搜索"}</button>` : "";
  const autoButton = topic.execution_mode === "auto" && topic.status === "created" ? '<button id="topic-auto-button" class="button primary" type="button">启动自动执行</button>' : "";
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
  const planPanel = topic.status === "awaiting_plan_confirmation" && plan ? `<section class="topic-confirmation"><div><strong>确认研究语义</strong><span>${escapeHtml((plan.ambiguity_reasons || []).join("；"))}${planJob.active ? " · LLM 规划中" : ""}</span>${planJob.active ? '<button id="plan-interrupt" class="button secondary" type="button">中断 LLM 规划</button>' : ""}</div><div class="candidate-list">${(plan.options || []).map(option => `<label class="candidate-card"><input type="radio" name="plan-option" value="${escapeHtml(option.option_id)}" ${option.option_id === plan.recommended_option_id ? "checked" : ""}><span><span class="candidate-title">${escapeHtml(option.label)}</span><span class="candidate-summary">${escapeHtml(option.goal)}</span><span class="candidate-meta">${escapeHtml((option.facets || []).join(" · "))}</span></span></label>`).join("")}</div><div class="plan-editor"><label>目标<input id="plan-goal" value="${escapeHtml(defaultPlanOption?.goal || "")}"></label><label>排除范围<input id="plan-exclusions" value="${escapeHtml((defaultPlanOption?.exclusions || []).join("；"))}"></label><label>研究维度<input id="plan-facets" value="${escapeHtml((defaultPlanOption?.facets || []).join("；"))}"></label><label>查询词<input id="plan-queries" value="${escapeHtml((defaultPlanOption?.queries || []).join("；"))}"></label><button id="plan-confirm" class="button primary" type="button">确认计划</button></div></section>` : "";
  topicDetail.innerHTML = `
    <div class="topic-actions">${searchButton}${autoButton}${selectButton}${retryButton}<span class="muted">${escapeHtml(topic.status)} · ${escapeHtml(topic.execution_mode || "manual")}</span>${budgetSummary}</div>
    ${planPanel}
    ${confirmation}
    ${outcome}
    ${candidates.length ? `<div class="candidate-list">${candidates.map(candidate => `
      <label class="candidate-card"><input type="checkbox" value="${escapeHtml(candidate.candidate_id)}" ${candidate.selected ? "checked" : ""} ${topic.status !== "awaiting_selection" ? "disabled" : ""}>
        <span><span class="candidate-title">${escapeHtml(candidate.title || candidate.url)}</span><span class="candidate-summary">${escapeHtml(candidate.summary || "暂无摘要")}</span><span class="candidate-meta">${escapeHtml(candidate.source_type)} · ${escapeHtml(candidate.host)} · ${escapeHtml((candidate.providers || []).join(", "))}</span><span class="candidate-meta">${escapeHtml(candidate.relevance_reason || "")}${(candidate.risk_flags || []).length ? ` · 风险：${escapeHtml(candidate.risk_flags.join(", "))}` : ""}</span></span>
        <span class="candidate-score">${Number(candidate.quality_score) || 0}</span>
      </label>`).join("")}</div>` : '<p class="muted">当前没有候选来源。</p>'}`;
  const searchButtonNode = document.querySelector("#topic-search-button");
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
    await loadRuns();
    await selectRun(result.run_id);
  } catch (error) {
    formMessage.className = "form-message error";
    formMessage.textContent = error.message;
  } finally {
    submitButton.disabled = false;
  }
});

topicForm?.addEventListener("submit", async event => {
  event.preventDefault();
  topicMessage.className = "form-message";
  topicMessage.textContent = "正在创建主题...";
  try {
    const result = await request("/api/topics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ topic: topicForm.elements.topic.value, mode: topicForm.elements.mode.value, execution_mode: topicForm.elements.execution_mode.value }) });
    topicForm.elements.topic.value = "";
    topicMessage.textContent = "主题已创建，请在右侧启动搜索。";
    topicState.selectedId = result.run_id;
    await loadTopics();
    await selectTopic(result.run_id);
  } catch (error) { topicMessage.className = "form-message error"; topicMessage.textContent = error.message; }
});

document.querySelector("#refresh-button").addEventListener("click", async () => {
  await loadRuns();
  if (state.selectedId) await selectRun(state.selectedId);
});

loadTopics();

modelsButton.addEventListener("click", async () => {
  modelsButton.disabled = true;
  modelsButton.textContent = "查询中...";
  modelsPanel.hidden = false;
  modelsPanel.innerHTML = '<div class="loading compact">正在查询 IceAPI 模型...</div>';
  try {
    renderModels(await request("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: currentApiKey() })
    }));
  } catch (error) {
    modelsPanel.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`;
  } finally {
    modelsButton.disabled = false;
    modelsButton.textContent = "查询模型";
  }
});

document.querySelector("#check-button").addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "自检中...";
  try {
    const result = await request("/api/mvp-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: currentApiKey() })
    });
    showToast(result.status === "passed" ? "MVP 自检通过" : "MVP 自检未通过");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "运行自检";
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
    if (latest && (latest.updated_at || "") !== state.selectedDetailVersion) {
      const runId = state.selectedId;
      const run = await request(`/api/runs/${encodeURIComponent(runId)}`);
      if (state.selectedId === runId) {
        state.selectedDetailVersion = run.updated_at || "";
        renderDetail(run);
      }
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
    if (latest && (latest.updated_at || "") !== topicState.selectedDetailVersion) {
      const runId = topicState.selectedId;
      const topic = await request(`/api/topics/${encodeURIComponent(runId)}`);
      if (topicState.selectedId === runId) {
        topicState.selectedDetailVersion = topic.updated_at || "";
        renderTopic(topic);
      }
    }
  } finally {
    topicPollState.inFlight = false;
  }
}

loadRuns(true);
loadMetrics();
state.timer = window.setInterval(poll, 3000);
window.setInterval(pollTopics, 3000);
