"""Free-text -> MedDRA term normalizer (ETL-only).

Maps free-text adverse-reaction text (e.g. from openFDA labels) to a MedDRA
Preferred Term from a closed vocabulary. It NEVER asserts a clinical fact — the
assertion comes from the source; this only assigns a code. Used solely by the
offline ETL; runtime never calls it.

``LocalLLMNormalizer`` is a placeholder for a locally-hosted open-source model
(e.g. Qwen / Gemma) to be implemented by the maintainer. It adds NO model
dependency here.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TermNormalizer(Protocol):
    """Maps free text to a MedDRA (Preferred Term, code) pair, or None."""

    def normalize(self, text: str) -> tuple[str, str] | None: ...


class LocalLLMNormalizer:
    """Placeholder normalizer backed by a future local open-source LLM.

    Not yet implemented. Wire a locally-hosted model here; keep the mapping
    constrained to the MedDRA vocabulary so it codes rather than invents.
    """

    def normalize(self, text: str) -> tuple[str, str] | None:
        raise NotImplementedError("Local LLM normalizer not yet implemented")
