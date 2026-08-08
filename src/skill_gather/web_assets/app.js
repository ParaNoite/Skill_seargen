const stages = ["manifest", "media_extract", "frame_extract", "asr", "vision_ocr", "timeline_merge", "distill", "score", "package"];
const stageNames = {
  manifest: "元数据", media_extract: "媒体", frame_extract: "抽帧", asr: "ASR",
  vision_ocr: "视觉", timeline_merge: "证据合并", distill: "蒸馏", score: "评分", package: "打包"
};
const statusNames = { created: "排队中", running: "处理中", completed: "已完成", failed: "失败", passed: "通过", needs_review: "待复核" };
const difficultyNames = { lenient: "宽松", standard: "标准", strict: "严格", off: "关闭 Judge" };
const RUN_POLL_STATUSES = new Set(["created", "running"]);
const TOPIC_POLL_STATUSES = new Set(["processing_sources", "generating", "scoring"]);

const state = { runs: [], selectedId: null, timer: null };
const runPollState = { inFlight: false };
const topicState = { topics: [], selectedId: null };
const topicPollState = { inFlight: false };
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
    topicState.topics = (await request("/api/topics")).topics || [];
    renderTopics();
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
  const labels = { created: "未搜索", searching: "搜索中", awaiting_selection: "待确认", processing_sources: "处理中", generating: "生成中", scoring: "评分中", completed: "已完成", failed: "失败" };
  topicsList.innerHTML = topicState.topics.map(topic => `
    <button class="topic-item ${topic.run_id === topicState.selectedId ? "active" : ""}" type="button" data-topic-id="${escapeHtml(topic.run_id)}">
      <div class="topic-item-title">${escapeHtml(topic.topic)}</div>
      <div class="topic-item-meta">${escapeHtml(topic.mode)} · ${escapeHtml(labels[topic.status] || topic.status)} · ${Number(topic.candidate_count) || 0} 条候选</div>
    </button>`).join("");
  topicsList.querySelectorAll("[data-topic-id]").forEach(button => button.addEventListener("click", () => selectTopic(button.dataset.topicId)));
}

async function selectTopic(runId) {
  topicState.selectedId = runId;
  renderTopics();
  topicDetail.innerHTML = '<div class="loading compact">正在读取主题...</div>';
  try { renderTopic(await request(`/api/topics/${encodeURIComponent(runId)}`)); }
  catch (error) { topicDetail.innerHTML = `<div class="notice">${escapeHtml(error.message)}</div>`; }
}

function renderTopic(topic) {
  const candidates = topic.candidates || [];
  const selectedSources = topic.selected_sources || [];
  const videoRuns = topic.video_runs || [];
  const job = topic.job || {};
  const searchButton = topic.status === "created" || topic.status === "awaiting_selection" ? `<button id="topic-search-button" class="button secondary" type="button">${topic.status === "created" ? "搜索候选" : "刷新搜索"}</button>` : "";
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
      ${topic.artifacts?.video_processing_audit ? `<span class="status completed">视频审计：${escapeHtml(topic.artifacts.video_processing_audit)}</span>` : ""}
      ${topic.artifacts?.github_processing_audit ? `<span class="status completed">GitHub 审计：${escapeHtml(topic.artifacts.github_processing_audit)}</span>` : ""}
      ${videoRunSummary}
    </section>` : "";
  topicDetail.innerHTML = `
    <div class="topic-actions">${searchButton}${selectButton}${retryButton}<span class="muted">${escapeHtml(topic.status)}</span></div>
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
  runsList.innerHTML = '<div class="loading">正在读取处理记录...</div>';
  try {
    state.runs = (await request("/api/runs")).runs;
    renderRuns();
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
  renderRuns();
  detailPanel.innerHTML = '<div class="loading">正在读取详情...</div>';
  try {
    renderDetail(await request(`/api/runs/${encodeURIComponent(runId)}`));
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
        judge_difficulty: form.elements.judge_difficulty.value
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
    const result = await request("/api/topics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ topic: topicForm.elements.topic.value, mode: topicForm.elements.mode.value }) });
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
    if (latest) await selectRun(state.selectedId);
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
    if (latest) await selectTopic(topicState.selectedId);
  } finally {
    topicPollState.inFlight = false;
  }
}

loadRuns(true);
state.timer = window.setInterval(poll, 3000);
window.setInterval(pollTopics, 3000);
