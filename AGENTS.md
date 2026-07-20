# Agent Instructions

本仓库遵循 `PROJECT.md` 和 `README.md` 中定义的项目边界。任何 Agent 在修改前必须先阅读本文件和 `PROJECT.md`。

## 执行约束

- 修改前先检查相关文件和当前 Git 状态。
- 保持改动聚焦，不顺手重构无关模块。
- 不提交真实 API key、cookie、视频、音频、关键帧、运行日志或模型输出。
- 不直接修改 `third_party/**` 下的第三方源码；必要 patch 必须记录在主仓库文档中。
- `runs/`、`skills/`、`third_party/` 下的本地产物默认不进入 Git。
- git 操作如果因为网络失败中断，必须通知用户并保留进度说明。

## Python 环境

当前项目是 Python CLI 原型。默认命令：

```powershell
$env:PYTHONPATH='src'; python -m unittest discover -s tests
$env:PYTHONPATH='src'; python -m skill_gather --help
```

如果后续加入虚拟环境入口或开发脚本，应先更新本文件，再要求 Agent 使用统一入口。
