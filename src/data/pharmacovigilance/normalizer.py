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
2. Ranking / disambiguation -- a locally-hosted LLM (via llama.cpp, in-process
   GGUF) picks ONE candidate from the shortlist, or NONE. A GBNF grammar
   constrains the model to emit only digits, so the answer is always a clean
   index rather than free text to parse.

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

import os

from llama_cpp import Llama, LlamaGrammar

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Constants for the local LLM normalizer (llama.cpp, in-process GGUF)

LLM_MODEL_PATH = os.getenv("LLM_MODEL_PATH", "/models/gemma4_e4b_it.gguf")

SAPBERT_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"

DEFAULT_TOP_K = 5

DEFAULT_PARAMS = {
    "temperature": 0.0,
    "top_k": 1,
    "top_p": 1.0,
    "min_p": 0.0,
    "repeat_penalty": 1.0,
    "seed": 711,
}

ANSWER_GRAMMAR = "root ::= [0-9]+"

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
    """Normalizer that ranks retrieved candidates with a local llama.cpp LLM.

    Loads a GGUF model in-process (no server) via `llama_cpp.Llama`. A GBNF
    grammar (`ANSWER_GRAMMAR`) constrains generation to digits only, so the
    model can neither invent a code nor choose outside the closed vocabulary
    -- it only ever emits the *index* of one retrieved candidate (or 0 for
    NONE); the (Preferred Term, code) pair is read from the local candidate
    table.
    """

    def __init__(
        self,
        candidate_generator: CandidateGenerator,
        model_path: str = LLM_MODEL_PATH,
        top_k: int = DEFAULT_TOP_K,
        params: dict | None = None,
    ) -> None:
        """Initialize the local LLM normalizer (llama.cpp backend).

        Args:
            candidate_generator: Retrieval stage that shortlists candidates.
            model_path: Path to the local .gguf model file.
            top_k: Number of candidates to retrieve and present to the model.
            params: Sampling params override (defaults to DEFAULT_PARAMS).
        """
        self.generator = candidate_generator
        self.top_k = top_k
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.grammar = LlamaGrammar.from_string(ANSWER_GRAMMAR)
        self.llm = Llama(
            model_path=model_path,
            n_ctx=2048,
            seed=self.params["seed"],
            verbose=False,
            flash_attn=True,
        )

    def _build_prompt(self, text: str, candidates: list[tuple[str, str]]) -> str:
        listing = "\n".join(
            f"{i}. {pt}" for i, (pt, _) in enumerate(candidates, start=1)
        )
        return self._render_prompt(text, listing)
    
    def _render_prompt(self, text: str, listing: str) -> str:
        return (
        "You are a strict medical coding assistant performing entity linking "
        "between free-text adverse-reaction descriptions and MedDRA Preferred "
        "Terms (PTs).\n\n"
        "Rules:\n"
        "- Choose the single candidate PT that best matches the MEDICAL MEANING "
        "of the adverse-reaction text, not surface wording similarity.\n"
        "- A match must be a legitimate synonym, abbreviation, lay term, or "
        "narrower/broader clinical expression of the SAME clinical concept. "
        "Do not match based on partial word overlap alone.\n"
        "- If two candidates seem plausible, choose the more clinically precise "
        "one, not the most general.\n"
        "- If the text describes a concept not represented by any candidate, "
        "or is too vague/ambiguous to map confidently to one specific PT, "
        "answer 0. It is always better to answer 0 than to guess.\n"
        "- Do not use outside medical knowledge to reinterpret the text beyond "
        "what it states. Do not infer a diagnosis or mechanism not implied by "
        "the wording itself.\n\n"
        "Output format:\n"
        "- Answer with ONLY the number of the best-matching candidate, or 0 if "
        "none apply.\n"
        "- No explanation, no punctuation, no extra text, no restating the "
        "number in words. Output must be a single integer and nothing else.\n\n"
        f'Adverse-reaction text: "{text}"\n\n'
        f"Candidates:\n{listing}\n\n"
        "Answer:"
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
            completion = self.llm.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=self.params["temperature"],
                top_k=self.params["top_k"],
                top_p=self.params["top_p"],
                min_p=self.params["min_p"],
                repeat_penalty=self.params["repeat_penalty"],
                max_tokens=4,
                grammar=self.grammar,
            )
            answer = (completion["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            logger.warning("LLM normalize failed for %r: %s", text, exc)
            return None

        index = self._parse_index(answer)
        # 0 (NONE), a missing number, or an out-of-range pick all drop the term
        # rather than risk a wrong code.
        if index is None or not (1 <= index <= len(candidates)):
            return None

        return candidates[index - 1]