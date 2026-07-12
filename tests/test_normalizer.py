"""Offline tests for the retrieve-then-rank term normalizer.

No torch and no Ollama: the retrieval stage is a fake CandidateGenerator and the
Ollama client is replaced with a stub, so only the ranking/parsing logic runs.
"""

import logging

from src.data.pharmacovigilance.normalizer import (
    CandidateGenerator,
    LocalLLMNormalizer,
)

_CANDIDATES = [("Insomnia", "10022437"), ("Headache", "10019211")]


class _FakeGenerator:
    """CandidateGenerator returning a fixed shortlist (top-k slice)."""

    def __init__(self, candidates: list[tuple[str, str]]) -> None:
        self._candidates = candidates

    def candidates(self, text: str, k: int) -> list[tuple[str, str]]:
        return self._candidates[:k]


class _StubCompletions:
    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        message = type("_Msg", (), {"content": self._content})()
        choice = type("_Choice", (), {"message": message})()
        return type("_Resp", (), {"choices": [choice]})()


class _StubClient:
    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self.completions = _StubCompletions(content, exc)
        self.chat = type("_Chat", (), {"completions": self.completions})()


def _make_normalizer(candidates, content=None, exc=None) -> LocalLLMNormalizer:
    normalizer = LocalLLMNormalizer(_FakeGenerator(candidates))
    normalizer.client = _StubClient(content, exc)  # replace the live Ollama client
    return normalizer


def test_fake_generator_satisfies_protocol():
    assert isinstance(_FakeGenerator(_CANDIDATES), CandidateGenerator)


def test_picks_candidate_by_index():
    normalizer = _make_normalizer(_CANDIDATES, content="1")
    assert normalizer.normalize("cannot sleep") == ("Insomnia", "10022437")


def test_parses_index_from_noisy_answer():
    normalizer = _make_normalizer(_CANDIDATES, content="The best match is 2.")
    assert normalizer.normalize("pounding head") == ("Headache", "10019211")


def test_zero_means_none():
    normalizer = _make_normalizer(_CANDIDATES, content="0")
    assert normalizer.normalize("green nausea aura") is None


def test_non_numeric_answer_is_none():
    normalizer = _make_normalizer(_CANDIDATES, content="NONE")
    assert normalizer.normalize("gibberish") is None


def test_out_of_range_index_is_none():
    normalizer = _make_normalizer(_CANDIDATES, content="5")
    assert normalizer.normalize("something") is None


def test_empty_shortlist_skips_the_model():
    normalizer = _make_normalizer([], content="1")
    assert normalizer.normalize("anything") is None
    assert normalizer.client.completions.calls == []  # model never queried


def test_exception_logs_and_returns_none(caplog):
    normalizer = _make_normalizer(_CANDIDATES, exc=RuntimeError("ollama down"))
    with caplog.at_level(logging.WARNING):
        assert normalizer.normalize("headache") is None
    assert any("LLM normalize failed" in r.message for r in caplog.records)
