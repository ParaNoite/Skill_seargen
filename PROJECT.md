# skill_seargen 项目规范

> 本文件负责架构、目录职责、模块边界和第三方管理。`AGENTS.md` 负责 Agent 执行约束；`video-skill-gather-v0.1-plan.md` 保留 v0.1 决策，`docs/v0.1-to-v1.0-roadmap.md` 定义后续版本范围。

## 1. 项目定位

`skill_seargen` 是一个本地 CLI 原型，用于从公开视频证据中蒸馏可复核、可追溯的 Codex skill 候选包，并为主题研究任务提供本地可恢复状态。

v0.1 不追求完整视频下载器或通用多平台系统，只跑通：

```text
B站公开视频 URL
  -> 元数据与媒体采集
  -> 抽帧、ASR、OCR/视觉理解
  -> EvidenceTimeline
  -> RIA++ skill 候选
  -> 规则评分 + LLM judge
  -> 候选包或失败审计包
```

v0.2 保持单个 B 站公开视频边界，增加失败可识别、蒸馏可重试、模型响应可脱敏审计、评分可拆分校准，以及视觉成本实验入口。

v0.3 新增主题任务与主题包契约、主题级 run、预算和缓存策略。v0.4 在此基础上接入 Fake、Bilibili、GitHub 和 SearXNG 搜索、确定性语义意图、可选 NewAPI 增强、候选确认和搜索审计。Bilibili 仅访问公开搜索入口，JSON 入口返回 412 时可回退解析公开搜索结果页；不使用 cookie、登录、浏览器自动化或验证码绕过。v0.5 处理普通模式中已确认的公开网页：提取正文、保存轻量快照和网页证据，并生成中文 `knowledge.md`。v0.6 将公开视频接入主题子 run，汇入视频时间线证据，隔离单视频失败并执行时长和模型调用预算；跨来源结论融合仍留待后续版本。

## 2. 目录职责

| 路径 | 职责 | 修改边界 |
|---|---|---|
| `PROJECT.md` | 架构、目录职责、第三方和模块边界 | 接口或边界变化时同步更新 |
| `AGENTS.md` | Agent 执行约束 | 修改前说明原因 |
| `README.md` | 安装、配置、运行入口 | 面向使用者，避免塞入详细架构 |
| `video-skill-gather-v0.1-plan.md` | v0.1 产品规格和验收标准 | 产品决策变更时同步 |
| `docs/` | 项目结构、第三方策略、证据契约、测试规范 | 存文档，不存运行产物 |
| `configs/` | 可复现配置和配置样例 | 不含真实密钥、cookie 或个人绝对路径 |
| `src/skill_gather/` | 主仓库拥有的 CLI、schema、编排和薄适配代码 | 不复制第三方核心算法 |
| `tests/` | 单元测试和后续集成测试 | 按模块归属放置 |
| `runs/` | 本地 run 状态、审计包和中间产物 | 默认不进 Git |
| `skills/` | 本地生成的候选 skill 包 | 默认不进 Git，受控样例需单独说明 |
| `third_party/` | 本地 clone 的外部后端或 fork | 源码默认不由主仓库跟踪 |

## 3. 源码模块归属

| 模块 | 路径 | 职责 |
|---|---|---|
| CLI | `src/skill_gather/cli.py` | 命令入口、参数解析、面向用户的错误语义 |
| 数据模型 | `src/skill_gather/models.py` | Manifest、EvidenceTimeline、RunState、评分结果 |
| 主题任务 | `src/skill_gather/topics.py` | 主题 run 的持久化、状态推进、预算用量、失败审计与恢复 |
| 来源适配 | `src/skill_gather/adapters/` | B站、未来 YouTube 等平台适配 |
| 外部集成 | `src/skill_gather/integrations/` | `yt-dlp`、`ffmpeg`、newapi 等工具/API 的薄包装 |
| 管线编排 | `src/skill_gather/pipeline/` | 阶段调度、断点续跑、失败审计 |
| skill 蒸馏 | `src/skill_gather/distillers/` | RIA++ 蒸馏、模板生成 |
| 评分评估 | `src/skill_gather/evaluators/` | 固定规则评分和 LLM judge |
| 质量评测 | `src/skill_gather/evaluation.py` | 人工标签映射、混淆矩阵与校准报告 |
| 打包输出 | `src/skill_gather/packaging/` | `SKILL.md`、`README.md`、`metadata.json` 写出 |

通用逻辑只有在至少两个模块实际复用、接口稳定后，才提取共享模块。不要提前建立宽泛的 `utils.py`。

## 4. 第三方后端管理

本项目采用分层管理：

- Python 包依赖进入 `pyproject.toml`。
- `yt-dlp`、`ffmpeg` 这类外部命令默认不入仓，由集成层检测并调用。
- faster-whisper 是 v0.1 主链路必需的本地 ASR 后端，由 `integrations/faster_whisper.py` 薄包装访问；模型权重、缓存和转写产物不入仓。
- newapi 是外部服务，只通过 `integrations/newapi.py` 之类的薄包装访问，负责视觉、蒸馏和 judge；v0.1 不把 newapi 远端 ASR 作为替代路径。
- 真正 fork 或本地 clone 的第三方源码放在 `third_party/<project-name>/`，源码默认不由主仓库跟踪。
- 第三方来源、版本、license、patch 和调用方式登记到 `THIRD_PARTY.md` 或模块文档。

每项第三方依赖至少记录：

- 名称、用途和使用方式。
- 来源 URL 和许可证。
- 版本、完整 commit SHA 或可复现的包版本。
- 安装方式和运行前置条件。
- 是否随主仓库分发。
- 本地 patch 路径、SHA-256 和修改原因。
- 由哪个主仓库模块调用，输入输出格式是什么。

不得以 `latest`、`main` 或浮动 tag 作为唯一版本标识。

## 5. Manifest 接口规则

跨阶段数据必须通过结构化 manifest 或 metadata 交接，不依赖目录顺序、文件修改时间或个人机器路径。

核心接口包括：

- `VideoSourceManifest`
- `FrameManifest`
- `EvidenceTimeline`
- `RunState`
- `SkillPackageMetadata`
- `TopicTask`
- `TopicPackage`

主题任务通过 `TopicTask` 交接主题、模式、预算、实际用量、缓存策略、候选来源、已选来源、视频子 run、阶段状态、失败记录和主题包索引。`TopicPackage` 中的路径必须相对主题 run 目录；v0.4 额外写入候选、选择和 `search_audit.json`。确认动作原子写入 `selected_sources` 与 `sources.json`，并进入 `processing_sources`。v0.5 写入网页结构化证据、文本快照和 `knowledge.md`；v0.6 额外在 `video_runs/` 建立子 run，并将成功视频的 `EvidenceTimeline` 写入主题包。`SKILL.md` 与跨来源融合仍由后续阶段写入。

所有 manifest 和运行记录必须使用仓库相对路径或用户显式配置的输出路径，不得写入凭据、cookie 或临时下载 URL。

## 6. 运行产物边界

以下内容默认不进 Git：

- 原始视频、音频、关键帧和截图。
- `runs/` 下的中间文件、审计包和日志。
- `skills/` 下的本地候选包，除非是专门审核过的小型 fixture。
- 第三方源码 clone、模型权重、缓存和虚拟环境。
- 真实配置、API key、cookie、token。

## 7. 测试与完成定义

常规改动至少运行：

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests
```

涉及 CLI 的改动还应运行：

```powershell
$env:PYTHONPATH='src'; python -m skill_gather --help
```

完成交付时必须说明：

- 改了哪些项目边界或接口。
- 跑了哪些检查。
- 哪些检查因为环境、网络或依赖原因没能运行。
