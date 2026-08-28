# 证据链接口契约

视频蒸馏链路里的阶段交接必须通过结构化数据完成，不能依赖隐式文件状态。

## 核心对象

| 对象 | 职责 |
|---|---|
| `VideoSourceManifest` | 视频来源、标题、作者、时长、字幕可用性和风险标记 |
| `FrameManifest` | 抽帧预算、帧路径、时间戳、采样原因和重要性 |
| `EvidenceTimeline` | ASR、OCR、视觉观察和 metadata 合并后的时间线 |
| `RunState` | 阶段状态、已完成阶段、失败原因和 artifact 路径 |
| `SkillPackageMetadata` | skill 候选状态、模型配置、证据摘要、评分和风险 |
| `TopicTask` | 主题、普通/技术模式、预算、用量、缓存、来源选择、视频子 run、状态、失败审计和主题包索引 |
| `TopicPackage` | 主题包中 `sources.json`、`evidence/`、`references/`、`COURSE.md`、`knowledge.md`、可选 `SKILL.md` 和评分的相对路径 |

## v0.4 搜索候选契约

- 搜索 provider 只返回 `RawSearchResult` 元数据，不返回网页正文、下载路径、cookie 或浏览器会话。
- `TopicSourceCandidate.candidate_id` 使用规范化 URL 的稳定哈希；`canonical_url`、`providers`、`engines`、`queries`、`quality_score`、`risk_flags` 和 `duplicate_count` 用于排序与审计。
- `topic_package/sources.json` 同时保存候选草稿、显式选择和 provider warning；`search_audit.json` 保存查询、批次、返回数量、命中引擎、缓存命中和失败信息。
- 用户确认前不得进入网页抓取、视频下载、ASR、视觉理解或 GitHub 仓库分析。确认动作只推进主题 run 到后续处理入口。
- 查询意图、扩展查询、provider 请求、缓存命中、部分失败、候选评分理由和可选 NewAPI 判断均写入 `search_audit.json`；NewAPI 不可用不得使搜索主流程失败。
- 单个 provider 失败时保留其余成功 provider 的候选并记录 warning；只有所有启用 provider 都失败时，主题 run 才进入 `failed`。
- Bilibili provider 只接受公开视频 URL；JSON 搜索入口返回 412 时可解析公开搜索结果页的视频卡片，不访问候选视频页，也不使用 cookie、登录、浏览器自动化或验证码绕过。

## 主题任务契约

- 主题 run 使用 `created`、`searching`、`awaiting_selection`、`processing_sources`、`generating`、`scoring`、`completed` 或 `failed` 状态。
- `TopicBudget` 限制候选数、已选来源数、视频时长、模型调用、费用估算和运行时间；来源处理阶段必须通过 `TopicRunStore.record_usage()` 写入实际用量。超限时任务标记为 `failed`，并记录失败阶段和原因。
- `TopicCachePolicy` 默认复用缓存；显式刷新意图必须写入主题任务，供后续搜索和来源处理阶段读取。
- 主题包路径必须相对主题 run 目录，且不得记录 API key、cookie、token 或临时下载 URL。

## v0.5 网页文本契约

- `topic process` 只处理普通模式中用户已确认的 `web` 候选；请求仅使用公开 `http/https` URL，不传递 cookie、登录态或认证信息。
- 每个成功来源在 `topic_package/evidence/` 写入 JSON 证据，在 `topic_package/references/` 写入截断后的纯文本快照；`web_processing_audit.json` 分别记录成功、失败和跳过来源。
- `knowledge.md` 必须包含来源编号、关键要点、可识别步骤、冲突检测结果、证据不足和来源 URL。网页、视频和 GitHub 的统一融合不属于 v0.5。

## v0.6 视频主题契约

- `TopicTask.video_runs` 记录每个已确认视频的父主题 run、子 run、状态、时长、模型调用数、证据路径和失败原因。
- 视频子 run 位于主题 run 的 `video_runs/` 下，复用现有单视频阶段至 `timeline_merge`；`evidence_only` 模式不会生成独立 skill 包、蒸馏或评分产物。
- 成功视频的 manifest 与 `EvidenceTimeline` 写入 `topic_package/evidence/video-*.json`；失败视频只更新子 run 和 `video_processing_audit.json`，不阻止其他视频继续处理。
- 主题处理在媒体下载前检查累计视频时长，在视觉阶段按 `max_model_calls` 限制远程帧分析调用；本地 faster-whisper ASR 的费用估算为零。
- 主题子 run 默认复用自身 `video_runs/` 缓存；显式 `refresh_cache` 会重建对应子 run。网页、视频和 GitHub 的结论融合不属于 v0.6。

## v0.7 GitHub 主题契约

- `topic process` 只处理技术模式中用户已确认的公开 `github` 候选；普通模式会跳过 GitHub 来源，不把仓库页面当作普通网页处理。
- GitHub 处理只读取公开仓库元信息、仓库树和受限数量的文本文件，不 clone 仓库、不执行仓库代码、不安装依赖，也不使用 cookie 或登录态。默认匿名读取；可选 token 只随 GitHub API 请求发送，绝不写入主题任务、审计或 evidence。
- 每个成功来源在 `topic_package/evidence/` 写入 `github-<candidate-id>.json`，在 `topic_package/references/` 写入同名 Markdown 摘要；`github_processing_audit.json` 分别记录成功、失败和跳过来源。
- GitHub evidence 必须包含仓库元信息、读取文件、分类 findings、0–100 质量分、0–1 置信度、风险和 limitations；已有 skill/Agent 格式材料只作为降级参考，不直接成为最终 skill。
- 匿名 GitHub API 限流时，可改从公开 raw 地址读取 `main/master` 分支的 README；该 evidence 必须标记 `github_api_rate_limit_fallback`、`github_file_selection_truncated`，并在 limitations 中说明只读取了 README。
- GitHub-only 技术主题可凭 GitHub 证据完成来源处理阶段。网页、视频和 GitHub 的统一结论融合不属于 v0.7。

## 当前 Generate 产物契约

- 普通模式和技术模式都必须生成 `topic_package/COURSE.md`。它是面向学习者的主要成果，至少包含学习目标、课程导览、教学单元、常见误区或边界、自测或实践，以及可定位证据引用。
- 配置文本模型时，教学蒸馏输入只能来自 `fusion.json` 中已保存的结论和引用。课程中的引用必须匹配已有 `source_id:locator`，不得由模型发明。
- 教学模型不可用、超时或输出结构不合规时，生成器可退回确定性证据提纲，但必须在正文和 `course_audit.json` 中明确标记降级原因，不得冒充完整课程。
- `knowledge.md` 是审核文档，保留关键结论、冲突、证据缺口、来源质量、风险和引用说明；它不再承担主要教学正文职责。
- 技术模式在课程和审核文档之外生成 `SKILL.md` 与 `score.json`。评分的 documentation 维度必须检查课程、审核文档和 reference 索引，而不能只检查 Skill 文件存在。
- Web 结果 API 对主题返回 `course`、`knowledge`、可选 `skill`、`score` 和 `fusion`；结果页优先展示课程，技术模式随后展示 AI Skill。

## 通用规则

## 监工展示证据契约

- Playwright 流程必须保留 Trace；截图只在事件驱动的稳定页面状态登记，不按时间连续连拍。
- `bug_reproduction` 和 `bug_fixed` 只能作为内部诊断证据，不能进入 `capture-index.md` 或 `showcase.html`。
- 展示帧最多 5 张，必须带叙事节点、评分、理由和关联产物；低于展示阈值或证据不足时保持未选中。
- `showcase.html` 和 `capture-index.md` 只能引用相对路径，脱离开发服务器也必须能打开；展示副本不得含 token、cookie、Authorization 或查询参数。

- 时间必须能追溯到视频时间戳。
- 证据必须带来源类型和置信度。
- 路径默认使用仓库相对路径或用户配置的输出目录路径。
- 不记录 API key、cookie、token 或临时下载 URL。
- 证据不足时生成失败审计包，不生成伪完整 `SKILL.md`。

## 状态语义

- `passed`：高质量候选，但不自动入库。
- `needs_review`：生成候选包，等待人工复核。
- `failed`：不建议入库；证据不足或处理失败时保留审计包。
