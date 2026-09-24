import pytest
from pydantic import ValidationError

from app.evaluator import evaluate
from app.models import Evaluation, RetrievedDocument


def test_coverage_counts_unique_objective_tokens():
    assert (
        evaluate("alpha alpha beta", "alpha gamma", []).objective_token_coverage == 0.5
    )


def test_objective_without_tokens_has_zero_coverage():
    assert evaluate("the and", "alpha", []).objective_token_coverage == 0


@pytest.mark.parametrize(
    "message,expected", [("", False), ("   ", False), ("alpha", True)]
)
def test_message_presence(message, expected):
    assert evaluate("alpha", message, []).has_message is expected


@pytest.mark.parametrize("size,expected", [(4000, True), (4001, False)])
def test_message_length_boundary(size, expected):
    assert evaluate("alpha", "x" * size, []).within_message_limit is expected


def test_retrieved_count_matches_supplied_evidence():
    docs = [RetrievedDocument(document_id="a", excerpt="alpha", score=1)]
    assert evaluate("alpha", "alpha", docs).retrieved_document_count == 1


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1, 2])
def test_response_metrics_reject_invalid_scores(score):
    with pytest.raises(ValidationError):
        Evaluation(
            objective_token_coverage=score,
            retrieved_document_count=0,
            has_message=True,
            within_message_limit=True,
        )
