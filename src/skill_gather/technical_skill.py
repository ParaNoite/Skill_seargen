"""Compatibility imports for the technical skill generator."""

from .distillers.technical_skill import (
    _is_procedural_claim,
    _rank_procedures,
    apply_topic_human_review,
    generate_technical_skill,
    score_technical_package,
)

__all__ = [
    "_is_procedural_claim",
    "_rank_procedures",
    "apply_topic_human_review",
    "generate_technical_skill",
    "score_technical_package",
]
