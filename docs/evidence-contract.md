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
| `TopicTask` | 主题、普通/技术模式、预算、用量、缓存、来源选择、状态、失败审计和主题包索引 |
| `TopicPackage` | 主题包中 `sources.json`、`evidence/`、`references/` 及未来文档产物的相对路径 |

## v0.4 搜索候选契约

- 搜索 provider 只返回 `RawSearchResult` 元数据，不返回网页正文、下载路径、cookie 或浏览器会话。
- `TopicSourceCandidate.candidate_id` 使用规范化 URL 的稳定哈希；`canonical_url`、`providers`、`engines`、`queries`、`quality_score`、`risk_flags` 和 `duplicate_count` 用于排序与审计。
- `topic_package/sources.json` 同时保存候选草稿、显式选择和 provider warning；`search_audit.json` 保存查询、批次、返回数量、命中引擎、缓存命中和失败信息。
- 用户确认前不得进入网页抓取、视频下载、ASR、视觉理解或 GitHub 仓库分析。确认动作只推进主题 run 到后续处理入口。

## 主题任务契约

- 主题 run 使用 `created`、`searching`、`awaiting_selection`、`processing_sources`、`generating`、`scoring`、`completed` 或 `failed` 状态。
- `TopicBudget` 限制候选数、已选来源数、视频时长、模型调用、费用估算和运行时间；来源处理阶段必须通过 `TopicRunStore.record_usage()` 写入实际用量。超限时任务标记为 `failed`，并记录失败阶段和原因。
- `TopicCachePolicy` 默认复用缓存；显式刷新意图必须写入主题任务，供后续搜索和来源处理阶段读取。
- 主题包路径必须相对主题 run 目录，且不得记录 API key、cookie、token 或临时下载 URL。

## 通用规则

- 时间必须能追溯到视频时间戳。
- 证据必须带来源类型和置信度。
- 路径默认使用仓库相对路径或用户配置的输出目录路径。
- 不记录 API key、cookie、token 或临时下载 URL。
- 证据不足时生成失败审计包，不生成伪完整 `SKILL.md`。

## 状态语义

- `passed`：高质量候选，但不自动入库。
- `needs_review`：生成候选包，等待人工复核。
- `failed`：不建议入库；证据不足或处理失败时保留审计包。
