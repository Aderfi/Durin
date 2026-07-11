import pytest

from src.data.pharmacovigilance.normalizer import LocalLLMNormalizer


def test_local_llm_normalizer_is_placeholder():
    with pytest.raises(NotImplementedError):
        LocalLLMNormalizer().normalize("stomach bleeding")
