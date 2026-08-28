# skill_seargen

`skill_seargen`（search and generate）是一个本地 Search × Generate 原型：广泛搜索公开视频、公开网页与代码中的真实经验，再把多模态证据同时蒸馏为人类可学习的课程和 AI 可执行的 Skill。

当前本地 Web 版本包含首页、Skill 目录和工作台。目录用于展示人工挑选的结果；工作台用于搜索来源、处理证据，并在结果页优先阅读 `COURSE.md`，技术模式下继续查看 `SKILL.md`、评分与审计。

当前目录是源码仓库内的本地演示数据，运行时读取仓库根目录的 `catalog/`；暂不承诺从 wheel 安装后携带这批目录内容。

## 项目文档

当前使用：

- [PROJECT.md](PROJECT.md)：架构、目录职责、模块边界和第三方管理。
- [AGENTS.md](AGENTS.md)：Agent 执行约束。
- [THIRD_PARTY.md](THIRD_PARTY.md)：第三方依赖来源、版本和 license 总账。
- [docs/evidence-contract.md](docs/evidence-contract.md)：证据链 manifest 契约。
- [docs/v1.1-closure.md](docs/v1.1-closure.md)：v1.1 研究自动化、API 与前端工作台验收说明。
- [docs/frontend-user-guide.md](docs/frontend-user-guide.md)：面向第一次使用产品的客人，说明首页、Skill 目录和前端工作台的完整操作流程。
- [docs/testing.md](docs/testing.md)：测试规范。
- [docs/project-structure.md](docs/project-structure.md)：项目结构规范。
- [docs/third-party-policy.md](docs/third-party-policy.md)：第三方管理细则。

历史记录：`docs/v0.*`、`docs/v1.0-*`、`docs/v1.0-tickets.md` 和 `video-skill-gather-v0.1-plan.md` 保留对应版本的范围、决策与验收结果。

历史文档可能使用当时的旧产物名称。当前产品行为以本 README、`PROJECT.md`、`docs/evidence-contract.md` 和前端用户指南为准。

## 当前产物

Generate 不是只做可信度汇总。所有成功主题都会先生成给人阅读的课程；技术模式再生成给 AI 使用的执行能力。

| 产物 | 面向对象 | 用途 |
|---|---|---|
| `COURSE.md` | 学习者 | 课程目标、导览、教学单元、误区、自测和实践；结果页默认优先展示 |
| `knowledge.md` | 审核者 | 结论级引用、冲突、证据缺口、来源质量与风险 |
| `SKILL.md` | AI | 技术模式的触发条件、执行步骤、完成标准和边界 |
| `score.json` | Judge / 审核者 | 技术课程与 Skill 的结构、证据、可执行性和边界评分 |
| `course_audit.json` | 审核者 | 教学蒸馏使用的模型或降级原因，不保存原始模型正文 |

配置了可用文本模型时，Generate 会把有引用的融合证据重写成结构化中文课程。模型不可用、超时或响应不合规时，任务仍可生成明确标注的“证据提纲”降级版，不会把转录拼接冒充完整课程。

## 从零部署与验收

### 前置条件

- Windows PowerShell、Python 3.11 或更高版本、Node.js 20 或更高版本。
- 视频处理需要本地 `ffmpeg` 与 `yt-dlp`；首次启用 ASR 时需要联网下载 faster-whisper 模型。
- 真实主题搜索需要可访问的 SearXNG 实例；真实视觉、课程蒸馏与 Judge 需要设置 `NEWAPI_API_KEY`。未配置外部服务时，仍可运行单元测试、CLI 帮助、MVP 自检和离线验收。

### 安装步骤

```powershell
# 1. 创建并激活 Python 环境
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装 Python 与前端依赖
python -m pip install --upgrade pip
python -m pip install -e .
npm install

# 3. 创建本地配置文件；config.json 已被 Git 忽略
Copy-Item configs\skill-gather.example.json config.json

# 4. 可选：为真实模型阶段设置当前 PowerShell 会话的密钥
$env:NEWAPI_API_KEY="your-key"
```

配置样例使用保留域名 `https://api.example.test/v1`，不能直接请求。将 `providers.newapi.base_url` 改为你自己的 OpenAI-compatible NewAPI 服务地址，并只在当前 PowerShell 会话中设置密钥：

```powershell
$env:NEWAPI_API_KEY="your-key"
```

`config.json` 已被 Git 忽略；不要将 API key、cookie、token、个人机器路径或真实服务地址写入配置样例或提交到 Git。

### Docker 与 SearXNG（真实搜索必需）

SearXNG 仅用于真实主题搜索；离线测试和 `--fake` 搜索不需要 Docker。先安装并启动 Docker Desktop，再确认 Docker daemon 已就绪：

```powershell
docker version
```

以下命令在仓库下创建一个本地 SearXNG 配置并仅绑定到回环地址。`settings.yml` 位于 `.local/`，不会进入 Git。镜像先使用官方当前标签；用于长期部署前，请通过 `docker image inspect` 记录并固定其 digest。

```powershell
$searxDir = Join-Path $PWD ".local\searxng"
New-Item -ItemType Directory -Force $searxDir | Out-Null
$searxSecret = [guid]::NewGuid().ToString("N")
@"
use_default_settings: true
server:
  secret_key: $searxSecret
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
formats:
  - html
  - json
"@ | Set-Content -Encoding ascii (Join-Path $searxDir "settings.yml")

docker run -d --name skill-seargen-searxng --restart unless-stopped --publish 127.0.0.1:8080:8080 --volume "${searxDir}:/etc/searxng:rw" searxng/searxng:latest
docker image inspect searxng/searxng:latest --format '{{index .RepoDigests 0}}'
```

验证 JSON 接口后，将地址写入本地 `config.json` 的 `search.searxng_base_url`：

```powershell
Invoke-RestMethod "http://127.0.0.1:8080/search?q=skill_seargen&format=json" | Select-Object -ExpandProperty results -First 1
# 在 config.json 中设置："searxng_base_url": "http://127.0.0.1:8080"
.\.venv\Scripts\python.exe -m skill_gather model-check --config config.json
```

`model-check` 会调用配置的 NewAPI 服务，因而可能产生外部请求或费用；只验证 SearXNG 时运行前一条 `Invoke-RestMethod` 即可。停止并删除本地搜索服务：

```powershell
docker rm --force skill-seargen-searxng
```

首次安装 ASR 模型时运行：

```powershell
npm run setup:local
```

该脚本使用仓库 `.hf-cache` 保存模型缓存。具备 NVIDIA CUDA 环境时可设置 `SKILL_GATHER_FASTER_WHISPER_DEVICE=cuda` 与 `SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE=float16`；其他环境使用 CPU/int8：

```powershell
$env:SKILL_GATHER_FASTER_WHISPER_DEVICE="cpu"
$env:SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE="int8"
```

### 启动与本地验收

```powershell
# 原前端，默认 http://127.0.0.1:8765
npm run dev

# 学校主题第二前端，默认 http://127.0.0.1:8766
npm run start2

# 自动化与命令入口检查
npm test
npm run cli
.\.venv\Scripts\python.exe -m skill_gather mvp-check --config configs\skill-gather.example.json
.\.venv\Scripts\python.exe -m skill_gather acceptance --dataset benchmarks\v1.0-topics.json --config configs\skill-gather.example.json --runs .\runs\v1.0-offline-acceptance
```

## 配置

复制配置样例并按需填写：

```powershell
Copy-Item configs\skill-gather.example.json config.json
```

`config.json` 中需要配置 newapi 可用的模型名。根目录的 `config.example.json` 暂时保留，用于兼容初始化阶段已有命令。

API key 建议通过环境变量读取：

```powershell
$env:NEWAPI_API_KEY="your-key"
```

不要把真实 API key、cookie、token 或个人绝对路径提交进 Git。

### 主题任务默认值

`topic_defaults` 用于约束后续主题研究的候选数量、来源数量、视频时长、模型调用、费用和运行时间。`reuse_cache` 默认启用；`refresh_cache` 记录显式刷新意图，供后续的搜索和来源处理阶段使用。示例配置已包含默认值。

### NewAPI 模型与阶段参数

`providers.newapi` 中的 `vision_model`、`distiller_model` 和 `judge_model` 可配置为 NewAPI 后台可调用的模型名，例如 `gpt-5.6-sol`。ASR 仍是本地主链路，`asr_model` 必须保持 `faster-whisper:<模型名或本地路径>`。

`timeout_sec` 控制 NewAPI 聊天请求超时；`request_profiles` 可按阶段覆盖请求参数：

- `probe`：`model-check` 的最小可用性探测。
- `vision`：视频帧视觉/OCR。
- `search`：语义计划、查询扩展和候选复核。
- `course`：面向人的 `COURSE.md` 蒸馏。
- `skill`：单视频 RIA++ Skill 蒸馏。
- `judge`：评分与风险判断。

支持的字段包括 `temperature`、`top_p`、`max_tokens`、`max_completion_tokens` 和 `reasoning_effort`。如果服务端对某个模型不接受某个字段，请先删掉对应 profile 字段后再运行 `model-check`。

## ASR 模型

ASR 是 v0.1 主链路的必需能力，默认使用本地 faster-whisper。推荐从较小模型开始验证：

```powershell
.\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('loaded base')"
```

如果本机有可用的 NVIDIA GPU，可以在配置中使用更高质量的模型：

```json
"asr_model": "faster-whisper:large-v3-turbo"
```

`large-v3-turbo` 可以替换为 faster-whisper 支持的模型名或本地模型路径。也可以通过环境变量控制运行设备和计算类型：

```powershell
$env:SKILL_GATHER_FASTER_WHISPER_DEVICE="cuda"
$env:SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE="float16"
```

首次加载模型会下载权重到本机 Hugging Face 缓存，模型权重不进入 Git。国内网络如果 Hugging Face 直连超时，可在当前 PowerShell 会话里设置镜像：

```powershell
$env:HF_ENDPOINT="https://hf-mirror.com"
$env:HF_HUB_DISABLE_XET="1"
$env:HF_HOME="$PWD\.hf-cache"
```

直接运行 CLI 时，如果这些变量没有显式设置，程序会自动使用上述镜像设置和项目根目录下的 `.hf-cache`；用户提供的环境变量始终优先。

## CLI 使用

### OpenCode Agent 组装

学校主题前端提供独立“组装”页：从可下载 Skill 货架勾选能力后生成 `skill-seargen-agent.zip`。解压并用 OpenCode 打开其中的 `skill-seargen-agent` 目录即可使用。

也可以通过模块方式调用：

```powershell
.\.venv\Scripts\python.exe -m skill_gather --help
```

常用命令说明：

- `video`：处理单个 B 站公开视频，并生成候选包或失败审计包。
- `score`：对已有候选 skill 目录重新评分。
- `inspect`：查看 run 的证据摘要、阶段状态和评分结果。
- `review`：将人工的 `usable / needs_changes / unusable` 结论独立写入 `human_review.json`。
- `calibrate`：比较规则、Judge、最终决策与人工标签，输出三套混淆矩阵。
- `benchmark-report`：汇总冻结视频集的执行数、失败数、重试、退出码和模型审计覆盖。
- `vision-report`：比较多个视觉实验 run 的远程调用数、耗时和人工定义字段正确率。
- `mvp-check`：使用 fake clients 离线验证候选包与失败审计两条主分支。
- `model-check`：以最小文本和视觉请求探测配置模型的真实可用性；`/models` 返回的目录只用于建议，不能证明模型可调用。
- `topic create`：创建或恢复普通/技术模式的主题任务，并保存预算、缓存和主题包索引。
- `topic search`：按模式调用搜索 provider，写入候选、查询审计和 `sources.json`；`--fake` 用于离线验证。
- `topic candidates`：查看候选 ID、来源类型、质量分、风险和查询来源。
- `topic select`：显式确认候选来源；v0.4 原子写入 `TopicTask.selected_sources` 与 `topic_package/sources.json`，状态进入 `processing_sources`，但不触发下载、抓取、ASR、视觉分析或生成。
- `topic process`：处理已确认的网页、公开视频和技术模式 GitHub 来源，统一生成面向人的 `COURSE.md`、面向审核的 `knowledge.md`、`fusion.json` 和 `course_audit.json`；技术模式额外生成 `SKILL.md` 和分维度 `score.json`。视频处理可通过 `--config`、`--vision-mode` 和 `--vision-frame-limit` 控制；普通模式不会强行处理 GitHub。
- `topic inspect`：以 JSON 查看主题 run 的阶段、失败信息和将来产物路径。
- `topic resume`：将失败的主题 run 恢复到失败前的阶段，并保留失败审计信息。
- `topic rerun`：从 `processing_sources`、`generating` 或 `scoring` 阶段重跑，并将请求写入 `rerun_audit.json`。
- `topic review`：记录技术主题 skill 的人工复核；人工结论不能补造证据，自动失败只能提升为 `needs_review`。

失败 run 的 `video` 命令退出码为 `1`，参数或配置错误为 `2`。脚本和后续批处理不能只解析输出文本，应同时检查退出码和 JSON 中的 `status`。

视觉成本实验示例：

```powershell
skill-gather video <bilibili-url> --config config.json --vision-mode sampled --vision-frame-limit 12
skill-gather video <bilibili-url> --config config.json --vision-mode sampled --vision-frame-limit 12 --run-variant sampled-12
skill-gather video <bilibili-url> --config config.json --vision-mode off --run-variant vision-off
```

每个 run 的 `vision_ocr.json` 记录 `source_frame_count`、`analyzed_frame_count`、`remote_call_count` 和 `strategy`。`stage_timings.json` 可用于同一输入、机器和模型配置下的墙钟时间比较。

同一 run 一旦完成阶段，就不能改用不同的 Judge 或视觉参数恢复；必须使用不同的 `--run-variant`，防止实验结果混用旧产物。

### 搜索配置

`configs/skill-gather.example.json` 的 `search` 节包含搜索超时、缓存 TTL、Bilibili JSON 搜索入口及其公开搜索页回退地址、GitHub API 地址和 SearXNG 地址。Bilibili JSON 入口返回 412 时，会只解析公开搜索结果页中的视频卡片，不访问候选视频页；该回退不使用 cookie、登录、浏览器自动化或验证码绕过。GitHub 默认匿名读取公开仓库；可选 token 通过 `GITHUB_TOKEN`（或 `github_token_env` 指定的变量）提供，只随 GitHub API 请求发送，不写入配置或 run。GitHub 读取遇到 TLS EOF、连接重置或超时时会执行有限退避重试；匿名 API 限流时会降级读取公开仓库的 README，并在 evidence 中标记限制。

每次搜索都会先生成可审计的检索意图，再执行 provider 查询、URL/来源检查、去重和排序。默认完全使用确定性规则；`use_newapi_query_expansion` 可让 NewAPI 补充意图侧面和至多两条查询变体，`use_newapi_candidate_assessment` 可批量复核候选元数据并参与排序。两者均为可选增强：无 key、超时或响应无效时自动回退规则结果，且不会声称读取候选页面或观看视频。

普通模式调用 Bilibili + SearXNG，技术模式额外调用 GitHub。单个 provider 失败时保留其他已成功的候选和 warning；所有启用的 provider 都失败时，主题 run 才标记为 `failed`。未配置 SearXNG 时，真实搜索会明确报配置错误；测试和演示使用 `--fake`。

SearXNG 是独立的 AGPL-3.0 本地服务。本项目只调用其 JSON HTTP 接口，不复制源码、不捆绑镜像。它不需要商业搜索 key，但搜索词会发送给实例启用的上游引擎。

## 本地 Web 界面

本地 Web 界面包含三个入口：

- 首页：以 `Search × Generate` 展示“发现来源”和“蒸馏课程与能力”两条同等重要的能力轨道。
- 浏览 Skills：读取 `catalog/catalog.json`，展示 17 个带来源、版本、许可证和下载状态的条目。
- 工作台：保留主题创建、搜索、选源、视频提交、运行进度、证据、评分和 MVP 自检能力。

工作台“结果”页会先显示“人类课程”；技术主题在同一结果中继续显示“AI Skill”。可信度、冲突和风险保留在审计产物中，不占据主要学习正文。

目录中的外部 Skill 默认只建立来源索引。只有本项目原创或许可证明确允许再分发、且包位于 `catalog/packages/` 下的条目才会提供 ZIP 下载。

导航与视图状态使用 React 19，并由 Vite 构建为 Python 包内的静态资源。修改 `frontend/` 后运行：

```powershell
npm install
npm run frontend:build
```

推荐开发启动入口：

```powershell
npm run dev
```

如果 PowerShell 拦截 `npm.ps1`，使用等价命令：

```powershell
npm.cmd run dev
```

等价的 Windows 脚本入口：

```powershell
.\scripts\dev.cmd
```

这条命令会启动后端服务，并由后端直接托管前端页面。默认访问地址：

```text
http://127.0.0.1:8765
```

兼容启动入口：

```powershell
.\scripts\start-web.cmd
```

可以通过参数指定 Python、配置文件和端口：

```powershell
.\scripts\dev.cmd -Python .\.venv\Scripts\python.exe -Config config.json -Port 8765
```

Web 表单中的 API key 只会写入当前 Web 进程的环境变量，不会写回配置文件，也不会在响应里回显。

本机稳定模式：首次安装或 ASR 模型变更后运行 npm run setup:local；日常使用 npm start。
npm start 固定优先使用仓库 .venv，在启动前检查配置和本地 ASR 依赖，并使用仓库
.hf-cache、CUDA 和 float16。完整功能检查仍会真实访问外部服务，因此外部服务临时故障
无法由本地配置消除。

### 学校主题第二前端

原前端与学校主题前端相互独立：

```powershell
npm start
npm run start2
```

`npm start` 继续启动原前端，默认地址为 `http://127.0.0.1:8765`。`npm run start2` 会先构建 `frontend-school/`，再用独立静态资源目录启动学校主题前端，默认地址为 `http://127.0.0.1:8766`。两套前端使用同一组后端 API 和本地 run 数据。

注意：npm 的自定义脚本需要 `run` 关键字，直接执行 `npm start2` 会得到 `Unknown command: "start2"`。

## v1.0 离线验收

固定验收集位于 `benchmarks/v1.0-topics.json`，包含普通教程、技术流程、中英文材料、视频、网页、GitHub、低质量来源、冲突和证据不足十类场景。运行：

```powershell
.\.venv\Scripts\python.exe -m skill_gather acceptance --dataset benchmarks/v1.0-topics.json --config configs/skill-gather.example.json --runs .\runs\v1.0-offline-acceptance
```

该命令使用离线 fake 搜索，输出明确标记为 `synthetic`，仅验证主题 run、预算、候选和搜索审计契约；不代表真实网页、Bilibili、GitHub、ASR、视觉模型或 NewAPI 已通过。真实服务 smoke test 的操作见 `docs/v1.0-closure.md`。

## Judge 难度

Judge 难度可在 Web 页面选择，也可在 CLI 中通过 `--judge-difficulty` 指定：

- `lenient`（宽松）：80 分通过，60 分进入人工复核。
- `standard`（标准）：85 分通过，70 分进入人工复核。
- `strict`（严格）：90 分通过，78 分进入人工复核。
- `off`（关闭 Judge）：跳过 LLM judge；只要蒸馏阶段产出候选草案，就直接生成 `needs_review` 候选包。

难度会同时影响 LLM judge 的评分提示词、最终状态门槛，并记录在 `score.json` 和 Web 详情中。

## v0.1 边界

- 只处理单个 B 站公开视频。
- 不支持 cookie、会员、付费、验证码、地区限制或绕权内容。
- YouTube 和批量处理后续再做。
- 证据不足时应明确失败，并保留带阶段、风险、证据概况和重跑提示的审计包。
- `score` 默认输出 JSON。
- `inspect` 面向人工复核，优先展示证据摘要和评分。
- 候选包 README 面向人工复核，默认包含来源、风险、评分和证据摘要。

## 目录边界

- `src/skill_gather/adapters/`：B 站、未来 YouTube 等来源适配。
- `src/skill_gather/integrations/`：`yt-dlp`、`ffmpeg`、newapi、faster-whisper 等外部工具/API 的薄包装。
- `src/skill_gather/pipeline/`：阶段编排、断点续跑和失败审计。
- `src/skill_gather/distillers/`：RIA++ skill 蒸馏。
- `src/skill_gather/evaluators/`：规则评分和 LLM judge。
- `src/skill_gather/packaging/`：`SKILL.md`、`README.md`、`metadata.json` 打包。
- `runs/`、`skills/`、`third_party/` 下本地产物默认不进入 Git。

## 测试

常规检查：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m skill_gather --help
```

只检查 Web 相关逻辑：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_web
```
