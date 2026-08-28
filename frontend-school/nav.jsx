import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

const accents = {
  blue: "#1769e0", red: "#f04e3e", cyan: "#087f8c", green: "#0c8b78",
  violet: "#6956c8", orange: "#c76519", yellow: "#a66d00",
};
const skillSymbols = {
  video: "▷", globe: "◎", merge: "⇄", shield: "✓", book: "≡", browser: "□",
  file: "▤", table: "#", presentation: "▣", image: "◫", lock: "◇", cloud: "⌁",
};

function SkillCard({ item, featured = false, onOpen }) {
  return (
    <button className={`skill-box${featured ? " featured" : ""}`} onClick={() => onOpen(item)} type="button">
      <span className="skill-box-rule" style={{ backgroundColor: accents[item.accent] || accents.blue }} />
      <span className="skill-box-head">
        <span className="skill-symbol" aria-hidden="true" style={{ color: accents[item.accent] || accents.blue }}>{skillSymbols[item.icon] || "·"}</span>
        <span className={`availability ${item.downloadable ? "ready" : "source"}`}>
          {item.downloadable ? "可修读" : item.review_status === "needs_review" ? "待复核课程" : "旁听索引"}
        </span>
      </span>
      <strong>{item.title}</strong>
      <span className="skill-summary">{item.summary}</span>
      <span className="skill-box-foot"><span>{item.category}</span><span>{item.source_label}</span></span>
    </button>
  );
}

function SkillDetail({ item, onClose }) {
  if (!item) return null;
  return (
    <div className="detail-overlay" role="dialog" aria-modal="true" aria-labelledby="catalog-detail-title">
      <button className="detail-backdrop" type="button" onClick={onClose} aria-label="关闭详情" />
      <article className="catalog-detail">
        <div className="detail-topline" style={{ backgroundColor: accents[item.accent] || accents.blue }} />
        <header className="catalog-detail-header">
          <button className="close-button" type="button" onClick={onClose} aria-label="关闭">×</button>
          <p className="section-kicker">{item.category} / {item.source_label}</p>
          <h2 id="catalog-detail-title">{item.title}</h2>
          <p className="detail-lead">{item.summary}</p>
          {item.downloadable ? (
            <a className="primary-action" href={`/api/catalog/${encodeURIComponent(item.id)}/download`}>领取课程 Skill</a>
          ) : (
            <a className="secondary-action" href={item.source_url} target="_blank" rel="noreferrer">查看课程来源</a>
          )}
        </header>
        <div className="detail-body">
          <section><span className="detail-label">适合学生</span><p>{item.audience}</p></section>
          <section><span className="detail-label">先修要求</span><ul className="detail-list">{item.prerequisites.map(value => <li key={value}>{value}</li>)}</ul></section>
          <section><span className="detail-label">修读方式</span><p>{item.downloadable ? "本地课程包已通过再分发检查，可直接下载 ZIP。" : "当前仅收录公开教材来源，不在本站镜像；请前往原始地址确认许可与安装方式。"}</p></section>
          <section><span className="detail-label">教材与证据</span><ul className="detail-list">{item.evidence.map(value => <li key={value}>{value}</li>)}</ul></section>
          <section><span className="detail-label">考试风险</span><ul className="detail-list risk">{item.risks.map(value => <li key={value}>{value}</li>)}</ul></section>
          <dl className="provenance-list">
            <div><dt>来源</dt><dd><a href={item.source_url} target="_blank" rel="noreferrer">{item.source_label}</a></dd></div>
            <div><dt>版本</dt><dd>{item.source_version}</dd></div>
            <div><dt>许可证</dt><dd>{item.license}</dd></div>
            <div><dt>状态</dt><dd>{item.downloadable ? "已验证 / 可修读" : item.review_status === "needs_review" ? "待复核 / 未入学" : "教材可查 / 未镜像"}</dd></div>
          </dl>
          <div className="tag-row">{item.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
        </div>
      </article>
    </div>
  );
}

const homeActions = [
  { id: "video", index: "01", label: "单视频蒸馏", note: "从一个公开视频提炼课程与 Skill", accent: "blue", page: "workspace", target: "video" },
  { id: "topic", index: "02", label: "主题研究", note: "跨视频、网页与代码研究一个主题", accent: "red", page: "workspace", target: "topic" },
  { id: "skills", index: "03", label: "技能包市场", note: "浏览并领取已验证的 Skill 包", accent: "green", page: "catalog" },
  { id: "agents", index: "04", label: "Agent 市场", note: "选择已组装、可直接上岗的 Agent", accent: "orange", page: "agents" },
];

function Home({ onNavigate }) {
  return (
    <main className="site-page home-page">
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <p className="brand-overline">AI UNIVERSITY / LOCAL CAMPUS</p>
          <h1 id="hero-title"><span className="university-word">AI 大学</span></h1>
          <p className="hero-subtitle">让 AI 真正经历选课、学习、考试与毕业</p>
          <p className="hero-note">从公开视频、网页与代码中选课，把证据变成课程，用 Judge 决定一项能力能否毕业。</p>
          <button className="primary-action" type="button" onClick={() => onNavigate("catalog")}>进入技能市场</button>
        </div>
        <div className="hero-method" aria-label="AI 大学培养流程">
          <div className="method admissions-method"><span>01 / SEARCH</span><strong>选课处</strong><p>从真实来源选课</p></div>
          <div className="method classroom-method"><span>02 / GENERATE</span><strong>教务处</strong><p>把证据变成课程</p></div>
          <div className="method exam-method"><span>03 / JUDGE</span><strong>考试院</strong><p>成绩、风险与边界</p></div>
          <div className="method career-method"><span>04 / SKILL</span><strong>就业中心</strong><p>把毕业能力交给 Agent</p></div>
        </div>
        <div className="home-actions" aria-label="核心功能入口">
          {homeActions.map(action => (
            <button className={`home-action ${action.accent}`} key={action.id} type="button" onClick={() => onNavigate(action.page, action.target)}>
              <span className="home-action-index">{action.index}</span>
              <span className="home-action-copy"><strong>{action.label}</strong><span>{action.note}</span></span>
              <span className="home-action-arrow" aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      </section>
      <section className="thesis-band">
        <p>没有选课，学习只是随机模仿。</p><span>→</span><p>没有考试，完成不等于毕业。</p>
      </section>
    </main>
  );
}

function Catalog({ items, categories, onOpen }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [availability, setAvailability] = useState("all");
  const filtered = useMemo(() => items.filter(item => {
    const haystack = [item.title, item.summary, item.category, item.audience, ...item.tags].join(" ").toLowerCase();
    return (!query || haystack.includes(query.toLowerCase()))
      && (category === "all" || item.category === category)
      && (availability === "all" || (availability === "downloadable") === item.downloadable);
  }), [items, query, category, availability]);

  return (
    <main className="site-page catalog-page">
      <header className="catalog-heading">
        <div><p className="section-kicker">SKILL MARKET / VERIFIED PACKAGES</p><h1>技能市场</h1><p>下载、导入，AI 就拥有相应 Skill。每个 Skill 都带着教材来源、许可、考试风险与当前状态。</p></div>
        <div className="catalog-count"><strong>{String(filtered.length).padStart(2, "0")}</strong><span>可用 Skills</span></div>
      </header>
      <div className="catalog-controls">
        <label className="search-field"><span>搜索 Skill</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="能力、教材来源或关键词" /></label>
        <label><span>课程方向</span><select value={category} onChange={event => setCategory(event.target.value)}><option value="all">全部方向</option>{categories.map(categoryItem => <option key={categoryItem.name} value={categoryItem.name}>{categoryItem.name} ({categoryItem.count})</option>)}</select></label>
        <label><span>修读状态</span><select value={availability} onChange={event => setAvailability(event.target.value)}><option value="all">全部状态</option><option value="downloadable">可修读</option><option value="source_only">旁听索引</option></select></label>
      </div>
      <section className="catalog-grid" aria-live="polite">
        {filtered.map(item => <SkillCard key={item.id} item={item} onOpen={onOpen} />)}
        {!filtered.length && <div className="catalog-empty"><strong>没有匹配的 Skill</strong><span>换一个关键词或筛选条件。</span></div>}
      </section>
    </main>
  );
}

function Assemble({ items }) {
  const downloadable = items.filter(item => item.downloadable);
  const [selected, setSelected] = useState([]);
  const [agentName, setAgentName] = useState("my-skill-agent");
  const [message, setMessage] = useState("");
  function toggle(id) {
    setSelected(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]);
  }
  async function assemble() {
    if (!selected.length) return setMessage("请先勾选至少一个 Skill");
    setMessage("正在组装...");
    const response = await fetch("/api/assemble", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ item_ids: selected, agent_name: agentName }) });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      setMessage(error.error || "组装失败");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${agentName}.zip`;
    link.click();
    URL.revokeObjectURL(url);
    setMessage("已下载。解压后把生成的 Agent 文件夹内容放进项目根目录，再用 OpenCode 打开。");
  }
  return (
    <main className="site-page assemble-page">
      <header className="catalog-heading"><div><p className="section-kicker">CUSTOM AGENT / OPENCODE</p><h1>定制Agent</h1><p>从已验证货架中勾选 Skill，生成可直接放进项目的 OpenCode Agent 文件夹。</p></div><div className="catalog-count"><strong>{selected.length}</strong><span>已选择</span></div></header>
      <section className="assemble-panel">
        <div className="assemble-list">
          {downloadable.map(item => <label className="assemble-item" key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggle(item.id)} /><span><strong>{item.title}</strong><small>{item.summary}</small></span></label>)}
          {!downloadable.length && <p className="muted">当前没有可组装的已验证 Skill。</p>}
        </div>
        <aside className="assemble-actions"><h2>OpenCode Agent</h2><label className="agent-name-field"><span>Agent 名称</span><input value={agentName} onChange={event => setAgentName(event.target.value)} placeholder="my-skill-agent" maxLength={48} /></label><p>包内包含 <code>.opencode/agents/&lt;Agent名称&gt;.md</code>、每个 Skill 的 <code>SKILL.md</code> 和根目录 <code>AGENTS.md</code>。</p><button className="primary-action" type="button" onClick={assemble} disabled={!selected.length || !agentName.trim()}>下载 Agent 压缩包</button><p className="form-message" role="status">{message}</p></aside>
      </section>
    </main>
  );
}

function AgentPortrait({ accent, name }) {
  const initial = name.split(/\s+/).map(word => word[0]).join("").slice(0, 2).toUpperCase();
  return <span className={`agent-portrait ${accent || "blue"}`} aria-hidden="true"><i /><b>{initial}</b></span>;
}

function AgentCard({ agent, onOpen }) {
  return <button className="agent-card" type="button" onClick={() => onOpen(agent)}><AgentPortrait accent={agent.accent} name={agent.name} /><span className="agent-card-copy"><span className="agent-role">{agent.role}</span><strong>{agent.name}</strong><span>{agent.summary}</span><small>{agent.skills.length} 项已验证 Skill</small></span><span className="agent-status">{agent.status}</span></button>;
}

function AgentDetail({ agent, onClose }) {
  if (!agent) return null;
  return (
    <div className="detail-overlay" role="dialog" aria-modal="true" aria-labelledby="agent-detail-title">
      <button className="detail-backdrop" type="button" onClick={onClose} aria-label="关闭 Agent 详情" />
      <article className="catalog-detail agent-detail">
        <div className="detail-topline" style={{ backgroundColor: accents[agent.accent] || accents.blue }} />
        <header className="catalog-detail-header agent-detail-header">
          <button className="close-button" type="button" onClick={onClose} aria-label="关闭">×</button><AgentPortrait accent={agent.accent} name={agent.name} />
          <p className="section-kicker">AGENT JOB HALL / {agent.role}</p><h2 id="agent-detail-title">{agent.name}</h2><p className="detail-lead">{agent.description}</p>
          <a className="primary-action" href={`/api/agents/${encodeURIComponent(agent.id)}/download`}>下载 Agent ZIP</a>
        </header>
        <div className="detail-body">
          <section><span className="detail-label">拥有的 Skills</span><div className="agent-skill-list">{agent.skills.map(skill => <article key={skill.id}><span className="agent-skill-mark" style={{ backgroundColor: accents[skill.accent] || accents.blue }} /><strong>{skill.title}</strong><p>{skill.summary}</p></article>)}</div></section>
          <section><span className="detail-label">上岗说明</span><p>下载包会将下列已验证 Skills 安装到 <code>.opencode/skills/</code>，并生成同名 OpenCode Agent 文件。使用前仍应读取相关 Skill 的前置条件、边界与完成标准。</p></section>
          <dl className="provenance-list"><div><dt>岗位状态</dt><dd>{agent.status}</dd></div><div><dt>能力数量</dt><dd>{agent.skills.length} 项已验证 Skill</dd></div><div><dt>交付格式</dt><dd>{agent.id}.zip</dd></div></dl>
        </div>
      </article>
    </div>
  );
}

function AgentHall({ agents, onOpen }) {
  return <main className="site-page agent-hall-page"><header className="catalog-heading"><div><p className="section-kicker">AGENT JOB HALL / READY TO WORK</p><h1>Agent 求职大厅</h1><p>已经封装好能力组合的 Agent。查看岗位职责和已有 Skills，再一键领取可运行的 OpenCode Agent。</p></div><div className="catalog-count"><strong>{String(agents.length).padStart(2, "0")}</strong><span>可上岗 Agent</span></div></header><section className="agent-hall-grid" aria-live="polite">{agents.map(agent => <AgentCard key={agent.id} agent={agent} onOpen={onOpen} />)}{!agents.length && <div className="catalog-empty"><strong>暂无可上岗 Agent</strong><span>Agent 清单加载后会在这里显示。</span></div>}</section></main>;
}

function SiteApp() {
  const [page, setPage] = useState("home");
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [selected, setSelected] = useState(null);
  const [agents, setAgents] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [workspaceTarget, setWorkspaceTarget] = useState(null);
  const workspace = document.querySelector("#workspace-root");

  useEffect(() => {
    Promise.all([fetch("/api/catalog").then(response => response.json()), fetch("/api/catalog/categories").then(response => response.json()), fetch("/api/agents").then(response => response.json())])
      .then(([catalog, categoryData, agentData]) => {
        const nextItems = catalog.items || [];
        setItems(nextItems);
        setCategories(categoryData.categories || []);
        setAgents(agentData.agents || []);
        const hashId = decodeURIComponent(location.hash.replace(/^#skill\//, ""));
        if (location.hash.startsWith("#skill/")) setSelected(nextItems.find(item => item.id === hashId) || null);
      });
  }, []);

  useEffect(() => {
    function syncHash() {
      if (!location.hash.startsWith("#skill/")) setSelected(null);
      else setSelected(items.find(item => item.id === decodeURIComponent(location.hash.replace(/^#skill\//, ""))) || null);
    }
    window.addEventListener("hashchange", syncHash);
    return () => window.removeEventListener("hashchange", syncHash);
  }, [items]);

  useEffect(() => {
    if (workspace) workspace.hidden = page !== "workspace";
    document.body.dataset.page = page;
    if (page === "workspace") {
      const view = workspaceTarget ? "research" : "operations";
      window.dispatchEvent(new CustomEvent("workspace:view", { detail: { view } }));
      if (workspaceTarget) requestAnimationFrame(() => {
        const form = document.querySelector(workspaceTarget === "topic" ? "#topic-form" : "#video-form");
        const targetInput = document.querySelector(workspaceTarget === "topic" ? "#topic-input" : "#video-url");
        form?.scrollIntoView({ behavior: "smooth", block: "start" });
        targetInput?.focus({ preventScroll: true });
      });
    }
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [page, workspaceTarget]);

  function navigate(next, target = null) { history.replaceState(null, "", location.pathname); setSelected(null); setSelectedAgent(null); setWorkspaceTarget(target); setPage(next); }
  function openSkill(item) { location.hash = `skill/${encodeURIComponent(item.id)}`; setSelected(item); }
  function closeSkill() { history.replaceState(null, "", location.pathname); setSelected(null); }

  return (
    <>
      <header className="site-header">
        <button className="wordmark" type="button" onClick={() => navigate("home")}><span className="wordmark-mark">AIU</span><span>AI 大学</span></button>
        <nav className="site-nav" aria-label="主导航">
          {[['home', '校园'], ['catalog', '技能市场'], ['agents', 'Agent求职大厅'], ['workspace', '教务工作台'], ['assemble', '定制Agent']].map(([name, label]) => <button key={name} className={page === name ? "active" : ""} type="button" onClick={() => navigate(name)}>{label}</button>)}
        </nav>
        <span className="local-indicator"><i />LOCAL CAMPUS</span>
      </header>
      {page === "home" && <Home onNavigate={navigate} />}
      {page === "catalog" && <Catalog items={items} categories={categories} onOpen={openSkill} />}
      {page === "agents" && <AgentHall agents={agents} onOpen={setSelectedAgent} />}
      {page === "assemble" && <Assemble items={items} />}
      {selected && <SkillDetail item={selected} onClose={closeSkill} />}
      {selectedAgent && <AgentDetail agent={selectedAgent} onClose={() => setSelectedAgent(null)} />}
    </>
  );
}

const root = document.querySelector("#react-nav");
if (root) createRoot(root).render(<SiteApp />);
