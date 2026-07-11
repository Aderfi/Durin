"""Central logging configuration for Durin.

Configures the root logger once with a console handler and a rotating file
handler under ``logs/``. All modules obtain their logger via ``get_logger`` and
never configure handlers themselves. No error is ever swallowed silently.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3

_configured = False


def setup_logging(level: int = logging.INFO, log_dir: Path | None = None) -> None:
    """Configure the root logger (idempotent).

    Adds a console handler and a rotating file handler writing to
    ``<log_dir>/durin.log``. Safe to call multiple times; only the first call
    installs handlers.
    """
    global _configured
    directory = log_dir or _DEFAULT_LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove handlers from a previous configuration (e.g. tests with a new dir).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        directory / "durin.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, configuring logging with defaults on first use."""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
