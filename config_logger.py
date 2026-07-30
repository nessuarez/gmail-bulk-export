"""Configure the logger for the application."""

import logging
import os
import threading
import uuid

_LOGGER_NAME = "bulk_gmail"
_setup_lock = threading.Lock()


def get_app_logger(log_dir: str = "logs") -> logging.Logger:
    """Return the shared application logger, configuring it exactly once.

    Every module calls this at import time. Configuring on each call attached a
    fresh pair of handlers to the same underlying logger, so a run with nine
    importers wrote every line nine times into nine different files.
    """
    logger = logging.getLogger(_LOGGER_NAME)

    with _setup_lock:
        if getattr(logger, "_bulk_gmail_configured", False):
            return logger

        # Avoid circular import: use os.makedirs directly instead of importing from core.utils
        os.makedirs(log_dir, exist_ok=True)

        # Generate a unique log file path per execution
        random_suffix = uuid.uuid4().hex
        log_file_path = os.path.join(log_dir, f"log_{random_suffix}.txt")

        formatter = logging.Formatter("%(asctime)s %(levelname)8s %(name)s | %(message)s")

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)

        logger.setLevel(logging.INFO)
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        # Handlers are attached here; don't let the root logger duplicate them.
        logger.propagate = False
        logger._bulk_gmail_configured = True
        logger.info("Logger configured successfully. Log file: %s", log_file_path)

    return logger
