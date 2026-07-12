"""Free-text -> MedDRA term normalizer (ETL-only).

Maps free-text adverse-reaction text (e.g. from openFDA labels) to a MedDRA
Preferred Term drawn from a closed vocabulary. This is medical concept
normalization (entity linking), NOT clinical reasoning: it never asserts a
fact -- the assertion comes from the source label; this only attaches a code.
Used solely by the offline ETL (Tier 2); runtime never calls it.

Architecture is the industry-standard retrieve-then-rank:

1. Candidate generation -- a biomedical bi-encoder (SapBERT) embeds the query
   text and the closed vocabulary, then retrieves the top-K nearest Preferred
   Terms by cosine similarity. This handles colloquial label phrasing
   ("can't sleep" -> "Insomnia") that plain string matching misses.
2. Ranking / disambiguation -- a locally-hosted LLM (via Ollama) picks ONE
   candidate from the shortlist, or NONE.

Safety by construction:
- Closed vocabulary: the LLM only ever chooses among the retrieved candidates.
- The LLM returns a candidate *index*, never a code and never free text; the
  (Preferred Term, code) pair is read from the local table, so a hallucinated
  or malformed code is impossible.
- Deterministic (temperature=0.0).
- Precision-first: an unparseable/out-of-range answer or NONE yields None, so
  ambiguous text is dropped rather than mis-coded.

The heavy embedding dependency (``sentence-transformers``) is imported lazily
inside ``SapBERTCandidateGenerator`` and lives in the ``extras`` group, so the
core package can import this module without pulling torch.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from openai import OpenAI

from src.utils.logging import get_logger

logger = get_logger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
LLM_MODEL = "gemma2"
SAPBERT_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
DEFAULT_TOP_K = 25


@runtime_checkable
class TermNormalizer(Protocol):
    """Maps free text to a MedDRA (Preferred Term, code) pair, or None."""

    def normalize(self, text: str) -> tuple[str, str] | None: ...


@runtime_checkable
class CandidateGenerator(Protocol):
    """Retrieves the closest (Preferred Term, code) candidates for free text.

    The retrieval stage of retrieve-then-rank. Implementations shrink the closed
    vocabulary (thousands of Preferred Terms) to a small shortlist the ranking
    LLM can actually choose from.
    """

    def candidates(self, text: str, k: int) -> list[tuple[str, str]]: ...


class SapBERTCandidateGenerator:
    """Dense candidate generator backed by the SapBERT biomedical bi-encoder.

    Embeds every Preferred Term in the closed vocabulary once at construction,
    then retrieves the top-K nearest terms to the query by cosine similarity.
    SapBERT is trained on UMLS synonymy, so it links colloquial adverse-event
    phrasing to the canonical MedDRA term far better than lexical matching.
    """

    def __init__(
        self,
        meddra_terms: dict[str, str],
        model_name: str = SAPBERT_MODEL,
    ) -> None:
        """Initialize and pre-encode the closed vocabulary.

        Args:
            meddra_terms: Closed vocabulary mapping Preferred Term -> MedDRA code.
            model_name: Hugging Face id of the SapBERT-class bi-encoder to load.
        """
        # Imported lazily so the module (and the TermNormalizer protocol) can be
        # used without installing the heavy `sentence-transformers`/torch stack.
        from sentence_transformers import SentenceTransformer

        self._terms: list[tuple[str, str]] = list(meddra_terms.items())
        self._model = SentenceTransformer(model_name)
        preferred_terms = [pt for pt, _ in self._terms]
        self._embeddings = self._model.encode(
            preferred_terms,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )

    def candidates(self, text: str, k: int) -> list[tuple[str, str]]:
        if not self._terms:
            return []
        query = self._model.encode(
            [text],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )[0]
        # Embeddings are L2-normalized, so the dot product is cosine similarity.
        scores = self._embeddings @ query
        top = scores.argsort()[::-1][:k]
        return [self._terms[i] for i in top]


class LocalLLMNormalizer:
    """Normalizer that ranks retrieved candidates with a local LLM via Ollama.

    Uses Ollama's OpenAI-compatible API (through the `openai` client). The LLM
    only ever picks the *index* of one retrieved candidate (or 0 for NONE); the
    (Preferred Term, code) pair is read from the local candidate table, so the
    model can neither invent a code nor choose outside the closed vocabulary.
    """

    def __init__(
        self,
        candidate_generator: CandidateGenerator,
        model: str = LLM_MODEL,
        base_url: str = OLLAMA_BASE_URL,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        """Initialize the local LLM normalizer.

        Args:
            candidate_generator: Retrieval stage that shortlists candidates.
            model: Name of the local Ollama model to use (e.g. "gemma2", "qwen2").
            base_url: Base URL where the Ollama server is running.
            top_k: Number of candidates to retrieve and present to the model.
        """
        self.generator = candidate_generator
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.top_k = top_k
        self.client = OpenAI(base_url=f"{self.base_url}/v1", api_key="ollama")

    def _build_prompt(self, text: str, candidates: list[tuple[str, str]]) -> str:
        listing = "\n".join(
            f"{i}. {pt}" for i, (pt, _) in enumerate(candidates, start=1)
        )
        return (
            "You are a strict medical coding assistant performing entity "
            "linking. Choose the single MedDRA Preferred Term from the numbered "
            "list below that best matches the adverse-reaction text. If none "
            "apply, answer 0.\n\n"
            f'Adverse-reaction text: "{text}"\n\n'
            f"Candidates:\n{listing}\n\n"
            "Answer with ONLY the number of the best match, or 0 if none apply."
        )

    @staticmethod
    def _parse_index(answer: str) -> int | None:
        """Extract the first integer the model emitted, or None if absent."""
        match = re.search(r"\d+", answer)
        return int(match.group()) if match else None

    def normalize(self, text: str) -> tuple[str, str] | None:
        candidates = self.generator.candidates(text, self.top_k)
        if not candidates:
            return None

        prompt = self._build_prompt(text, candidates)
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            answer = (completion.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("LLM normalize failed for %r: %s", text, exc)
            return None

        index = self._parse_index(answer)
        # 0 (NONE), a missing number, or an out-of-range pick all drop the term
        # rather than risk a wrong code.
        if index is None or not (1 <= index <= len(candidates)):
            return None

        return candidates[index - 1]
