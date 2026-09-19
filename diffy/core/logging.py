from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path


APPLICATION_NAME = "diffy"
LOG_DIRECTORY = Path.home() / "Library" / "Logs" / APPLICATION_NAME
LOG_FILE = LOG_DIRECTORY / f"{APPLICATION_NAME}.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def configure_logging() -> Path:
    logger = logging.getLogger(APPLICATION_NAME)
    if logger.handlers:
        return LOG_FILE
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(process)d %(threadName)s %(name)s: %(message)s",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    def log_uncaught_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    def log_thread_exception(args):
        logger.critical(
            "Unhandled thread exception",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_uncaught_exception
    threading.excepthook = log_thread_exception
    logger.info(
        "Logging initialized path=%s max_bytes=%d backup_count=%d",
        LOG_FILE,
        MAX_LOG_BYTES,
        BACKUP_COUNT,
    )
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{APPLICATION_NAME}.{name}")
