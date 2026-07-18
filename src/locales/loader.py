import os
from functools import lru_cache
from pathlib import Path

import json5

CURRENT_LANG = os.getenv("APP_LANG", "es")
_LOCALES_DIR = Path(__file__).parent


@lru_cache
def load_locale(lang: str = "es") -> dict[str, str]:
    path = _LOCALES_DIR / f"{lang}.json5"
    return json5.loads(path.read_text(encoding="utf-8"))


def t(key: str, **kwargs) -> str:
    """Traduce una clave al idioma activo, /
    interpolando variables si se pasan."""
    catalog = load_locale(CURRENT_LANG)
    template = catalog.get(key, key)
    # si falta la clave, clave como fallback
    return template.format(**kwargs) if kwargs else template
