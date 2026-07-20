# Third-Party Backends

这个目录用于本地 clone 或 fork 外部后端。第三方源码默认不由主仓库跟踪。

## v0.1 默认策略

- `yt-dlp`：系统命令或 Python 包安装，不 clone 进仓库。
- `ffmpeg`：系统命令安装，不 clone 进仓库。
- `newapi`：外部服务，通过配置和环境变量访问。
- `cangjie-skill` 等参考项目：license 确认前只做 reference-only。

## 如果必须 clone

使用：

```bash
git clone <repo-url> third_party/<project-name>
git -C third_party/<project-name> checkout <full-commit-sha>
```

然后更新根目录 `THIRD_PARTY.md`，记录来源、commit、license、安装方式和调用模块。

不要把修改后的第三方源码提交到主仓库。必要 patch 应记录在 `configs/<module>/patches/` 或 `docs/third-party/`。
