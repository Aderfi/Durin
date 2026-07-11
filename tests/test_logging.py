import logging
from pathlib import Path

from src.utils.logging import get_logger, setup_logging


def test_setup_logging_writes_to_file(tmp_path: Path):
    setup_logging(log_dir=tmp_path)
    logger = get_logger("durin.test")
    logger.warning("hello-durin")

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = tmp_path / "durin.log"
    assert log_file.exists()
    assert "hello-durin" in log_file.read_text()


def test_get_logger_returns_named_logger():
    assert get_logger("durin.sample").name == "durin.sample"
