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

## v0.2 质量基线

人工标注规则见 `benchmarks/v0.2-labeling-guide.md`。建立真实基线时，应冻结公开视频 URL、模型名、Judge 难度和标注结果，再运行：

```powershell
.\.venv\Scripts\python.exe -m skill_gather calibrate benchmarks\v0.2-labels.json
```

报告必须同时保留规则、Judge 和最终保守决策的混淆矩阵。示例文件只验证格式，不代表真实质量结论。

冻结公开视频清单位于 `benchmarks/v0.2-videos.json`。可靠性和视觉实验报告命令：

```powershell
.\.venv\Scripts\python.exe -m skill_gather benchmark-report benchmarks\v0.2-videos.json --runs .\runs
.\.venv\Scripts\python.exe -m skill_gather vision-report runs\<full-run> runs\<sampled-run> --expected-fields benchmarks\v0.2-expected-fields.json
```

`not_run_count` 必须原样报告，不能把未执行样本当作成功。视觉字段正确率依赖人工冻结的预期字段；没有标注时返回 `null`，不能据此宣称质量提升。
