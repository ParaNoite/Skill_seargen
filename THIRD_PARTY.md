# Third-Party Inventory

本文件记录项目使用、计划使用或参考的第三方依赖。真实第三方源码默认不由主仓库跟踪。

| 名称 | 用途 | 使用方式 | 来源 | 版本 / commit | License | 是否入仓 | 备注 |
|---|---|---|---|---|---|---|---|
| yt-dlp | B站元数据和媒体流采集 | external-command | https://github.com/yt-dlp/yt-dlp | 待固定 | Unlicense | 否 | 由 `integrations/` 薄包装调用 |
| FFmpeg | 抽音频、抽帧、转码 | external-command | https://github.com/FFmpeg/FFmpeg | 待固定 | LGPL/GPL 取决于构建 | 否 | 运行时检测系统命令 |
| PySceneDetect | 场景变化检测 | python-dependency / external-tool | https://github.com/Breakthrough/PySceneDetect | 待固定 | BSD-3-Clause | 否 | 后续进入 `pyproject.toml` |
| newapi | 云端视觉、ASR、蒸馏和 judge | external-service | https://api.renice.cc/ | 配置文件指定 | 服务侧 | 否 | API key 只从环境变量读取 |
| kangarooking/cangjie-skill | RIA++ skill 蒸馏参考 | reference-only | 待补充 | 待确认 | 待确认 | 否 | 许可证确认前只参考，不复制 |

新增第三方前必须补齐：

- 来源 URL。
- 许可证。
- 固定版本、完整 commit SHA 或可复现包版本。
- 安装方式。
- 是否需要本地 patch。
- 由哪个模块调用。
