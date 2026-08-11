# Agent Instructions

本仓库遵循 `PROJECT.md` 和 `README.md` 中定义的项目边界。任何 Agent 在修改前必须先阅读本文件和 `PROJECT.md`。

## 执行约束

- 修改前先检查相关文件和当前 Git 状态。
- 保持改动聚焦，不顺手重构无关模块。
- 不提交真实 API key、cookie、视频、音频、关键帧、运行日志或模型输出。
- 不直接修改 `third_party/**` 下的第三方源码；必要 patch 必须记录在主仓库文档中。
- `runs/`、`skills/`、`third_party/` 下的本地产物默认不进入 Git。
- git 操作如果因为网络失败中断，必须通知用户并保留进度说明。

## “真实全量”验收声明

当用户明确要求“真实全量”时，Agent 必须执行以下强化验收流程：

- 执行全量自动化测试，并运行请求范围内的真实外部链路，不得用 fake、缓存或模拟结果替代真实验证。
- 至少进行多轮“测试 -> 直接阅读产物 -> 修复 -> 回归测试”的迭代；测试通过不等同于产物验收通过。
- 直接审核最终产物的完整性、可执行性、版本边界、引用和风险标记。证据不足的产物必须保持 `needs_review`，不得标记为可直接发布。
- 小问题应自行定位、修复、补充回归测试并继续迭代。
- 发现重大阻断问题时必须立即停止后续扩展、保留现场与证据并请求用户介入。重大问题包括核心功能缺失、必需模型或运行时不可用、API key 或权限失效、关键外部服务不可达且无合规替代路径，以及数据或产物完整性无法保证。
- 交付时必须直观展示最终产物、真实测试结果、直接审核结论、未解决风险，以及用户可复现的验收步骤。

## Python 环境

当前项目是 Python CLI 原型。默认命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m skill_gather --help
```

faster-whisper 是必需本地 ASR 后端。首次下载/加载模型权重时，如果 Hugging Face 直连超时，使用当前已验证的镜像环境变量：

```powershell
$env:HF_ENDPOINT="https://hf-mirror.com"
$env:HF_HUB_DISABLE_XET="1"
.\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('loaded base')"
```

临时本地 Web 界面统一入口：

```powershell
.\scripts\dev.cmd
npm run dev
.\scripts\start-web.cmd
```

`.\scripts\dev.cmd` / `npm run dev` 是本地开发优先入口，会优先使用仓库 `.venv`；`.\scripts\start-web.cmd` 保留为兼容入口。

如果后续加入虚拟环境入口或开发脚本，应先更新本文件，再要求 Agent 使用统一入口。
