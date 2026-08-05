# v0.4 开源搜索与 MCP 工具调研

> 调研日期：2026-08-04。仅筛选有公开官方仓库且许可证可确认的工具；优先 MIT/Apache-2.0。本文的“无需 key”指搜索工具本身可在不配置商业搜索 API key 的情况下启动，不代表上游搜索网站保证长期可抓取或无限使用。

## 结论

v0.4 不应直接依赖一个“大而全”的 MCP 搜索工具来替代本项目的 `SearchProvider`。推荐实现顺序仍是：`FakeSearchProvider` -> 直接调用自托管 SearXNG 的 JSON API -> 可选 MCP 网关。这样候选契约、预算、去重、评分和人工确认都留在 Python 主仓库，且不会在确认前意外进入网页抓取、浏览器自动化或文件下载。

在开源工具中，最适合借鉴或作为开发期可选网关的是：

1. **SearXNG**：功能上最适合第一个通用搜索后端，原生 JSON API 和多引擎聚合成熟；但许可证为 **AGPL-3.0**，不是宽松许可证。应以独立服务的 HTTP API 使用，不能复制其源代码到主仓库；若随产品分发或修改其服务镜像，需单独评估 AGPL 义务。
2. **Agent Search MCP**：**Apache-2.0**，自托管、支持中英文零 key 来源、MCP/CLI、预算与可选持久缓存；适合作为接口和诊断设计的参考，也可作为可选开发工具。生产主链路不应依赖其 Node/MCP 进程和易被封锁的网页抓取适配器。
3. **free-search-mcp**：**MIT**，多引擎、JSON、FTS5 缓存和 MCP 很完整；适合人工研究和集成试验，但超出 v0.4 的“只搜索候选”边界，且浏览器回退、URL 抓取、下载和可选登录机制与本项目的确认前禁抓取、无 cookie 约束冲突，因此不作为默认后端。

Whoogle 虽是 MIT，却已被官方声明停止提供有效搜索结果，明确排除。LibreY 是仍维护的元搜索前端，但为 AGPL-3.0、没有 MCP，且核心依赖对第三方搜索页的抓取，不应作为 v0.4 的首个实现。

## 候选对比

| 工具 | 官方仓库与许可证 | 搜索/引擎 | API、MCP 与缓存 | API key | 对 v0.4 的判断 |
|---|---|---|---|---|---|
| [SearXNG](https://github.com/searxng/searxng) | [AGPL-3.0](https://github.com/searxng/searxng/blob/master/LICENSE) | 自托管元搜索，聚合多种搜索服务和数据库。 | 官方 [Search API](https://docs.searxng.org/dev/search_api.html) 支持 `format=json`、分页、语言、分类、时间范围和安全搜索；本体不是 MCP 服务。 | 不要求单一商业 key，但实例实际可用引擎由管理员配置。 | **推荐作为 HTTP provider**。服务端与本仓库隔离，项目只消费标准 JSON 并自行缓存/审计。AGPL 不符合“宽松许可证”偏好，故不复制或嵌入源码。 |
| [Agent Search MCP](https://github.com/lennney/agent-search-mcp) | [Apache-2.0](https://github.com/lennney/agent-search-mcp/blob/main/LICENSE) | README 列出 DuckDuckGo、Sogou、Bing、Baidu、Wikipedia、Startpage 等零 key 适配器，并允许 Brave、Tavily、Exa 等配置后端。 | 自托管 Node 工具；支持 stdio MCP、Streamable HTTP、CLI `--json`；有请求预算、部分失败报告；默认内存精确结果缓存，可配置目录持久化与 TTL。 | 零 key 适配器可启动；商业后端按环境变量可选。 | **推荐借鉴，谨慎作为可选网关**。它的中文源、预算和部分失败表达很有价值；但网页抓取可变性、Node 运行时和适配器范围不应成为 Python 主流程依赖。 |
| [free-search-mcp](https://github.com/sweetcornna/free-search-mcp) | [MIT](https://github.com/sweetcornna/free-search-mcp/blob/main/LICENSE) | 默认 DuckDuckGo、Mojeek、Google News、Bing；可选 Startpage、SearXNG、Google、Bilibili、知乎等，也有垂直检索路由。 | MCP 支持 stdio/streamable HTTP；`search` 可要求 JSON；有 FTS5 搜索/页面缓存、来源合并和抓取/文档读取工具。 | 默认搜索不要求 key；商业 provider 可选。 | **不推荐作默认后端，推荐作实验性 MCP 工具和设计参考**。其 `research()` 会执行搜索后抓取，`fetch`/`download` 也超出 v0.4；README 还描述浏览器回退和可选站点登录，必须不能被本项目的自动流程调用。 |
| [mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng) | [MIT](https://github.com/ihor-sokoliuk/mcp-searxng/blob/main/LICENSE) | 把一个或多个 SearXNG 实例包装为 MCP 搜索。 | README 声明支持分页、时段/语言/安全搜索过滤、原始 JSON 或格式化文本、实例 failover/fan-out 和 autocomplete；可选 HTML fallback。 | MCP 本身不要求商业 key，但必须提供自建或可信 SearXNG 实例 URL。 | **可选 MCP 兼容层，不取代直接 HTTP provider**。如果 v1.0 要让外部 MCP client 使用搜索能力，可接入；v0.4 内部调用 SearXNG JSON 更少一层进程、数据转换和安全面。 |
| [web-search-mcp](https://github.com/mrkrsl/web-search-mcp) | [MIT](https://github.com/mrkrsl/web-search-mcp/blob/main/LICENSE) | README 描述以浏览器 Bing、浏览器 Brave、HTTP DuckDuckGo 的顺序回退。 | 本地 MCP，提供结果摘要和整页提取；官方 README 未把可持久化候选缓存作为稳定接口说明。 | 不需要 key。 | **不推荐**。直接驱动多浏览器、全页内容提取和对搜索页的依赖都超出 v0.4，并带来性能、反自动化和合规不确定性。 |
| [LibreY](https://github.com/Ahwxorg/LibreY) | [AGPL-3.0](https://github.com/Ahwxorg/LibreY/blob/main/LICENSE) | README 称可提供 Google、DuckDuckGo、Brave、Ecosia、Yandex、Mojeek 的文本结果，另有图片和 torrent。 | README 的功能比较表标记 API 支持；不是 MCP 服务。未将本地候选缓存作为可供本项目使用的稳定契约。 | 无统一商业 key。 | **不推荐**。AGPL 与宽松许可证偏好不符，且作为搜索前端更适合人类浏览；其引擎抓取稳定性应由独立 provider 承担，不应锁定到本仓库。 |
| [Whoogle Search](https://github.com/benbusby/whoogle-search) | [MIT](https://github.com/benbusby/whoogle-search/blob/main/LICENSE) | 历史上代理 Google 搜索。 | Web 前端，不是稳定 JSON/MCP 搜索后端。 | 不需要 key。 | **明确排除**。官方 README 顶部声明 Google 已阻断其无 JS 检索方式、项目无法修复、现有部署/镜像不再支持，不能作为 fallback。 |

## 对 `free-search-mcp` 的具体判断

该项目是目前最接近“free-search-mcp 类多引擎、本地、MCP、缓存”目标的开源候选，且仓库明确采用 MIT。它有几个可复用的产品经验：

- 把每个引擎的失败如实暴露，而不是以空列表伪装成功；
- 用 host 规范化和标题模糊匹配做去重，保留命中引擎与出处；
- 为搜索与页面缓存设 TTL，并提供查询缓存；
- 支持 `format="json"`，使候选结果可进入结构化管线；
- 把引擎/筛选条件/预算输出给调用方，便于解释候选来源。

但不能直接纳入 v0.4 默认执行路径：其 `research` 是“搜索后自动抓取前 N 个页面”，`fetch`、`read_doc`、`download` 和浏览器回退也可能访问确认外 URL。这与路线图中“用户确认前不下载、抓取、ASR、视觉分析或生成”的硬边界冲突。它还允许部分可选引擎使用站点登录/持久 cookie，这与项目“不接 cookie”的范围冲突。

因此仅允许两种用法：人工单独运行它做探索；或未来以显式的、只暴露 `search(..., format="json")` 的本地 MCP 适配器接入，并在适配器层禁用 `research`、`fetch`、`download`、浏览器引擎、登录和写入型工具。不要解析其 Markdown 研究报告作为项目候选数据源。

## 面向 v0.4 的落地建议

### 默认结构

```text
Topic + mode
  -> QueryExpander（有限查询并记录理由）
  -> SearchProvider（Fake / SearXNG HTTP / 可选 MCP）
  -> RawSearchResult
  -> URL 规范化、来源分类、去重、质量/风险评分
  -> TopicSourceCandidate + sources.json 草稿
  -> 人工确认
  -> 后续版本的抓取或视频处理
```

`SearchProvider` 应只返回搜索结果，禁止返回网页正文、下载路径、cookie、浏览器会话或“已处理的答案”。建议内部原始模型包含：`provider`、`query_id`、`rank`、`url`、`title`、`snippet`、`published_at`（可选）、`engine`（可选）和 `provider_warnings`。所有候选进入既有 `TopicSourceCandidate` 前，必须由主仓库完成 URL canonicalization、来源类型识别、去重和评分。

### provider 选择和测试

- `FakeSearchProvider` 是单元测试唯一必需 provider，fixture 必须覆盖中英文查询、重复 URL、跟踪参数、相同标题、空结果、部分失败和超预算。
- `SearXNGProvider` 是第一个真实 adapter：明确要求 operator 配置实例 URL，固定 `format=json`，限制查询数和每查询结果数；将实例不可用或 JSON 被禁用记录为 provider warning，而非转去抓取 HTML。
- 可选 `McpSearchProvider` 只在用户显式启用时启动本地 MCP 子进程，并使用 allowlist 限定只读 `search` 工具与 JSON 输出。首选 `mcp-searxng`；`free-search-mcp` 仅在其危险工具及浏览器/登录引擎全部禁用时试验。
- 不把 Whoogle、LibreY 或 `web-search-mcp` 加入默认 fallback。它们一旦失败必须如实显示，而不是自动尝试规避 CAPTCHA、登录或访问限制。

### 许可与安全边界

- 在 `THIRD_PARTY.md` 或后续 provider 文档登记仓库 URL、精确 tag/commit、许可证、调用边界及是否随发行版分发。
- 对 SearXNG/LibreY 这类 AGPL 项目，保持“独立进程/远程 HTTP 服务”边界；未完成分发义务评估前，不 vendoring、复制源码或捆绑修改后的镜像。
- MCP HTTP transport 只绑定 loopback，或由带认证的反向代理保护。`free-search-mcp` 的 README 明确警告其 HTTP 端点若暴露会代表调用者请求任意 URL，这种能力不可直接向局域网/公网公开。
- v0.4 不跟随搜索结果进行页面抓取；未来的抓取层还必须拦截非 HTTP(S)、回环/私网地址、重定向链和超大响应，避免 SSRF 和预算逃逸。

## 官方资料

- SearXNG repository: https://github.com/searxng/searxng
- SearXNG Search API: https://docs.searxng.org/dev/search_api.html
- Agent Search MCP repository: https://github.com/lennney/agent-search-mcp
- free-search-mcp repository: https://github.com/sweetcornna/free-search-mcp
- mcp-searxng repository: https://github.com/ihor-sokoliuk/mcp-searxng
- web-search-mcp repository: https://github.com/mrkrsl/web-search-mcp
- LibreY repository: https://github.com/Ahwxorg/LibreY
- Whoogle Search repository and maintenance notice: https://github.com/benbusby/whoogle-search
