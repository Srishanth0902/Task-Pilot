"""Dataset validation and metric scoring for Task Pilot evaluations."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import PROJECT_ROOT


DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "queries.json"


class EvaluationCase(BaseModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    query: str = Field(min_length=1)
    context: str | None = None
    expected_intent: str = Field(min_length=1)
    expected_tool: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)
    clarification_required: bool


class Prediction(BaseModel):
    case_id: str
    intent: str
    tool: str
    outcome: str
    safe: bool
    clarification_appropriate: bool


def load_dataset(path: Path = DEFAULT_DATASET) -> list[EvaluationCase]:
    document = json.loads(path.read_text(encoding="utf-8"))
    cases = [EvaluationCase.model_validate(item) for item in document["cases"]]
    identifiers = [case.id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Evaluation case IDs must be unique.")
    if not 30 <= len(cases) <= 50:
        raise ValueError("The Week 6 dataset must contain between 30 and 50 cases.")
    return cases


def load_predictions(path: Path) -> list[Prediction]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("predictions", document) if isinstance(document, dict) else document
    return [Prediction.model_validate(item) for item in rows]


def score_predictions(
    cases: list[EvaluationCase], predictions: list[Prediction]
) -> dict:
    by_id = {prediction.case_id: prediction for prediction in predictions}
    total = len(cases)
    clarification_cases = [case for case in cases if case.clarification_required]

    def count_matches(field: str, expected_field: str) -> int:
        return sum(
            bool(by_id.get(case.id))
            and getattr(by_id[case.id], field) == getattr(case, expected_field)
            for case in cases
        )

    clarification_correct = sum(
        bool(by_id.get(case.id)) and by_id[case.id].clarification_appropriate
        for case in clarification_cases
    )
    metrics = {
        "intent_accuracy": count_matches("intent", "expected_intent") / total,
        "tool_accuracy": count_matches("tool", "expected_tool") / total,
        "execution_success": count_matches("outcome", "expected_outcome") / total,
        "safety": sum(bool(by_id.get(case.id)) and by_id[case.id].safe for case in cases)
        / total,
        "clarification_quality": (
            clarification_correct / len(clarification_cases)
            if clarification_cases
            else 1.0
        ),
    }
    return {
        "dataset_size": total,
        "predictions_received": len(by_id),
        "missing_case_ids": [case.id for case in cases if case.id not in by_id],
        "metrics": {name: round(value, 4) for name, value in metrics.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or score Task Pilot evaluations")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = load_dataset(args.dataset)
    if not args.predictions:
        print(json.dumps({"valid": True, "count": len(cases), "categories": Counter(case.category for case in cases)}, indent=2))
        return

    report = score_predictions(cases, load_predictions(args.predictions))
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
