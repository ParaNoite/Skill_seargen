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
