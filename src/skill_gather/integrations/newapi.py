from __future__ import annotations

import json
import os
from hashlib import sha256
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
    compact_items: list[dict[str, Any]] = []
    for item in items[:max_items]:
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


def resolve_api_key(api_key_env_or_value: str) -> str:
    value = str(api_key_env_or_value).strip()
    if not value:
        return ""
    env_value = os.getenv(value, "").strip()
    if env_value:
        return env_value
    if value.startswith(("sk-", "sk_")):
        return value
    return ""


@dataclass(frozen=True, slots=True)
class NewApiClient:
    base_url: str
    api_key: str
    timeout_sec: int = 120

    @classmethod
    def from_config(cls, config: Any) -> "NewApiClient | None":
        api_key = resolve_api_key(str(config.api_key_env))
        if not api_key:
            return None
        return cls(base_url=str(config.base_url), api_key=api_key)

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
    ) -> str:
        body = json.dumps(
            payload
        ).encode("utf-8")
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
