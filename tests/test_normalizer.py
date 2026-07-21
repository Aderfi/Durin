"""Offline tests for the retrieve-then-rank term normalizer.

No SapBERT and no real GGUF load: the retrieval stage is a fake
CandidateGenerator and ``normalizer_module.Llama`` is monkeypatched to a fake
model, so only the ranking/parsing logic in ``LocalLLMNormalizer`` runs.
"""

import logging

import pytest

from src.data.pharmacovigilance import normalizer as normalizer_module
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


class _FakeLlama:
    """Stand-in for ``llama_cpp.Llama``: records calls, returns a scripted answer."""

    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.calls: list[dict] = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture(autouse=True)
def _no_real_gguf_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never load a real GGUF file while constructing a normalizer under test."""
    monkeypatch.setattr(normalizer_module, "Llama", lambda **kwargs: _FakeLlama())


def _make_normalizer(candidates, content=None, exc=None) -> LocalLLMNormalizer:
    normalizer = LocalLLMNormalizer(_FakeGenerator(candidates))
    normalizer.llm = _FakeLlama(content, exc)  # scripted answer for test
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
    assert normalizer.llm.calls == []  # model never queried


def test_exception_logs_and_returns_none(caplog):
    normalizer = _make_normalizer(_CANDIDATES, exc=RuntimeError("llama.cpp down"))
    with caplog.at_level(logging.WARNING):
        assert normalizer.normalize("headache") is None
    assert any("LLM normalize failed" in r.message for r in caplog.records)
