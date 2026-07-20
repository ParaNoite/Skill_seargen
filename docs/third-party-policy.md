# 第三方管理规范

本项目采用分层管理，目标是让依赖可复现、license 可追踪、主仓库不混入来源不明的复制代码。

## 1. Python 依赖

能通过包管理安装的 Python 库写入 `pyproject.toml`。

要求：

- 固定合理版本范围。
- 不为一次性脚本引入重依赖。
- 新依赖必须说明用途。

## 2. 外部命令

`yt-dlp`、`ffmpeg` 等命令默认不进入 Git。

主仓库只负责：

- 检测命令是否存在。
- 构造参数。
- 捕获 stdout、stderr 和退出码。
- 把运行记录写入 run 审计信息。

## 3. 外部服务

newapi 等服务只通过 `integrations/` 下的薄包装访问。

要求：

- base URL、模型名从配置读取。
- API key 从环境变量读取。
- 不把请求中的凭据写入日志、manifest 或 run record。

## 4. 本地 clone / fork

只有真正需要研究、patch 或运行第三方源码时，才放到 `third_party/<project-name>/`。

规则：

- `third_party/` 下源码默认不由主仓库跟踪。
- 每个后端必须在 `THIRD_PARTY.md` 或模块文档中记录来源、commit、license 和安装方式。
- 不直接在主仓库提交修改后的第三方源码。
- 必要 patch 放在 `configs/<module>/patches/` 或 `docs/third-party/`，并记录 SHA-256、原因和应用方式。

## 5. 参考资料

只作为设计参考的项目标记为 `reference-only`。许可证未确认前，不得复制源码、README 大段文本或模板。
