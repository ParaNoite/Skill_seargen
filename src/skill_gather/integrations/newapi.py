from __future__ import annotations

import json
import os
from hashlib import sha256
from difflib import SequenceMatcher
import re
import urllib.error
import urllib.request
from base64 import b64encode
from dataclasses import dataclass
from mimetypes import guess_type
from pathlib import Path
from typing import Any

from .yt_dlp import sanitize_command_output


class NewApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "newapi_error",
        status_code: int | None = None,
        response_excerpt: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.safe_summary = sanitize_command_output(message)
        self.response_audit = _model_response_audit(response_excerpt) if response_excerpt else {}


_SAFE_RESPONSE_FIELDS = {
    "candidate_title",
    "summary",
    "ria",
    "recall",
    "interpret",
    "apply",
    "boundary",
    "test",
    "evidence_refs",
    "score",
    "rationale",
    "risk_flags",
    "observations",
    "type",
    "claim",
    "raw_excerpt",
    "confidence",
    "goal",
    "facets",
    "exclusions",
    "queries",
    "assessments",
    "candidate_id",
    "relevance",
    "matched_facets",
    "reason",
    "title",
    "learning_outcomes",
    "overview",
    "lessons",
    "heading",
    "content",
    "pitfalls",
    "exercises",
    "next_steps",
}


def _model_response_audit(content: str) -> dict[str, Any]:
    encoded = content.encode("utf-8", errors="replace")
    audit: dict[str, Any] = {
        "response_sha256": sha256(encoded).hexdigest(),
        "response_length": len(content),
        "format": "unparsed",
    }
    try:
        parsed = _json_object_from_content(content)
    except (TypeError, json.JSONDecodeError):
        return audit
    audit["format"] = "json"
    audit["response_shape"] = _safe_response_shape(parsed)
    return audit


def _safe_response_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        safe_fields = {
            key: _safe_response_shape(item)
            for key, item in value.items()
            if key in _SAFE_RESPONSE_FIELDS
        }
        return {
            "type": "object",
            "fields": safe_fields,
            "unknown_field_count": len(value) - len(safe_fields),
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "item": _safe_response_shape(value[0]) if value else None,
        }
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": "string", "length": len(str(value))}


def _multipart_form_data(fields: dict[str, str], file_field: str, file_name: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = "----skillgatherboundary7d1b0d1d0f7f"
    lines: list[bytes] = []
    for key, value in fields.items():
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"'.encode("utf-8"),
                b"",
                value.encode("utf-8"),
            ]
        )
    lines.extend(
        [
            f"--{boundary}".encode("utf-8"),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"'.encode("utf-8"),
            b"Content-Type: application/octet-stream",
            b"",
            file_bytes,
            f"--{boundary}--".encode("utf-8"),
            b"",
        ]
    )
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def _has_model_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_model_content(item) for item in value)
    if isinstance(value, dict):
        return any(_has_model_content(item) for item in value.values())
    return False


def _normalize_ria(ria: dict[str, Any]) -> dict[str, Any]:
    return {key: _normalize_ria_value(ria.get(key)) for key in ["recall", "interpret", "apply", "boundary", "test"]}


def _normalize_ria_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_ria_item(item) for item in value if _normalize_ria_item(item)]
    if isinstance(value, dict):
        return _normalize_ria_item(value)
    return value


def _normalize_ria_item(item: Any) -> str:
    if isinstance(item, dict):
        parts = [
            str(item.get("claim", "")).strip(),
            str(item.get("timestamp", "")).strip(),
            str(item.get("evidence_ref", "")).strip(),
        ]
        text = " — ".join(part for part in parts if part)
        return text or json.dumps(item, ensure_ascii=False)
    return str(item).strip()


def _json_object_from_content(content: str) -> dict[str, Any]:
    text = str(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        result = json.loads(text[start : end + 1])
    if not isinstance(result, dict):
        raise json.JSONDecodeError("expected JSON object", text, 0)
    return result


def _compact_evidence_timeline(evidence_timeline: dict[str, Any]) -> dict[str, Any]:
    items = evidence_timeline.get("items", [])
    if not isinstance(items, list):
        items = []
    max_items = _env_int("SKILL_GATHER_DISTILL_EVIDENCE_LIMIT", 90)
    max_claim_chars = _env_int("SKILL_GATHER_DISTILL_CLAIM_CHARS", 220)
    normalized_items = _normalize_evidence_items(items)
    deduped_items = _dedupe_evidence_items(normalized_items)
    selected_items = _select_evidence_items(deduped_items, max_items)
    compact_items: list[dict[str, Any]] = []
    for item in selected_items:
        if not isinstance(item, dict):
            continue
        compact_item = {
            "timestamp": item.get("timestamp", "00:00:00"),
            "type": item.get("type", ""),
            "claim": _truncate_text(item.get("claim", ""), max_claim_chars),
            "raw_excerpt": _truncate_text(item.get("raw_excerpt", ""), max_claim_chars),
            "confidence": item.get("confidence", 0),
        }
        compact_items.append(compact_item)
    return {
        "video_duration_sec": evidence_timeline.get("video_duration_sec", 0),
        "frame_budget": evidence_timeline.get("frame_budget", 0),
        "sampling_strategy": evidence_timeline.get("sampling_strategy", ""),
        "items": compact_items,
        "omitted_item_count": max(0, len(items) - len(compact_items)),
    }


_EVIDENCE_TYPE_PRIORITY = {
    "asr": 5.0,
    "workflow_step": 5.0,
    "code_command": 5.0,
    "ui_action": 4.8,
    "rule": 4.6,
    "pitfall": 4.4,
    "example": 4.0,
    "frame_ocr": 4.0,
    "toolchain": 3.8,
    "metadata_title": 0.5,
    "metadata_author": 0.2,
}
_TRANSCRIPT_CUE_RE = re.compile(
    r"注意|看这里|看一下|如下|接下来|然后|步骤|执行|运行|安装|配置|输入|输出|命令|报错|示例|点击"
    r"|\b(?:look|notice|watch|next|then|step|run|execute|install|configure|click|type|error|example)\b",
    re.IGNORECASE,
)


def _normalize_evidence_items(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        claim = str(raw.get("claim", "")).strip()
        excerpt = str(raw.get("raw_excerpt", "")).strip()
        timestamp = raw.get("timestamp", "00:00:00")
        evidence_type = raw.get("type", "")
        normalized.append(
            {
                "timestamp": timestamp,
                "type": evidence_type,
                "claim": claim,
                "raw_excerpt": excerpt,
                "confidence": raw.get("confidence", 0),
                "_index": index,
                "_type_text": str(evidence_type),
                "_seconds": _evidence_timestamp_seconds(timestamp),
            }
        )
    return normalized


def _evidence_timestamp_seconds(value: Any) -> float | None:
    parts = str(value or "").strip().split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None
    return None


def _normalized_evidence_text(item: dict[str, Any]) -> str:
    value = str(item.get("claim", "")).lower()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value)


def _normalized_evidence_excerpt(item: dict[str, Any]) -> str:
    value = str(item.get("raw_excerpt", "")).lower()
    return re.sub(r"\s+", "", value)


def _looks_like_command(value: str) -> bool:
    return bool(
        re.search(
            r"(?:^|\s)(?:pip|npm|pnpm|yarn|git|python|node|cargo|docker|kubectl)\b|--?[a-z]|[=_`]",
            value,
            re.IGNORECASE,
        )
    )


def _evidence_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_text = _normalized_evidence_text(left)
    right_text = _normalized_evidence_text(right)
    if not left_text or not right_text:
        return 0.0
    if left_text == right_text:
        left_excerpt = _normalized_evidence_excerpt(left)
        right_excerpt = _normalized_evidence_excerpt(right)
        if left_excerpt and right_excerpt and left_excerpt != right_excerpt:
            if _looks_like_command(str(left.get("raw_excerpt", ""))) and _looks_like_command(str(right.get("raw_excerpt", ""))):
                return 0.0
        return 1.0
    if min(len(left_text), len(right_text)) < 8:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _evidence_rank(item: dict[str, Any]) -> float:
    try:
        confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return _EVIDENCE_TYPE_PRIORITY.get(_evidence_type(item), 2.5) + confidence


def _evidence_type(item: dict[str, Any]) -> str:
    return str(item.get("_type_text", item.get("type", "")))


def _dedupe_evidence_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse nearby rolling transcript/visual duplicates, keeping the stronger channel."""
    kept: list[dict[str, Any]] = []
    recent_indices: list[int] = []
    exact_claims: dict[str, list[int]] = {}
    for item in items:
        duplicate_index: int | None = None
        item_seconds = item.get("_seconds")
        normalized_claim = _normalized_evidence_text(item)
        candidate_indices = exact_claims.get(normalized_claim, []) if normalized_claim else []
        candidate_indices = [*candidate_indices, *recent_indices[-64:]]
        for index in reversed(dict.fromkeys(candidate_indices)):
            previous = kept[index]
            previous_seconds = previous.get("_seconds")
            if item_seconds is not None and previous_seconds is not None and abs(item_seconds - previous_seconds) > 8:
                continue
            if _evidence_similarity(item, previous) >= 0.92:
                duplicate_index = index
                break
        if duplicate_index is None:
            kept.append(item)
            kept_index = len(kept) - 1
            recent_indices.append(kept_index)
            if normalized_claim:
                exact_claims.setdefault(normalized_claim, []).append(kept_index)
        elif _evidence_rank(item) > _evidence_rank(kept[duplicate_index]):
            kept[duplicate_index] = item
    return kept


def _evidence_selection_score(item: dict[str, Any], cue_times: list[float]) -> float:
    score = _evidence_rank(item)
    item_seconds = item.get("_seconds")
    item_type = _evidence_type(item)
    claim = str(item.get("claim", ""))
    if item_type == "asr" and _TRANSCRIPT_CUE_RE.search(claim):
        score += 2.0
    if item_seconds is not None and item_type != "asr":
        if any(abs(item_seconds - cue_time) <= 8 for cue_time in cue_times):
            score += 1.5
    if item_type.startswith("metadata_"):
        score -= 2.0
    return score


def _select_evidence_items(items: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    if len(items) <= max_items:
        return sorted(items, key=lambda item: (item.get("_seconds") is None, item.get("_seconds") or 0, item["_index"]))

    cue_items = [
        item
        for item in items
        if _evidence_type(item) == "asr"
        and item.get("_seconds") is not None
        and _TRANSCRIPT_CUE_RE.search(str(item.get("claim", "")))
    ]
    cue_times = [item["_seconds"] for item in cue_items]
    scores = {id(item): _evidence_selection_score(item, cue_times) for item in items}
    if max_items <= 1:
        return [max(items, key=lambda item: (scores[id(item)], -item["_index"]))]

    timestamped = [item for item in items if item.get("_seconds") is not None]
    if timestamped:
        start = min(item["_seconds"] for item in timestamped)
        end = max(item["_seconds"] for item in timestamped)
        span = max(end - start, 1.0)
    else:
        start = 0.0
        span = max(len(items) - 1, 1)

    def bucket(item: dict[str, Any]) -> int:
        position = item.get("_seconds")
        if position is None:
            position = item["_index"]
            return min(max_items - 1, int(position / span * (max_items - 1)))
        return min(max_items - 1, int((position - start) / span * (max_items - 1)))

    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    protected_ids: set[int] = set()

    cue_representatives = [
        max(group, key=lambda item: (scores[id(item)], -item["_index"]))
        for group in _group_by_bucket(cue_items, bucket).values()
    ]
    cue_representatives.sort(key=lambda item: (item.get("_seconds") or 0, item["_index"]))
    pair_budget = max_items // 2
    if pair_budget and len(cue_representatives) > pair_budget:
        spread_indices = [
            round(index * (len(cue_representatives) - 1) / max(pair_budget - 1, 1))
            for index in range(pair_budget)
        ]
        cue_representatives = [cue_representatives[index] for index in spread_indices]

    for cue in cue_representatives:
        if len(selected) >= max_items:
            break
        if id(cue) not in selected_ids:
            selected.append(cue)
            selected_ids.add(id(cue))
            protected_ids.add(id(cue))
        neighbors = [
            item
            for item in items
            if id(item) not in selected_ids
            and _evidence_type(item) != "asr"
            and not _evidence_type(item).startswith("metadata_")
            and item.get("_seconds") is not None
            and abs(item["_seconds"] - cue["_seconds"]) <= 8
        ]
        if neighbors and len(selected) < max_items:
            neighbor = max(neighbors, key=lambda item: (scores[id(item)], -item["_index"]))
            selected.append(neighbor)
            selected_ids.add(id(neighbor))
            protected_ids.add(id(neighbor))

    substantive_exists = any(not _evidence_type(item).startswith("metadata_") for item in items)
    for bucket_items in _group_by_bucket(items, bucket).values():
        if len(selected) >= max_items:
            break
        if substantive_exists and all(_evidence_type(item).startswith("metadata_") for item in bucket_items):
            continue
        unselected = [item for item in bucket_items if id(item) not in selected_ids]
        if not unselected:
            continue
        candidate = max(unselected, key=lambda item: (scores[id(item)], -item["_index"]))
        selected.append(candidate)
        selected_ids.add(id(candidate))

    ranked = sorted(items, key=lambda item: (scores[id(item)], -item["_index"]), reverse=True)
    for item in ranked:
        if len(selected) >= max_items:
            break
        if id(item) not in selected_ids:
            selected.append(item)
            selected_ids.add(id(item))

    chronological = sorted(items, key=lambda item: (item.get("_seconds") is None, item.get("_seconds") or 0, item["_index"]))
    edge_items = [item for item in chronological if not _evidence_type(item).startswith("metadata_")] or chronological
    for edge in (edge_items[0], edge_items[-1]):
        if id(edge) in selected_ids:
            continue
        removable = [
            item
            for item in selected
            if id(item) not in protected_ids and item not in (edge_items[0], edge_items[-1])
        ]
        if not removable:
            continue
        removed = min(removable, key=lambda item: (scores[id(item)], item["_index"]))
        selected.remove(removed)
        selected_ids.remove(id(removed))
        selected.append(edge)
        selected_ids.add(id(edge))

    return sorted(selected[:max_items], key=lambda item: (item.get("_seconds") is None, item.get("_seconds") or 0, item["_index"]))


def _group_by_bucket(items: list[dict[str, Any]], bucket) -> dict[int, list[dict[str, Any]]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(bucket(item), []).append(item)
    return groups


def _compact_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": manifest.get("source", ""),
        "source_id": manifest.get("source_id", ""),
        "title": manifest.get("title", ""),
        "author": manifest.get("author", ""),
        "duration_sec": manifest.get("duration_sec", 0),
        "subtitle_available": manifest.get("subtitle_available", False),
        "media_access": manifest.get("media_access", ""),
    }


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _short_text_list(value: Any, *, limit: int, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _truncate_text(item, max_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def resolve_api_key(api_key_env_or_value: str) -> str:
    value = str(api_key_env_or_value).strip()
    if not value:
        return ""
    env_value = os.getenv(value, "").strip()
    if env_value:
        return env_value
    return ""


@dataclass(frozen=True, slots=True)
class NewApiClient:
    base_url: str
    api_key: str
    timeout_sec: int = 120
    request_profiles: dict[str, Any] | None = None

    @classmethod
    def from_config(cls, config: Any) -> "NewApiClient | None":
        api_key = resolve_api_key(str(config.api_key_env))
        if not api_key:
            return None
        return cls(
            base_url=str(config.base_url),
            api_key=api_key,
            timeout_sec=int(getattr(config, "timeout_sec", 180)),
            request_profiles=dict(getattr(config, "request_profiles", {}) or {}),
        )

    def transcribe_audio(self, audio_file: str | Path, model: str) -> dict[str, Any]:
        path = Path(audio_file)
        if not path.exists():
            raise NewApiError("audio file does not exist", code="audio_file_missing")

        body, content_type = _multipart_form_data(
            {"model": model},
            "file",
            path.name,
            path.read_bytes(),
        )
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/audio/transcriptions",
            data=body,
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("Content-Type", content_type)
        request.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                payload = response.read().decode("utf-8", errors="replace")
                status_code = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise NewApiError(
                body_text or str(exc),
                code="transcription_failed",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise NewApiError(
                str(exc.reason),
                code="transcription_unreachable",
            ) from exc

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise NewApiError(
                "newapi returned invalid JSON for transcription",
                code="invalid_transcription_json",
                status_code=status_code,
            ) from exc

        if not isinstance(parsed, dict):
            raise NewApiError(
                "newapi returned non-object transcription JSON",
                code="invalid_transcription_shape",
                status_code=status_code,
            )

        text = str(parsed.get("text", ""))
        segments = parsed.get("segments", [])
        if not isinstance(segments, list):
            segments = []
        return {
            "status": "transcribed",
            "model": model,
            "audio_path": str(path),
            "text": text,
            "language": str(parsed.get("language", "")),
            "segments": segments,
            "returncode": 0,
        }

    def _post_chat_completion_content(
        self,
        payload: dict[str, Any],
        *,
        operation: str,
        http_error_code: str,
        unreachable_code: str,
        profile: str | None = None,
    ) -> str:
        body = json.dumps(self._apply_request_profile(payload, profile)).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/chat/completions",
            data=body,
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                payload_text = response.read().decode("utf-8", errors="replace")
                status_code = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise NewApiError(
                body_text or str(exc),
                code=http_error_code,
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise NewApiError(
                str(exc.reason),
                code=unreachable_code,
            ) from exc

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise NewApiError(
                f"newapi returned invalid chat completion JSON for {operation}",
                code=f"invalid_{operation}_response_json",
                status_code=status_code,
            ) from exc

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise NewApiError(
                f"newapi returned invalid chat completion shape for {operation}",
                code=f"invalid_{operation}_response_shape",
                status_code=status_code,
            ) from exc
        return str(content)

    def _apply_request_profile(
        self, payload: dict[str, Any], profile: str | None
    ) -> dict[str, Any]:
        if not profile or not self.request_profiles:
            return payload
        request_profile = self.request_profiles.get(profile)
        if request_profile is None:
            return payload
        merged = dict(payload)
        for key in (
            "temperature",
            "top_p",
            "max_tokens",
            "max_completion_tokens",
            "reasoning_effort",
        ):
            value = getattr(request_profile, key, None)
            if value is not None:
                merged[key] = value
        if "max_completion_tokens" in merged:
            merged.pop("max_tokens", None)
        return merged

    def probe_model(self, model: str, capability: str) -> dict[str, Any]:
        """Verify a configured chat model with a minimal request without retaining its output."""
        expected_content = "1"
        response_format: dict[str, str] | None = None
        max_tokens = 4
        if capability == "text":
            content: str | list[dict[str, Any]] = "Reply with only the digit 1."
        elif capability == "vision":
            response_format = {"type": "json_object"}
            max_tokens = 128
            content = [
                {
                    "type": "text",
                    "text": (
                        "Analyze only the supplied image. Return JSON with an observations array. "
                        "Each observation must contain type, claim, raw_excerpt, and confidence."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAANSURBVBhXY/jPwPAfAAUAAf+mXJtdAAAAAElFTkSuQmCC"},
                },
            ]
        else:
            raise ValueError("capability must be text or vision")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        try:
            response = self._post_chat_completion_content(
                payload,
                operation="model_probe",
                http_error_code="model_probe_failed",
                unreachable_code="model_probe_unreachable",
                profile="probe",
            )
        except NewApiError as exc:
            summary = f"{exc.code}"
            if exc.status_code is not None:
                summary += f" (HTTP {exc.status_code})"
            return {
                "model": model,
                "capability": capability,
                "available": False,
                "error_code": exc.code,
                "status_code": exc.status_code,
                "summary": summary,
            }
        matched = response.strip().strip("`'\" ").rstrip(".。!！").lower() == expected_content
        if capability == "vision":
            try:
                parsed = _json_object_from_content(response)
            except (TypeError, json.JSONDecodeError):
                matched = False
            else:
                observations = parsed.get("observations")
                matched = (
                    isinstance(observations, list)
                    and bool(observations)
                    and all(
                        isinstance(item, dict)
                        and str(item.get("type", "")).strip()
                        and str(item.get("claim", "")).strip()
                        for item in observations
                    )
                )
        return {
            "model": model,
            "capability": capability,
            "available": matched,
            **({"summary": "model_probe_unexpected_response"} if not matched else {}),
        }

    def analyze_frame(self, frame_file: str | Path, model: str) -> dict[str, Any]:
        path = Path(frame_file)
        if not path.exists():
            raise NewApiError("frame file does not exist", code="frame_file_missing")

        media_type = guess_type(path.name)[0] or "image/jpeg"
        image_data = b64encode(path.read_bytes()).decode("ascii")
        content = self._post_chat_completion_content(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Analyze this tutorial video frame as evidence for distilling a reusable Codex skill. "
                                    "Use a Cangjie-style multi-extractor pass: extract OCR/code/commands/config, "
                                    "UI actions, workflow steps, examples or counterexamples, toolchain names, "
                                    "versions, errors, and decision rules visible in the frame. "
                                    "Return JSON with an observations array. Each observation must contain type, "
                                    "claim, raw_excerpt, and confidence. Use specific types such as frame_ocr, "
                                    "code_command, ui_action, workflow_step, example, pitfall, toolchain, or rule. "
                                    "Do not infer facts that are not visible in the frame."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{image_data}",
                                },
                            },
                        ],
                    }
                ],
                "response_format": {"type": "json_object"},
            },
            operation="vision",
            http_error_code="vision_failed",
            unreachable_code="vision_unreachable",
            profile="vision",
        )
        try:
            result = _json_object_from_content(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NewApiError(
                "newapi returned invalid JSON for vision analysis",
                code="invalid_vision_json",
            ) from exc
        if not isinstance(result, dict) or not isinstance(result.get("observations"), list):
            raise NewApiError(
                "newapi returned invalid vision observation shape",
                code="invalid_vision_shape",
            )
        for observation in result["observations"]:
            if (
                not isinstance(observation, dict)
                or not str(observation.get("type", "")).strip()
                or not str(observation.get("claim", "")).strip()
            ):
                raise NewApiError(
                    "newapi returned invalid vision observation shape",
                    code="invalid_vision_shape",
                )
        return {
            "status": "analyzed",
            "model": model,
            "frame_path": str(path),
            "observations": result["observations"],
            "returncode": 0,
        }

    def expand_search_queries(self, topic: str, mode: str, model: str) -> list[str]:
        prompt = (
            "Return only JSON with a queries array. Expand this research topic into at most two short, "
            "auditable search queries. Keep the original meaning, do not invent entities or URLs, and "
            "use Chinese plus an English variant only when useful. "
            f"Mode: {mode}. Topic: {topic}"
        )
        content = self._post_chat_completion_content(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            operation="search_query_expansion",
            http_error_code="search_query_expansion_failed",
            unreachable_code="search_query_expansion_unreachable",
            profile="search",
        )
        try:
            result = _json_object_from_content(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NewApiError(
                "newapi returned invalid search query JSON",
                code="invalid_search_query_json",
                response_excerpt=content,
            ) from exc
        queries = result.get("queries") if isinstance(result, dict) else None
        if not isinstance(queries, list):
            raise NewApiError(
                "newapi returned invalid search query shape",
                code="invalid_search_query_shape",
                response_excerpt=content,
            )
        normalized = [str(query).strip() for query in queries if str(query).strip()]
        if len(normalized) > 2:
            normalized = normalized[:2]
        return normalized

    def build_search_intent(self, topic: str, mode: str, model: str) -> dict[str, Any]:
        prompt = (
            "Return only JSON with goal as a string and facets, exclusions, and queries as arrays. Interpret the user's "
            "research topic for public-source discovery. Keep the original meaning, do not invent entities, "
            "URLs, or facts. Provide at most 3 short facets, 3 exclusions, and 2 query variants. "
            "Mode: " + mode + ". Topic: " + topic
        )
        content = self._post_chat_completion_content(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            operation="search_intent",
            http_error_code="search_intent_failed",
            unreachable_code="search_intent_unreachable",
            profile="search",
        )
        try:
            result = _json_object_from_content(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NewApiError("newapi returned invalid search intent JSON", code="invalid_search_intent_json", response_excerpt=content) from exc
        if not isinstance(result, dict):
            raise NewApiError("newapi returned invalid search intent shape", code="invalid_search_intent_shape", response_excerpt=content)
        goal = result.get("goal", "")
        if isinstance(goal, list):
            goal = goal[0] if goal else ""
        return {
            "goal": _truncate_text(goal, 240),
            "facets": _short_text_list(result.get("facets"), limit=3, max_chars=80),
            "exclusions": _short_text_list(result.get("exclusions"), limit=3, max_chars=80),
            "queries": _short_text_list(result.get("queries"), limit=2, max_chars=120),
        }

    def assess_search_candidates(self, intent: dict[str, Any], candidates: list[dict[str, Any]], model: str) -> dict[str, dict[str, Any]]:
        compact_candidates = [
            {
                "candidate_id": str(candidate.get("candidate_id", "")),
                "title": _truncate_text(candidate.get("title", ""), 180),
                "summary": _truncate_text(candidate.get("summary", ""), 280),
                "source_type": str(candidate.get("source_type", "")),
            }
            for candidate in candidates[:20]
            if str(candidate.get("candidate_id", ""))
        ]
        prompt = (
            "Return only JSON with an assessments array. Assess each candidate only from its supplied title, "
            "summary, and source type against the intent. Never claim to have opened a URL or watched a video. "
            "Each item: candidate_id, relevance (0-100 integer), matched_facets (max 3 strings), reason (max 120 chars), risk_flags (max 3 strings).\n\n"
            f"Intent: {json.dumps(intent, ensure_ascii=False)}\nCandidates: {json.dumps(compact_candidates, ensure_ascii=False)}"
        )
        content = self._post_chat_completion_content(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            operation="search_candidate_assessment",
            http_error_code="search_candidate_assessment_failed",
            unreachable_code="search_candidate_assessment_unreachable",
            profile="search",
        )
        try:
            result = _json_object_from_content(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NewApiError("newapi returned invalid candidate assessment JSON", code="invalid_candidate_assessment_json", response_excerpt=content) from exc
        items = result.get("assessments") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise NewApiError("newapi returned invalid candidate assessment shape", code="invalid_candidate_assessment_shape", response_excerpt=content)
        allowed_ids = {item["candidate_id"] for item in compact_candidates}
        assessments: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidate_id", "")).strip()
            if candidate_id not in allowed_ids or candidate_id in assessments:
                continue
            try:
                relevance = int(item.get("relevance", 0))
            except (TypeError, ValueError):
                relevance = 0
            assessments[candidate_id] = {
                "relevance": max(0, min(100, relevance)),
                "matched_facets": _short_text_list(item.get("matched_facets"), limit=3, max_chars=80),
                "reason": _truncate_text(item.get("reason", ""), 120),
                "risk_flags": _short_text_list(item.get("risk_flags"), limit=3, max_chars=60),
            }
        return assessments

    def distill_course(self, topic: str, mode: str, fusion: dict[str, Any], model: str) -> dict[str, Any]:
        conclusions = []
        allowed_refs: set[str] = set()
        ranked_conclusions = sorted(
            (item for item in fusion.get("conclusions", []) if isinstance(item, dict)),
            key=lambda item: self._course_evidence_priority(item, topic),
            reverse=True,
        )
        for item in ranked_conclusions[:18]:
            if not isinstance(item, dict):
                continue
            refs = []
            for ref in item.get("citations", [])[:3]:
                if not isinstance(ref, dict):
                    continue
                ref_id = f"{ref.get('source_id', '')}:{ref.get('locator', '')}"
                if ref_id.strip(":"):
                    refs.append(ref_id)
                    allowed_refs.add(ref_id)
            conclusions.append({"claim": _truncate_text(item.get("claim", ""), 700), "evidence_refs": refs})
        prompt = (
            "Return only JSON for a concise Chinese course written for a human learner. "
            "Use only the supplied evidence; synthesize and teach instead of copying transcripts. "
            "The object must contain title, learning_outcomes (2-4 strings), overview, lessons (3-8 objects), "
            "pitfalls, exercises, and next_steps. Each lesson object must contain heading, content, and evidence_refs. "
            "Every evidence_refs item must exactly match one supplied evidence reference. Do not invent commands, APIs, facts, or references. "
            f"Mode: {mode}. Topic: {topic}. Evidence: {json.dumps(conclusions, ensure_ascii=False)}"
        )
        content = self._post_chat_completion_content(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            operation="course_distillation",
            http_error_code="course_distillation_failed",
            unreachable_code="course_distillation_unreachable",
            profile="course",
        )
        try:
            result = _json_object_from_content(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NewApiError("newapi returned invalid course JSON", code="invalid_course_json", response_excerpt=content) from exc
        lessons = result.get("lessons")
        if not isinstance(lessons, list) or not lessons:
            raise NewApiError("newapi returned invalid course shape", code="invalid_course_shape", response_excerpt=content)
        normalized_lessons = []
        for lesson in lessons[:8]:
            if not isinstance(lesson, dict):
                continue
            heading = _truncate_text(lesson.get("heading", ""), 120)
            lesson_content = _truncate_text(lesson.get("content", ""), 1200)
            refs = [str(ref) for ref in lesson.get("evidence_refs", []) if str(ref) in allowed_refs][:3]
            if heading and lesson_content and refs:
                normalized_lessons.append({"heading": heading, "content": lesson_content, "evidence_refs": refs})
        if not normalized_lessons:
            raise NewApiError("course has no evidence-grounded lessons", code="invalid_course_evidence_refs", response_excerpt=content)
        return {
            "title": _truncate_text(result.get("title", topic), 160) or topic,
            "learning_outcomes": _short_text_list(result.get("learning_outcomes"), limit=4, max_chars=180),
            "overview": _truncate_text(result.get("overview", ""), 900),
            "lessons": normalized_lessons,
            "pitfalls": _short_text_list(result.get("pitfalls"), limit=6, max_chars=240),
            "exercises": _short_text_list(result.get("exercises"), limit=6, max_chars=300),
            "next_steps": _short_text_list(result.get("next_steps"), limit=6, max_chars=240),
        }

    def _course_evidence_priority(self, item: dict[str, Any], topic: str) -> tuple[int, int, float]:
        claim = str(item.get("claim", ""))
        lowered = claim.lower()
        citations = [citation for citation in item.get("citations", []) if isinstance(citation, dict)]
        source_types = {str(citation.get("source_type", "")) for citation in citations}
        topic_terms = {
            term.lower()
            for term in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}|[\u4e00-\u9fff]{2,}", topic)
            if len(term) >= 2
        }
        score = int(item.get("supporting_source_count", 0)) * 8
        if source_types & {"github", "web"}:
            score += 8
        if re.search(r"`[^`]+`|\bnpm\s+(?:run|install|test)|\b[A-Za-z_][A-Za-z0-9_]*\([^)]*\)", claim):
            score += 6
        if any(term in lowered for term in topic_terms):
            score += 4
        if source_types == {"video"} and self._is_low_value_course_observation(lowered):
            score -= 20
        return score, len(claim), float(item.get("confidence", 0))

    @staticmethod
    def _is_low_value_course_observation(lowered: str) -> bool:
        markers = (
            "browser tab", "tab title", "taskbar", "system clock", "the date", "address bar",
            "local-network", "local network", "port 5173", "not secure", "insecure", "watermark",
            "new tab", "visual studio code icon", "microsoft edge icon",
        )
        return any(marker in lowered for marker in markers)

    def distill_skill(
        self,
        evidence_timeline: dict[str, Any],
        manifest: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        compact_timeline = _compact_evidence_timeline(evidence_timeline)
        compact_manifest = _compact_manifest(manifest)
        prompt = (
            "Output only valid JSON. Create a reusable Chinese skill note from the video evidence. "
            "JSON keys: candidate_title, summary, ria, evidence_refs. "
            "ria keys: recall, interpret, apply, boundary, test. "
            "Each ria value must be a string or an array of short strings. "
            "Keep claims tied to timestamps and do not add facts absent from the evidence.\n\n"
            f"Video:\n{json.dumps(compact_manifest, ensure_ascii=False)}\n\n"
            f"Evidence:\n{json.dumps(compact_timeline, ensure_ascii=False)}"
        )
        content = self._post_chat_completion_content(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            operation="distillation",
            http_error_code="distillation_failed",
            unreachable_code="distillation_unreachable",
            profile="skill",
        )
        try:
            result = _json_object_from_content(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NewApiError(
                "newapi returned invalid JSON for distillation",
                code="invalid_distillation_json",
                response_excerpt=content,
            ) from exc

        ria = result.get("ria") if isinstance(result, dict) else None
        if isinstance(ria, dict):
            ria = _normalize_ria(ria)
        if (
            not isinstance(result, dict)
            or not str(result.get("candidate_title", "")).strip()
            or not isinstance(ria, dict)
            or not all(_has_model_content(ria.get(key)) for key in ["recall", "interpret", "apply", "boundary", "test"])
        ):
            raise NewApiError(
                "newapi returned invalid distillation shape",
                code="invalid_distillation_shape",
                response_excerpt=content,
            )

        return {
            "status": "distilled",
            "model": model,
            "candidate_title": str(result["candidate_title"]),
            "summary": str(result.get("summary", "")),
            "ria": ria,
            "evidence_refs": result.get("evidence_refs", []),
            "returncode": 0,
            "_audit": _model_response_audit(content),
        }

    def judge_skill(
        self,
        distillation: dict[str, Any],
        evidence_timeline: dict[str, Any],
        manifest: dict[str, Any],
        model: str,
        *,
        difficulty: str = "standard",
    ) -> dict[str, Any]:
        difficulty_instructions = {
            "lenient": (
                "Use a lenient bar: accept a useful draft with some missing detail when "
                "the core workflow is supported, but still penalize fabricated claims."
            ),
            "strict": (
                "Use a strict bar: require strong evidence coverage, executable steps, "
                "clear boundaries, tests, and cross-channel support; penalize gaps heavily."
            ),
            "standard": (
                "Use the standard bar: balance evidence coverage, executability, boundaries, "
                "tests, and transferability."
            ),
        }
        prompt = (
            "Judge this candidate Codex skill draft against the v0.1 rubric. "
            "Return JSON with score, rationale, and risk_flags. "
            "Score must be an integer from 0 to 100. Penalize unsupported claims, "
            "weak boundaries, missing tests, and evidence that only comes from one channel.\n"
            f"Judge difficulty: {difficulty}. {difficulty_instructions.get(difficulty, difficulty_instructions['standard'])}\n\n"
            f"Video manifest:\n{json.dumps(manifest, ensure_ascii=False)}\n\n"
            f"EvidenceTimeline:\n{json.dumps(evidence_timeline, ensure_ascii=False)}\n\n"
            f"Distillation:\n{json.dumps(distillation, ensure_ascii=False)}"
        )
        content = self._post_chat_completion_content(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            },
            operation="judge",
            http_error_code="judge_failed",
            unreachable_code="judge_unreachable",
            profile="judge",
        )
        try:
            result = _json_object_from_content(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NewApiError(
                "newapi returned invalid JSON for judge",
                code="invalid_judge_json",
                response_excerpt=content,
            ) from exc

        if not isinstance(result, dict):
            raise NewApiError(
                "newapi returned invalid judge shape",
                code="invalid_judge_shape",
                response_excerpt=content,
            )
        try:
            score = int(result.get("score"))
        except (TypeError, ValueError) as exc:
            raise NewApiError(
                "newapi returned invalid judge shape",
                code="invalid_judge_shape",
                response_excerpt=content,
            ) from exc
        if score < 0 or score > 100:
            raise NewApiError(
                "newapi returned invalid judge shape",
                code="invalid_judge_shape",
                response_excerpt=content,
            )
        risk_flags = result.get("risk_flags", [])
        if not isinstance(risk_flags, list):
            raise NewApiError(
                "newapi returned invalid judge shape",
                code="invalid_judge_shape",
                response_excerpt=content,
            )
        return {
            "status": "judged",
            "model": model,
            "score": score,
            "rationale": str(result.get("rationale", "")),
            "risk_flags": [str(flag) for flag in risk_flags],
            "returncode": 0,
            "_audit": _model_response_audit(content),
        }
