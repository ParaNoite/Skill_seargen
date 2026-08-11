from __future__ import annotations

import re
from dataclasses import dataclass

from .models import SemanticPlan, SemanticPlanOption, TopicTask
from .integrations.newapi import NewApiClient, NewApiError


@dataclass(frozen=True, slots=True)
class AmbiguityAssessment:
    ambiguous: bool
    reasons: tuple[str, ...]


_BROAD_TERMS = {
    "教程", "入门", "学习", "开发", "技术", "工具", "系统", "方法", "guide", "tutorial", "development",
}


def assess_ambiguity(topic: str, mode: str = "normal") -> AmbiguityAssessment:
    text = topic.strip()
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#._-]*|[\u4e00-\u9fff]{2,}", text)
    reasons: list[str] = []
    if len(words) <= 1:
        reasons.append("主题缺少关键实体或限定词")
    if len(text) < 4:
        reasons.append("主题过短，研究范围无法稳定确定")
    if any(word.lower() in _BROAD_TERMS for word in words) and len(words) <= 3:
        reasons.append("主题使用了过宽的概念词")
    if mode == "technical" and not any(re.search(r"[A-Za-z0-9]", word) for word in words):
        reasons.append("技术主题缺少可检索的产品、库或协议实体")
    return AmbiguityAssessment(bool(reasons), tuple(dict.fromkeys(reasons)))


def build_deterministic_plan(task: TopicTask, *, warning: str = "") -> SemanticPlan:
    assessment = assess_ambiguity(task.topic, task.mode)
    topic = task.topic.strip()
    base = SemanticPlanOption(
        option_id="focused",
        label="聚焦实践路径",
        goal=f"围绕“{topic}”整理可复核的实践步骤与限制",
        exclusions=["付费或受限内容", "与主题无关的泛泛介绍"],
        facets=[topic, "实践步骤", "常见问题"],
        queries=[topic, f"{topic} 教程", f"{topic} 官方文档"],
        rationale="优先保留能直接支撑操作结论的公开来源。",
    )
    broad = SemanticPlanOption(
        option_id="survey",
        label="广度综述路径",
        goal=f"比较“{topic}”的概念、方案和适用边界",
        exclusions=["无来源的观点", "重复转载"],
        facets=[topic, "概念对比", "适用边界"],
        queries=[topic, f"{topic} comparison", f"{topic} best practices"],
        rationale="适合先建立术语地图，再决定深入方向。",
    )
    technical = SemanticPlanOption(
        option_id="technical",
        label="技术实现路径",
        goal=f"提取“{topic}”的实现接口、配置和故障处理",
        exclusions=["营销页面", "未维护的示例"],
        facets=[topic, "API/配置", "故障处理"],
        queries=[topic, f"{topic} GitHub", f"{topic} API documentation"],
        rationale="技术模式优先选择文档、GitHub 和可复现示例。",
    )
    options = [base, broad, technical] if task.mode == "technical" else [base, broad]
    return SemanticPlan(
        ambiguous=assessment.ambiguous,
        ambiguity_reasons=list(assessment.reasons),
        options=options,
        recommended_option_id="technical" if task.mode == "technical" else "focused",
        generation_method="deterministic",
        audit_status="needs_confirmation" if assessment.ambiguous else "not_required",
        warning=warning,
    )


def build_semantic_plan(task: TopicTask, client: NewApiClient | None, model: str) -> SemanticPlan:
    if client is None:
        return build_deterministic_plan(task, warning="skipped_missing_api_key")
    try:
        intent = client.build_search_intent(task.topic, task.mode, model)
    except NewApiError as exc:
        return build_deterministic_plan(task, warning=f"fallback:{exc.code}")
    plan = build_deterministic_plan(task)
    focused = plan.options[0]
    focused.goal = str(intent.get("goal", "")).strip() or focused.goal
    focused.facets = list(dict.fromkeys([str(item) for item in intent.get("facets", [])] + focused.facets))[:6]
    focused.exclusions = list(dict.fromkeys([str(item) for item in intent.get("exclusions", [])] + focused.exclusions))[:6]
    focused.queries = list(dict.fromkeys([str(item) for item in intent.get("queries", [])] + focused.queries))[:6]
    focused.rationale = "NewAPI 结构化理解与保守规则方案共同生成；未保存原始模型响应。"
    plan.generation_method = "newapi"
    return plan


def apply_plan(task: TopicTask, plan: SemanticPlan, option_id: str, *, edited: dict[str, object] | None = None) -> SemanticPlan:
    option = next((item for item in plan.options if item.option_id == option_id), None)
    if option is None:
        raise ValueError(f"找不到语义方案：{option_id}")
    edited = edited or {}
    plan.selected_option_id = option_id
    plan.goal = str(edited.get("goal", option.goal)).strip() or option.goal
    plan.exclusions = [str(item) for item in edited.get("exclusions", option.exclusions)]
    plan.facets = [str(item) for item in edited.get("facets", option.facets)]
    plan.queries = [str(item) for item in edited.get("queries", option.queries) if str(item).strip()]
    plan.audit_status = "confirmed"
    return plan
