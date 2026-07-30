# Third-Party Inventory

本文件记录项目使用、计划使用或参考的第三方依赖。真实第三方源码默认不由主仓库跟踪。

| 名称 | 用途 | 使用方式 | 来源 | 版本 / commit | License | 是否入仓 | 备注 |
|---|---|---|---|---|---|---|---|
| yt-dlp | B站元数据和媒体流采集 | external-command | https://github.com/yt-dlp/yt-dlp | 待固定 | Unlicense | 否 | 由 `integrations/` 薄包装调用 |
| FFmpeg | 抽音频、抽帧、转码 | external-command | https://github.com/FFmpeg/FFmpeg | 待固定 | LGPL/GPL 取决于构建 | 否 | 运行时检测系统命令 |
| faster-whisper | 本地必需 ASR 转写后端 | python-dependency | https://github.com/SYSTRAN/faster-whisper | `>=1.1,<2` | MIT | 否 | 通过项目依赖安装；模型权重不入仓 |
| PySceneDetect | 场景变化检测 | python-dependency / external-tool | https://github.com/Breakthrough/PySceneDetect | 待固定 | BSD-3-Clause | 否 | 后续进入 `pyproject.toml` |
| newapi | 云端视觉、蒸馏和 judge | external-service | https://api.renice.cc/ | 配置文件指定 | 服务侧 | 否 | API key 只从环境变量读取 |
| kangarooking/cangjie-skill | RIA++ skill 蒸馏参考 | reference-only | 待补充 | 待确认 | 待确认 | 否 | 许可证确认前只参考，不复制 |

新增第三方前必须补齐：

- 来源 URL。
- 许可证。
- 固定版本、完整 commit SHA 或可复现包版本。
- 安装方式。
- 是否需要本地 patch。
- 由哪个模块调用。

## newapi 兼容契约

本仓库不提交 API key、请求媒体、模型输出或运行日志。`skill_gather.integrations.newapi.NewApiClient` 只通过用户配置的 `base_url` 和环境变量 API key 调用 OpenAI-compatible newapi 端点：

- ASR 不再走 newapi；v0.1 必须通过本地 faster-whisper 完成口播转写。
- 视觉/OCR：`POST {base_url}/chat/completions`，JSON body，使用 `messages[].content` 的 text + `image_url` 多模态格式，图片以 `data:{mime};base64,...` 传入，并请求 `response_format: {"type": "json_object"}`。
- RIA++ 草案蒸馏：`POST {base_url}/chat/completions`，JSON body，将 `VideoSourceManifest` 和 `EvidenceTimeline` 作为文本证据传入，并请求 `response_format: {"type": "json_object"}`。
- LLM judge：`POST {base_url}/chat/completions`，JSON body，将 `VideoSourceManifest`、`EvidenceTimeline` 和 `distillation.json` 草案作为文本证据传入，并请求 `response_format: {"type": "json_object"}`。

视觉/OCR 响应要求 `choices[0].message.content` 是 JSON 字符串，解析后必须包含 `observations` 数组；每个 observation 至少包含非空 `type` 和 `claim`，可选 `raw_excerpt` 与 `confidence`。HTTP、连接和响应形状错误会转换为脱敏后的 `NewApiError`，供管线逐帧记录到审计产物。

RIA++ 草案蒸馏响应要求 `choices[0].message.content` 是 JSON 字符串，解析后必须包含非空 `candidate_title` 和 `ria` 对象；`ria` 至少包含 `recall`、`interpret`、`apply`、`boundary`、`test`。HTTP、连接和响应形状错误同样会转换为脱敏后的 `NewApiError`，供管线写入 `distillation.json`。

LLM judge 响应要求 `choices[0].message.content` 是 JSON 字符串，解析后必须包含 0-100 的整数 `score`，可选 `rationale` 和 `risk_flags`。HTTP、连接和响应形状错误同样会转换为脱敏后的 `NewApiError`，供管线写入 `score.json` 的 `judge` 字段。
