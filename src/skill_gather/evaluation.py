from __future__ import annotations

from typing import Any

from .scoring import conservative_score, status_for_score, thresholds_for_difficulty


STATUSES = ("passed", "needs_review", "failed")
HUMAN_LABEL_STATUSES = {
    "usable": "passed",
    "needs_changes": "needs_review",
    "unusable": "failed",
}


def build_quality_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    predictions = {"rule": [], "judge": [], "final": []}
    expected: list[str] = []

    for index, sample in enumerate(samples):
        label = str(sample.get("human_label", "")).strip()
        if label not in HUMAN_LABEL_STATUSES:
            raise ValueError(f"第 {index + 1} 条样本缺少合法 human_label。")
        try:
            rule_score = int(sample["rule_score"])
            judge_score = int(sample["judge_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"第 {index + 1} 条样本缺少合法 rule_score 或 judge_score。") from exc
        difficulty = str(sample.get("difficulty", "standard"))
        expected.append(HUMAN_LABEL_STATUSES[label])
        predictions["rule"].append(status_for_score(rule_score, difficulty=difficulty))
        predictions["judge"].append(status_for_score(judge_score, difficulty=difficulty))
        predictions["final"].append(
            conservative_score(rule_score, judge_score, difficulty=difficulty).final_status
        )

    return {
        "sample_count": len(samples),
        "labels": list(STATUSES),
        **{
            name: _classification_report(expected, predicted)
            for name, predicted in predictions.items()
        },
        "judge_threshold_calibration": _judge_threshold_calibration(samples, expected),
    }


def _judge_threshold_calibration(
    samples: list[dict[str, Any]],
    expected: list[str],
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[int, str]]] = {}
    for sample, actual in zip(samples, expected, strict=True):
        difficulty = str(sample.get("difficulty", "standard"))
        grouped.setdefault(difficulty, []).append((int(sample["judge_score"]), actual))

    result: dict[str, Any] = {}
    for difficulty, pairs in grouped.items():
        current_pass, current_review = thresholds_for_difficulty(difficulty)
        best: tuple[float, int, int, int] | None = None
        for review_threshold in range(0, 101):
            for passed_threshold in range(review_threshold, 101):
                correct = sum(
                    _status_with_thresholds(score, passed_threshold, review_threshold) == actual
                    for score, actual in pairs
                )
                accuracy = correct / len(pairs)
                distance = abs(passed_threshold - current_pass) + abs(review_threshold - current_review)
                candidate = (accuracy, -distance, passed_threshold, review_threshold)
                if best is None or candidate > best:
                    best = candidate
        assert best is not None
        result[difficulty] = {
            "sample_count": len(pairs),
            "current": {
                "passed_threshold": current_pass,
                "review_threshold": current_review,
            },
            "recommended": {
                "passed_threshold": best[2],
                "review_threshold": best[3],
                "accuracy": round(best[0], 4),
            },
        }
    return result


def _status_with_thresholds(score: int, passed_threshold: int, review_threshold: int) -> str:
    if score >= passed_threshold:
        return "passed"
    if score >= review_threshold:
        return "needs_review"
    return "failed"


def _classification_report(expected: list[str], predicted: list[str]) -> dict[str, Any]:
    matrix = {
        actual: {prediction: 0 for prediction in STATUSES}
        for actual in STATUSES
    }
    correct = 0
    for actual, prediction in zip(expected, predicted, strict=True):
        matrix[actual][prediction] += 1
        if actual == prediction:
            correct += 1
    return {
        "accuracy": round(correct / len(expected), 4) if expected else 0.0,
        "matrix": matrix,
    }
