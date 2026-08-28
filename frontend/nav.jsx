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
        <span className="skill-box-badges">
          {item.featured && <span className="hot-badge">热门观察</span>}
          <span className={`availability ${item.downloadable ? "ready" : "source"}`}>
            {item.downloadable ? "可下载" : item.review_status === "needs_review" ? "待复核索引" : "来源索引"}
          </span>
        </span>
      </span>
      <strong>{item.title}</strong>
      <span className="skill-summary">{item.summary}</span>
      <span className="skill-box-foot"><span>{item.category}</span><span>{item.popularity?.label || item.source_label}</span></span>
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
            <a className="primary-action" href={`/api/catalog/${encodeURIComponent(item.id)}/download`}>下载 Skill</a>
          ) : (
            <a className="secondary-action" href={item.source_url} target="_blank" rel="noreferrer">查看原始来源</a>
          )}
        </header>
        <div className="detail-body">
          <section><span className="detail-label">适合谁</span><p>{item.audience}</p></section>
          <section><span className="detail-label">使用前提</span><ul className="detail-list">{item.prerequisites.map(value => <li key={value}>{value}</li>)}</ul></section>
          <section><span className="detail-label">如何获得</span><p>{item.downloadable ? "本地演示包已通过再分发检查，可直接下载 ZIP。" : "当前仅收录公开来源，不在本站镜像；请前往原始地址确认许可与安装方式。"}</p></section>
          <section><span className="detail-label">证据</span><ul className="detail-list">{item.evidence.map(value => <li key={value}>{value}</li>)}</ul></section>
          <section><span className="detail-label">风险</span><ul className="detail-list risk">{item.risks.map(value => <li key={value}>{value}</li>)}</ul></section>
          {item.popularity?.status === "observed" && <section className="popularity-detail"><span className="detail-label">热度观察</span><p>{item.popularity.label || "公开来源观察"}{item.popularity.observed_at ? ` · 观察于 ${item.popularity.observed_at}` : ""}</p><ul className="detail-list">{item.popularity.signals.map(value => <li key={value}>{value}</li>)}</ul>{item.popularity.limitations.length > 0 && <p className="popularity-limit">局限：{item.popularity.limitations.join("；")}</p>}</section>}
          <dl className="provenance-list">
            <div><dt>来源</dt><dd><a href={item.source_url} target="_blank" rel="noreferrer">{item.source_label}</a></dd></div>
            <div><dt>版本</dt><dd>{item.source_version}</dd></div>
            <div><dt>许可证</dt><dd>{item.license}</dd></div>
            <div><dt>状态</dt><dd>{item.downloadable ? "已验证 / 可下载" : item.review_status === "needs_review" ? "待复核 / 未镜像" : "来源可查 / 未镜像"}</dd></div>
          </dl>
          <div className="tag-row">{item.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
        </div>
      </article>
    </div>
  );
}

function Home({ items, onBrowse, onOpen }) {
  const hotItems = items.filter(item => item.featured && item.popularity?.status === "observed").slice(0, 6);
  return (
    <main className="site-page home-page">
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <p className="brand-overline">skill_seargen / LOCAL 01</p>
          <h1 id="hero-title"><span className="search-word">Search</span><span className="multiply">×</span><span className="generate-word">Generate</span></h1>
          <p className="hero-subtitle">让多模态经验成为可执行 Skill</p>
          <p className="hero-note">搜索视频、网页与代码中的真实经验。生成可追溯、可复核、能执行的能力。</p>
          <button className="primary-action" type="button" onClick={onBrowse}>浏览 Skills</button>
        </div>
        <div className="hero-method" aria-label="Search 与 Generate 双引擎">
          <div className="method search-method"><span>01</span><strong>SEARCH</strong><p>广泛发现来源</p></div>
          <div className="method generate-method"><span>02</span><strong>GENERATE</strong><p>蒸馏多模态证据</p></div>
        </div>
        <div className="hero-shelf" aria-label="精选 Skill 预览">
          {items.slice(0, 6).map((item, index) => <SkillCard key={item.id} item={item} featured={index < 2} onOpen={onOpen} />)}
        </div>
        <div className="hero-index"><span>{String(items.length).padStart(2, "0")} SKILLS</span><span>{String(items.filter(item => item.downloadable).length).padStart(2, "0")} LOCAL PACKAGES</span><span>03 SOURCE TYPES</span></div>
      </section>
      <section className="hot-section" aria-labelledby="hot-title">
        <div className="hot-heading"><div><p className="section-kicker">PUBLIC SIGNALS / 02</p><h2 id="hot-title">热门 Skill 观察</h2><p>按公开来源信号精选，不把单一榜单或瞬时排名当成普遍热度。</p></div><button className="secondary-action" type="button" onClick={onBrowse}>查看完整目录</button></div>
        {hotItems.length > 0 ? <div className="hot-grid">{hotItems.map(item => <SkillCard key={item.id} item={item} featured onOpen={onOpen} />)}</div> : <div className="hot-empty">热门观察正在收集可复核来源，当前先浏览完整目录。</div>}
      </section>
      <section className="thesis-band">
        <p>没有 Search，Generate 只是猜测。</p><span>×</span><p>没有 Generate，Search 只是资料堆。</p>
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
        <div><p className="section-kicker">SKILL CATALOG / 01</p><h1>把经验摆上货架</h1><p>每个 Skill 都带着来源、许可与可用状态。</p></div>
        <div className="catalog-count"><strong>{String(filtered.length).padStart(2, "0")}</strong><span>当前结果</span></div>
      </header>
      <div className="catalog-controls">
        <label className="search-field"><span>搜索</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="用途、来源或关键词" /></label>
        <label><span>分类</span><select value={category} onChange={event => setCategory(event.target.value)}><option value="all">全部分类</option>{categories.map(categoryItem => <option key={categoryItem.name} value={categoryItem.name}>{categoryItem.name} ({categoryItem.count})</option>)}</select></label>
        <label><span>状态</span><select value={availability} onChange={event => setAvailability(event.target.value)}><option value="all">全部状态</option><option value="downloadable">可下载</option><option value="source_only">来源索引</option></select></label>
      </div>
      <section className="catalog-grid" aria-live="polite">
        {filtered.map(item => <SkillCard key={item.id} item={item} onOpen={onOpen} />)}
        {!filtered.length && <div className="catalog-empty"><strong>没有匹配的 Skill</strong><span>换一个关键词或筛选条件。</span></div>}
      </section>
    </main>
  );
}

function SiteApp() {
  const [page, setPage] = useState("home");
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [selected, setSelected] = useState(null);
  const workspace = document.querySelector("#workspace-root");

  useEffect(() => {
    Promise.all([fetch("/api/catalog").then(response => response.json()), fetch("/api/catalog/categories").then(response => response.json())])
      .then(([catalog, categoryData]) => {
        const nextItems = catalog.items || [];
        setItems(nextItems);
        setCategories(categoryData.categories || []);
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
    if (page === "workspace") window.dispatchEvent(new CustomEvent("workspace:view", { detail: { view: "operations" } }));
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [page]);

  function navigate(next) { history.replaceState(null, "", location.pathname); setSelected(null); setPage(next); }
  function openSkill(item) { location.hash = `skill/${encodeURIComponent(item.id)}`; setSelected(item); }
  function closeSkill() { history.replaceState(null, "", location.pathname); setSelected(null); }

  return (
    <>
      <header className="site-header">
        <button className="wordmark" type="button" onClick={() => navigate("home")}><span className="wordmark-mark">S×G</span><span>skill_seargen</span></button>
        <nav className="site-nav" aria-label="主导航">
          {[['home', '首页'], ['catalog', '浏览 Skills'], ['workspace', '工作台']].map(([name, label]) => <button key={name} className={page === name ? "active" : ""} type="button" onClick={() => navigate(name)}>{label}</button>)}
        </nav>
        <span className="local-indicator"><i />LOCAL</span>
      </header>
      {page === "home" && <Home items={items} onBrowse={() => navigate("catalog")} onOpen={openSkill} />}
      {page === "catalog" && <Catalog items={items} categories={categories} onOpen={openSkill} />}
      {selected && <SkillDetail item={selected} onClose={closeSkill} />}
    </>
  );
}

const root = document.querySelector("#react-nav");
if (root) createRoot(root).render(<SiteApp />);
