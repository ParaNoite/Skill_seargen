# 项目结构规范

本项目使用轻量 Python CLI 结构，目录职责以 `PROJECT.md` 为准。

## 推荐结构

```text
Skill_seargen/
  AGENTS.md
  PROJECT.md
  README.md
  THIRD_PARTY.md
  pyproject.toml
  config.example.json
  configs/
  docs/
  src/
    skill_gather/
      adapters/
      integrations/
      pipeline/
      distillers/
      evaluators/
      packaging/
  tests/
  third_party/
  runs/       # ignored
  skills/     # ignored
```

## 放置规则

- 平台来源解析放 `adapters/`。
- 外部命令和服务 API 包装放 `integrations/`。
- 断点续跑、阶段调度和失败审计放 `pipeline/`。
- RIA++ 生成逻辑放 `distillers/`。
- 固定规则和 LLM judge 放 `evaluators/`。
- 候选包写出放 `packaging/`。

如果一个文件不知道该放哪里，先按数据流判断它是生产者、转换者还是消费者；仍不清楚时暂停并补充规范，不要塞进 `utils.py`。
