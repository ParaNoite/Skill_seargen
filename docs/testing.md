# 测试规范

## 默认检查

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests
```

涉及 CLI 的改动：

```powershell
$env:PYTHONPATH='src'; python -m skill_gather --help
```

## 测试分层

| 类型 | 路径 | 用途 |
|---|---|---|
| 单元测试 | `tests/test_*.py` | schema、配置、来源识别、评分策略、纯函数 |
| 集成测试 | 后续 `tests/integration/` | CLI 与本地 fixture 串联 |
| 外部工具 smoke test | 后续按模块添加 | `yt-dlp`、`ffmpeg`、newapi 的最小可用性 |

## 测试原则

- 测公开接口，不测私有实现细节。
- 不依赖真实 API key、真实 cookie 或网络。
- 需要网络或外部工具的测试默认跳过，并给出清楚原因。
- fixture 必须小型、可审核，不提交视频大文件、音频、关键帧或模型输出。
