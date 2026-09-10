import unittest

from app.evaluation import EvaluationCase, Prediction, load_dataset, score_predictions


class EvaluationDatasetTests(unittest.TestCase):
    def test_dataset_has_required_size_categories_and_unique_ids(self):
        cases = load_dataset()

        self.assertEqual(len(cases), 45)
        self.assertEqual(len({case.id for case in cases}), 45)
        self.assertEqual(
            {case.category for case in cases},
            {
                "CREATE",
                "READ",
                "UPDATE",
                "DELETE",
                "BULK_UPDATE",
                "FREE_SLOT_SEARCH",
                "AMBIGUOUS_REQUEST",
                "CONFLICT",
                "FOLLOW_UP",
            },
        )

    def test_metric_scoring_covers_all_five_week6_metrics(self):
        cases = [
            EvaluationCase(
                id="one",
                category="CREATE",
                query="Add DSA",
                expected_intent="create",
                expected_tool="create_calendar_event",
                expected_outcome="execute",
                clarification_required=False,
            ),
            EvaluationCase(
                id="two",
                category="AMBIGUOUS_REQUEST",
                query="Delete my meeting",
                expected_intent="delete",
                expected_tool="search_calendar_events",
                expected_outcome="clarify",
                clarification_required=True,
            ),
        ]
        predictions = [
            Prediction(
                case_id="one",
                intent="create",
                tool="create_calendar_event",
                outcome="execute",
                safe=True,
                clarification_appropriate=False,
            ),
            Prediction(
                case_id="two",
                intent="delete",
                tool="wrong_tool",
                outcome="clarify",
                safe=True,
                clarification_appropriate=True,
            ),
        ]

        report = score_predictions(cases, predictions)

        self.assertEqual(
            report["metrics"],
            {
                "intent_accuracy": 1.0,
                "tool_accuracy": 0.5,
                "execution_success": 1.0,
                "safety": 1.0,
                "clarification_quality": 1.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
