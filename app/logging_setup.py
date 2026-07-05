"""
logging_setup.py — Configures the root logger for the clinical RAG system.

Usage:
    from app.config import get_config
    from app.logging_setup import setup_logging

    cfg = get_config()
    setup_logging(cfg)

After this call every module can use:
    import logging
    logger = logging.getLogger(__name__)
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

# Track whether setup has already been applied so the function is idempotent.
_logging_configured: bool = False

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
LOG_FILENAME = "clinical_rag.log"


def setup_logging(config) -> None:
    """Configure the root logger.

    - Adds a TimedRotatingFileHandler (rotates at midnight, retains
      *config.logging.retention_days* backup files).
    - Adds a StreamHandler for console output.
    - Creates *config.logs_dir* if it does not exist.
    - Is idempotent: calling this function more than once has no effect.

    Parameters
    ----------
    config:
        An AppConfig instance (or any object with .logs_dir and .logging
        attributes where .logging has .level and .retention_days).
    """
    global _logging_configured
    if _logging_configured:
        return

    # Resolve the log directory; create it if necessary.
    logs_dir: str = config.logs_dir
    os.makedirs(logs_dir, exist_ok=True)

    log_file_path = os.path.join(logs_dir, LOG_FILENAME)

    # Resolve the numeric log level.
    level_name: str = config.logging.level.upper()
    numeric_level: int = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT)

    # --- Rotating file handler -------------------------------------------
    file_handler = TimedRotatingFileHandler(
        filename=log_file_path,
        when="midnight",
        backupCount=config.logging.retention_days,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)

    # --- Console (stream) handler ----------------------------------------
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(numeric_level)
    stream_handler.setFormatter(formatter)

    # --- Root logger configuration ---------------------------------------
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    _logging_configured = True

    root_logger.info(
        "Logging initialised — level=%s, file=%s, retention=%d days",
        level_name,
        log_file_path,
        config.logging.retention_days,
    )


def reset_logging() -> None:
    """Reset the idempotency flag (useful in tests)."""
    global _logging_configured
    _logging_configured = False
