"""Compatibility imports for topic evidence fusion."""

from .pipeline.topic_fusion import (
    FusionEvidence,
    fuse_topic_evidence,
    write_fusion_artifacts,
)

__all__ = [
    "FusionEvidence",
    "fuse_topic_evidence",
    "write_fusion_artifacts",
]
