const stages = ["manifest", "media_extract", "frame_extract", "asr", "vision_ocr", "timeline_merge", "distill", "score", "package"];
const stageNames = {
  manifest: "元数据", media_extract: "媒体", frame_extract: "抽帧", asr: "ASR",
  vision_ocr: "视觉", timeline_merge: "证据合并", distill: "蒸馏", score: "评分", package: "打包"
};
const statusNames = { created: "排队中", running: "处理中", completed: "已完成", failed: "失败", passed: "通过", needs_review: "待复核" };
const difficultyNames = { lenient: "宽松", standard: "标准", strict: "严格", off: "关闭 Judge" };

const state = { runs: [], selectedId: null, timer: null };
const runsList = document.querySelector("#runs-list");
const detailPanel = document.querySelector("#detail-panel");
const form = document.querySelector("#video-form");
const formMessage = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const modelsButton = document.querySelector("#models-button");
const modelsPanel = document.querySelector("#models-panel");

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
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

document.querySelector("#refresh-button").addEventListener("click", async () => {
  await loadRuns();
  if (state.selectedId) await selectRun(state.selectedId);
});

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
  if (!state.runs.some(run => ["created", "running"].includes(run.status))) return;
  await loadRuns();
  if (state.selectedId) await selectRun(state.selectedId);
}

loadRuns(true);
state.timer = window.setInterval(poll, 3000);
