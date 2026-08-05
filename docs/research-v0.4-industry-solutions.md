# v0.4 业内搜索与候选确认方案调研

> 调研日期：2026-08-04。本文只记录公开的一手资料和对本仓库 v0.4 的落地建议，不包含任何 API key、cookie 或真实运行产物。

## 结论摘要

成熟产品普遍把“检索”和“证据引用/人工采用”分成两层：搜索 API 返回可排序的结果及其摘要，应用层再做 URL 规范化、重复合并、质量/风险标记和用户选择。搜索提供方不会替应用决定“哪些来源可以进入高成本处理”。因此 v0.4 应把 `SearchProvider` 做成可替换适配器，先交付 fake provider 和一个通用网页搜索适配器；所有候选都进入本地 `sources.json` 草稿，只有明确确认的 URL 才能流向 v0.5+ 的抓取或 v0.1 视频管线。

推荐默认使用本地/自托管 SearXNG（无供应商 key、便于离线 fake 测试），同时保留 Brave、Tavily、Exa 适配器边界。若部署环境没有 SearXNG，再启用 Brave 作为第一方托管 API；Tavily/Exa 适合作为可选的高质量摘要或语义检索后端，不应成为核心模型或数据契约的依赖。

## 一手方案对比

| 方案 | 官方返回能力 | 适合本项目的部分 | 主要取舍/风险 |
|---|---|---|---|
| **SearXNG** | `GET /search`；`format=json` 返回 `query`、`number_of_results` 和结果数组。结果通常含 `url`、`title`、`content`、`engine`、`category`、`score`、`publishedDate` 等字段；支持 `pageno`、`safesearch`、语言、分类和时间范围。见 [Search API](https://docs.searxng.org/dev/search_api.html)。 | 自托管、无单一商业 key；能聚合多引擎，最符合本地 CLI/Web 原型和 fake provider 旁路。 | 实例管理员可禁用 JSON、设置引擎和限流；不同实例的字段完整性和排序不可假设。应记录实例 URL、请求参数和实际引擎，并把缺失字段视为正常情况。安全设置/限流见 [settings](https://docs.searxng.org/admin/settings.html)。 |
| **Brave Search API** | Web Search REST 接口；以 `q` 为核心，返回网页结果并支持 `count`、语言/地区、安全搜索、时间过滤、域名过滤、分页偏移及结果过滤等选项。官方入口：[Web Search API](https://api.search.brave.com/app/documentation/web-search/get-started)。 | 托管 API、结果字段较稳定，适合作为生产 smoke test 或没有自托管条件时的首选。 | 需要 secret token，受账户配额/HTTP 429 约束；不能把 token 写入 manifest、日志或候选数据。对摘要、排名和可访问性仍需应用层复核。 |
| **Tavily** | `POST /search`；请求可指定 `query`、`search_depth`、`topic`、`max_results`、时间范围、包含/排除域名，以及是否返回 answer/raw content/images；结果含 `title`、`url`、`content`、`score`、可选 `raw_content`/发布日期。见 [Search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search)。 | 适合快速得到带摘要的候选，`include_domains` 可支持“官方文档优先”的查询策略。 | key、配额和按深度计费；`answer` 是派生摘要，不能替代原始 URL 证据。v0.4 默认应关闭 raw content/images，避免越界到 v0.5 抓取和增加成本。 |
| **Exa** | `POST /search`；支持 `type`（如 neural/keyword/auto）、`category`、结果数、域名/日期过滤；可通过 contents 选项请求 highlights、summary 或 text。见 [Search](https://docs.exa.ai/reference/search) 与 [Contents](https://docs.exa.ai/reference/contents)。 | 语义主题检索和高相关性候选的可选增强，适合技术主题或长尾查询。 | API key、额度和语义排序的可解释性较弱；请求 contents 会增加响应/成本。应只保存候选元数据和可复核链接，文本抓取延后到人工确认后。 |

## 成熟产品的引用与选择模式

Anthropic 的引用能力要求模型输出与来源内容绑定的 citation，citation 包含来源标题、URL 以及引用文本在输入中的位置；见 [Citations](https://docs.anthropic.com/en/docs/build-with-claude/citations)。这说明可审计引用至少要保留“来源 URL + 原文片段/位置 + 生成时间”，不能只存模型写出的链接。搜索提供方的摘要也只能作为排序信号，最终证据应在后续抓取/视频分析阶段从用户确认的 URL 重新产生。

对 v0.4 的具体含义：

1. 每条结果保留 `provider`、`provider_result_id`（若有）、原始查询、请求时间、原始 URL 和摘要；这样可以解释“为什么出现”以及重复合并过程。
2. 选择是显式的二值闸门（selected + confirmed_at + confirmed_by=`user`），不能把排名靠前或模型推荐当成确认。
3. UI/CLI 展示“相关性理由、质量分、风险标记、查询来源”，并提供原始 URL 供人工打开复核；确认前不得下载、抓取、ASR、视觉分析或生成。

## 推荐的 v0.4 方案

### 1. 提供方边界

定义一个同步或异步等价的抽象（具体实现可先同步）：

```text
SearchProvider.search(
    topic: str,
    queries: list[str],
    *,
    max_results: int,
    mode: Literal["normal", "technical"],
    budget: TopicBudget,
) -> SearchBatch
```

`SearchBatch` 至少包含 `provider`、`requested_queries`、`results`、`started_at`、`finished_at`、`warnings` 和用量信息。单条原始结果先进入内部 `SearchResult`，再统一转换为现有 `TopicSourceCandidate`；提供方字段不能泄漏到主题模型之外。

首发适配顺序：

- `FakeSearchProvider`：固定 fixture、可注入错误/重复/空结果，所有单元测试默认使用它。
- `SearXNGProvider`：调用 `/search?format=json`，限制每个查询的页数和总结果数，记录实例与引擎。
- `BraveProvider`：可选托管适配器，读取环境变量中的 token，429 时返回可解释的 provider-level failure，不重试无限次。
- `TavilyProvider`/`ExaProvider`：保留扩展点，v0.4 可不纳入默认配置；使用其摘要或语义排序时仍走同一规范化管线。

### 2. 查询扩展与审计

从主题生成有限、可审计的查询集合（建议普通模式 3 条、技术模式 4 条上限）：原主题、同义词/英文变体、`official documentation` 变体，以及技术模式的 `site:github.com` 变体。每次查询记录 `query_id`、文本、生成原因、provider、耗时、返回数、错误和预算消耗；不允许无上限扩散。

### 3. 规范化、去重和评分

- URL：仅接受 `http`/`https`；小写 host、移除默认端口和 fragment，按已知规则清理追踪参数（`utm_*` 等），保留查询参数未知时的原值并标记 `url_normalization_warning`。
- 来源类型：host/path 规则识别 `video`、`web`、`github`、`unknown`；识别不确定时保留 `unknown`，不能因为标题猜测类型。
- 去重：先用规范化 URL 精确合并，再对标题+host 做保守近重复检测；合并项记录 `duplicate_of`、`merged_providers` 和所有命中查询，避免丢失出处。
- 评分：输出 0-100 的可解释分数，建议维度为相关性 40、来源可信度 25、内容完整度 15、新鲜度 10、重复/SEO 风险 10。分数只用于排序，不自动选择。
- 风险：至少标记 `unknown_source`、`paywall_or_login`（由结果元数据可见时）、`adult_or_sensitive`、`spam_or_seo`、`duplicate`、`provider_warning`、`unreachable`；无法判断则不打高置信标签。

### 4. 人工确认与持久化

候选卡片/CLI 行至少展示：稳定候选 ID、标题、host、来源类型、摘要、质量分、风险、命中查询和原始 URL。默认最多展示 20 条，最多确认 5 条，受 `TopicBudget` 强制限制。确认动作应原子写入 `TopicTask.selected_sources` 与 `topic_package/sources.json`，并把 run 状态从 `awaiting_selection` 推进到 `processing_sources`；无选择、超预算、provider 失败都写入 failure/audit，不触发后续处理。

建议在 `sources.json` 草稿中为每条候选保存：

```json
{
  "candidate_id": "cand-...",
  "url": "https://example.com/article",
  "canonical_url": "https://example.com/article",
  "title": "...",
  "summary": "...",
  "source_type": "web",
  "queries": ["原始主题", "主题 official documentation"],
  "provider": "searxng",
  "quality_score": 78,
  "risk_flags": [],
  "duplicate_of": null,
  "selected": false,
  "confirmed_at": null
}
```

## 安全、限流与失败策略

- 所有 provider secret 只从环境变量/本地未跟踪配置读取；日志和审计只记 provider、状态码、请求 ID（如有）和脱敏查询，不记 Authorization 头。
- 单个查询设置超时，429/5xx 采用有限次数指数退避；达到预算或 provider 限额立即停止该 provider，并保留已返回候选。不要把一次 provider 失败升级成整个主题失败。
- 对搜索返回 URL 做协议和 host 校验；禁止在 v0.4 自动跟随下载链接、执行页面脚本或访问本地/内网地址，SSRF 防护属于后续抓取层的必备条件。
- 记录缓存键（provider + 规范化查询 + 参数 + schema 版本）和缓存命中；显式 `refresh_cache` 才重新发起请求。
- `FakeSearchProvider` 必须覆盖：同 URL 多查询、不同追踪参数、空结果、重复标题、坏 URL、429/超时和部分 provider 成功，以确保无网络回归路径。

## 取舍结论

选择“本地 SearXNG + 可选托管 provider + 自有候选管线”的组合，获得可离线测试、低绑定和可审计性；代价是需要维护实例配置差异和 URL/质量启发式。Tavily/Exa 的增强摘要不在 v0.4 默认开启，避免把搜索阶段变成抓取/生成阶段；Brave 作为明确的托管 fallback，便于部署 smoke test。任何 provider 都不直接决定选中来源，人工确认仍是进入后续高成本处理的唯一闸门。

## 官方资料

- Brave Web Search API: https://api.search.brave.com/app/documentation/web-search/get-started
- SearXNG Search API: https://docs.searxng.org/dev/search_api.html
- SearXNG settings and limiter: https://docs.searxng.org/admin/settings.html
- Tavily Search endpoint: https://docs.tavily.com/documentation/api-reference/endpoint/search
- Tavily rate limits: https://docs.tavily.com/documentation/rate-limits
- Exa Search API: https://docs.exa.ai/reference/search
- Exa Contents API: https://docs.exa.ai/reference/contents
- Anthropic Citations: https://docs.anthropic.com/en/docs/build-with-claude/citations
- GitHub repository search (技术模式可选来源): https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28
