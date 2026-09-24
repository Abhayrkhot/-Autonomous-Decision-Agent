import math

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from app.models import Document
from app.retrieval import STOP_WORDS, retrieve, tokens


def test_cosine_score_matches_hand_calculation():
    hits = retrieve(
        "alpha beta",
        [Document(id="a", text="alpha alpha beta"), Document(id="b", text="alpha")],
    )
    assert hits[0].score == pytest.approx(3 / math.sqrt(10), abs=1e-6)
    assert hits[1].score == pytest.approx(1 / math.sqrt(2), abs=1e-6)


@pytest.mark.parametrize("limit", [-1, True, False, 1.5, "1", None])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="nonnegative integer"):
        retrieve("alpha", [], limit)


def test_zero_limit_returns_no_matches():
    assert retrieve("alpha", [Document(id="a", text="alpha")], 0) == []


def test_default_limit_truncates_to_three():
    hits = retrieve("alpha", [Document(id=str(i), text="alpha") for i in range(5)])
    assert [hit.document_id for hit in hits] == ["0", "1", "2"]


def test_equal_scores_use_document_id_tie_break():
    hits = retrieve(
        "alpha", [Document(id="b", text="alpha"), Document(id="a", text="alpha")]
    )
    assert [hit.document_id for hit in hits] == ["a", "b"]


@pytest.mark.parametrize("query", ["", "the and", "unrelated", "日本語"])
def test_queries_without_shared_tokens_return_no_matches(query):
    assert retrieve(query, [Document(id="a", text="alpha")]) == []


def test_excerpt_preserves_first_500_characters():
    text = "alpha " * 200
    assert retrieve("alpha", [Document(id="a", text=text)])[0].excerpt == text[:500]


def test_tokenizer_normalizes_ascii_words_and_removes_stopwords():
    assert tokens("ALPHA, the 123! 日本語") == ["alpha", "123"]


@given(st.text(max_size=2000))
def test_tokenizer_output_invariants(text):
    assert all(
        token.isascii()
        and token.isalnum()
        and token == token.lower()
        and token not in STOP_WORDS
        for token in tokens(text)
    )


TEXT = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ,.!?",
    min_size=1,
    max_size=10000,
).filter(lambda text: bool(text.strip()))


@settings(max_examples=70, deadline=None)
@given(
    query=st.text(alphabet="abcdefghijklmnopqrstuvwxyz ,.!?", max_size=4001),
    texts=st.lists(TEXT, max_size=8),
    limit=st.integers(min_value=0, max_value=10),
    permutation=st.permutations(tuple(range(8))),
)
@example(
    query=" ".join(["z"] * 1000) + " x",
    texts=[" ".join(["y"] * 4999 + ["x"])],
    limit=3,
    permutation=tuple(range(8)),
)
def test_retrieval_output_invariants(query, texts, limit, permutation):
    documents = [Document(id=str(i), text=text) for i, text in enumerate(texts)]
    hits = retrieve(query, documents, limit)
    assert all(0 < hit.score <= 1 for hit in hits)
    assert hits == sorted(hits, key=lambda hit: (-hit.score, hit.document_id))
    assert len(hits) <= limit
    shuffled = [documents[index] for index in permutation if index < len(documents)]
    assert hits == retrieve(query, shuffled, limit)
    by_id = {doc.id: doc for doc in documents}
    assert all(
        set(tokens(query)) & set(tokens(by_id[hit.document_id].text)) for hit in hits
    )


@given(z_count=st.integers(700, 1000), y_count=st.integers(3500, 4999))
def test_near_limit_skewed_scores_never_round_to_returned_zero(z_count, y_count):
    query = " ".join(["z"] * z_count + ["x"])
    doc = Document(id="skew", text=" ".join(["y"] * y_count + ["x"]))
    assert retrieve(query, [doc]) == []


@given(st.lists(st.sampled_from(["alpha", "beta", "gamma"]), min_size=1, max_size=1000))
def test_identical_query_document_has_unit_similarity(words):
    text = " ".join(words)
    assert retrieve(text, [Document(id="self", text=text)])[0].score == 1
