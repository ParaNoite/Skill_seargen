# Video Skill Gather

Video Skill Gather 是一个本地 Python CLI 原型，用于从 B 站公开视频证据中蒸馏可复核、可追溯的 Codex skill 候选包。

当前版本是 v0.2，在 v0.1 视频主链路上补齐失败退出码、蒸馏有限重试、脱敏模型审计、规则评分分项、人工复核与校准报告。视觉阶段支持全帧、抽样和关闭三种实验模式，并记录远程调用数；缺少标题且明显过短的视频会在下载前被低成本预筛拒绝。

## 项目文档

- [PROJECT.md](PROJECT.md)：架构、目录职责、模块边界和第三方管理。
- [AGENTS.md](AGENTS.md)：Agent 执行约束。
- [THIRD_PARTY.md](THIRD_PARTY.md)：第三方依赖来源、版本和 license 总账。
- [docs/project-structure.md](docs/project-structure.md)：项目结构规范。
- [docs/third-party-policy.md](docs/third-party-policy.md)：第三方管理细则。
- [docs/evidence-contract.md](docs/evidence-contract.md)：证据链 manifest 契约。
- [docs/testing.md](docs/testing.md)：测试规范。

## 安装

建议在虚拟环境中安装本项目：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

如果你已经激活了虚拟环境，也可以直接运行：

```bash
python -m pip install -e .
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

## CLI 使用

```bash
skill-gather video <bilibili-url> --config config.json --out ./skills --runs ./runs
skill-gather score <skill-dir>
skill-gather inspect <run-id> --runs ./runs
skill-gather review <run-id> --runs ./runs --label needs_changes --notes "边界需要补充"
skill-gather calibrate benchmarks/v0.2-labels.example.json
skill-gather benchmark-report benchmarks/v0.2-videos.json --runs ./runs
skill-gather vision-report runs/<full-run> runs/<sampled-run> --expected-fields benchmarks/v0.2-expected-fields.example.json
skill-gather mvp-check --config configs/skill-gather.example.json
```

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

失败 run 的 `video` 命令退出码为 `1`，参数或配置错误为 `2`。脚本和后续批处理不能只解析输出文本，应同时检查退出码和 JSON 中的 `status`。

视觉成本实验示例：

```powershell
skill-gather video <bilibili-url> --config config.json --vision-mode sampled --vision-frame-limit 12
skill-gather video <bilibili-url> --config config.json --vision-mode sampled --vision-frame-limit 12 --run-variant sampled-12
skill-gather video <bilibili-url> --config config.json --vision-mode off --run-variant vision-off
```

每个 run 的 `vision_ocr.json` 记录 `source_frame_count`、`analyzed_frame_count`、`remote_call_count` 和 `strategy`。`stage_timings.json` 可用于同一输入、机器和模型配置下的墙钟时间比较。

同一 run 一旦完成阶段，就不能改用不同的 Judge 或视觉参数恢复；必须使用不同的 `--run-variant`，防止实验结果混用旧产物。

## 本地 Web 界面

临时 Web 界面覆盖 API key 填写、视频提交、run 列表、处理进度、证据查看、评分查看和 MVP 自检。处理逻辑仍复用 CLI 管线。

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
