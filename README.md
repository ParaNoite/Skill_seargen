# Video Skill Gather

本项目是一个自用的本地 CLI 原型，用于从 B站公开视频证据中蒸馏可复核、可追溯的 Codex skill 候选包。

当前初始化版本只提供项目骨架、CLI 入口、核心数据结构、配置读取、来源识别、run 状态保存和评分/复核命令外壳。真实视频下载、抽帧、ASR/OCR、newapi 调用和 skill 蒸馏会在后续阶段实现。

## 安装

```bash
python -m pip install -e .
```

## 配置

复制 `config.example.json` 为 `config.json`，并填写 newapi 中实际可用的模型名。

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
