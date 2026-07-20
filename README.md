# Video Skill Gather

本项目是一个自用的本地 CLI 原型，用于从 B站公开视频证据中蒸馏可复核、可追溯的 Codex skill 候选包。

当前初始化版本只提供项目骨架、CLI 入口、核心数据结构、配置读取、来源识别、run 状态保存和评分/复核命令外壳。真实视频下载、抽帧、ASR/OCR、newapi 调用和 skill 蒸馏会在后续阶段实现。

## 项目规范

- [PROJECT.md](PROJECT.md)：架构、目录职责、模块边界和第三方管理。
- [AGENTS.md](AGENTS.md)：Agent 执行约束。
- [THIRD_PARTY.md](THIRD_PARTY.md)：第三方依赖来源、版本和 license 总账。
- [docs/project-structure.md](docs/project-structure.md)：项目结构规范。
- [docs/third-party-policy.md](docs/third-party-policy.md)：第三方管理细则。
- [docs/evidence-contract.md](docs/evidence-contract.md)：证据链 manifest 契约。
- [docs/testing.md](docs/testing.md)：测试规范。

## 安装

```bash
python -m pip install -e .
```

## 配置

复制 `configs/skill-gather.example.json` 为 `config.json`，并填写 newapi 中实际可用的模型名。根目录的 `config.example.json` 暂时保留，兼容初始化阶段已有命令。

API key 通过环境变量读取：

```powershell
$env:NEWAPI_API_KEY="your-key"
```

## CLI

```bash
skill-gather video <bilibili-url> --config config.json --out ./skills --runs ./runs
skill-gather score <skill-dir>
skill-gather inspect <run-id> --runs ./runs
```

## v0.1 边界

- 只处理单个 B站公开视频。
- 不支持 cookie、会员、付费、验证码、地区限制或绕权内容。
- YouTube 和批量处理后续再做。
- 证据不足时应明确失败并保留审计包。
- `score` 默认输出 JSON。
- `inspect` 面向人工复核，优先展示证据摘要和评分。

## 目录边界

- `src/skill_gather/adapters/`：B站、未来 YouTube 等来源适配。
- `src/skill_gather/integrations/`：`yt-dlp`、`ffmpeg`、newapi 等外部工具/API 的薄包装。
- `src/skill_gather/pipeline/`：阶段编排、断点续跑和失败审计。
- `src/skill_gather/distillers/`：RIA++ skill 蒸馏。
- `src/skill_gather/evaluators/`：规则评分和 LLM judge。
- `src/skill_gather/packaging/`：`SKILL.md`、`README.md`、`metadata.json` 打包。
- `runs/`、`skills/`、`third_party/` 下本地产物默认不进入 Git。
