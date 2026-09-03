"""Central configuration for gmail-bulk-export.

Values are read from the **working directory of whatever is running**, not from
the package: `config.json`, `.env`, `output/` and `logs/` all belong to the
deployment, not to the engine.

This module loads configuration from:
1. config.json (default values)
2. .env file (environment variables override JSON)
3. Command-line arguments (highest priority, handled in main.py)

Configuration priority (highest to lowest):
- Command-line arguments
- Environment variables (.env)
- config.json
- Hardcoded defaults
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def config_file() -> Path:
    """Where `config.json` is looked up, resolved at call time.

    The **working directory**, not `Path(__file__).parent`. This module ships
    inside an installed package: a `__file__`-relative path would read the
    engine's own `config.json` from site-packages and silently ignore the one
    belonging to the deployment that is actually running — the same class of
    bug as binding `OUTPUT_DIR` at import time. Every entry point already has
    to run from the project root.

    `GMAIL_BULK_EXPORT_CONFIG` overrides it for anything that cannot.
    """
    override = os.getenv("GMAIL_BULK_EXPORT_CONFIG")
    return Path(override) if override else Path.cwd() / "config.json"


# Labels whose message and thread counts are requested one by one. Standard
# Gmail labels only: an organisation's own labels are added from config.json or
# DETAILED_LABELS, never here.
DEFAULT_DETAILED_LABELS = ("INBOX", "SENT", "UNREAD", "CHAT")

# Global config dictionary
_config: Dict[str, Any] = {}


def load_config() -> Dict[str, Any]:
    """
    Load configuration from config.json and .env file.

    Returns:
        Dict containing all configuration values
    """
    global _config

    # Load defaults from config.json
    config_path = config_file()
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                _config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load config.json: {e}")
            _config = {}
    else:
        _config = {}

    # Override with environment variables
    _config["output_directory"] = os.getenv("OUTPUT_DIR", _config.get("output_directory", "output"))
    _config["credentials_filename"] = os.getenv(
        "CREDENTIALS_FILE", os.getenv("CREDENTIALS_FILENAME", "client_secret.json")
    )
    _config["log_dir"] = os.getenv("LOG_DIR", _config.get("log_dir", "logs"))
    _config["batch_size"] = int(os.getenv("BATCH_SIZE", _config.get("batch_size", 100)))
    _config["payload_batch_size"] = int(
        os.getenv("PAYLOAD_BATCH_SIZE", _config.get("payload_batch_size", 20))
    )
    _config["max_workers"] = int(os.getenv("MAX_WORKERS", _config.get("max_workers", 5)))
    _config["retry_attempts"] = int(os.getenv("RETRY_ATTEMPTS", _config.get("retry_attempts", 10)))
    _config["retry_wait_min"] = int(os.getenv("RETRY_WAIT_MIN", _config.get("retry_wait_min", 4)))
    _config["retry_wait_max"] = int(os.getenv("RETRY_WAIT_MAX", _config.get("retry_wait_max", 10)))
    # Target Gmail API pace. The per-user quota is 250 units/s and both
    # messages.list and messages.get cost 5 units, so ~50 msg/s is the ceiling.
    _config["messages_per_second"] = float(
        os.getenv("MESSAGES_PER_SECOND", _config.get("messages_per_second", 40))
    )
    from_env = os.getenv("DETAILED_LABELS")
    _config["detailed_labels"] = (
        [name.strip() for name in from_env.split(",") if name.strip()]
        if from_env
        else _config.get("detailed_labels", list(DEFAULT_DETAILED_LABELS))
    )

    return _config


def get_config(key: str, default: Any = None) -> Any:
    """
    Get a configuration value.

    Args:
        key: Configuration key
        default: Default value if key not found

    Returns:
        Configuration value or default
    """
    if not _config:
        load_config()
    return _config.get(key, default)


def output_dir() -> str:
    """Root of all downloaded data, resolved at call time.

    Deliberately a function rather than a constant. Every module used to do
    `from config import OUTPUT_DIR`, which binds the value at import time —
    before `update_config()` has seen the command line. That is what made
    `--output-dir` accept a path and then silently ignore it.

    Call this at the point of use. Do not assign it to a module-level name, and
    do not use it as a function default (defaults are evaluated once, when the
    function is defined, which reintroduces exactly the same bug).
    """
    return get_config("output_directory", "output")


def credentials_file() -> str:
    """Path to the service-account key, resolved at call time.

    Same reasoning as `output_dir()`: as an import-time constant it made
    `--credentials-file` a no-op.
    """
    return get_config("credentials_filename", "client_secret.json")


def detailed_labels() -> list:
    """Labels to request detailed counts for, resolved at call time.

    The list used to be hardcoded in `cli/gmail_labels_downloader.py` and
    included one organisation's own label, which tied an otherwise generic
    module to a single deployment. The default is now the standard Gmail labels
    only; each deployment adds its own in `config.json` or in `DETAILED_LABELS`
    (comma separated).
    """
    return list(get_config("detailed_labels", DEFAULT_DETAILED_LABELS))


def update_config(**kwargs) -> None:
    """
    Update configuration values (e.g., from command-line arguments).

    Args:
        **kwargs: Configuration key-value pairs to update
    """
    global _config, LOG_DIR
    global BATCH_SIZE, PAYLOAD_BATCH_SIZE, MAX_WORKERS
    global RETRY_ATTEMPTS, RETRY_WAIT_MIN, RETRY_WAIT_MAX, MESSAGES_PER_SECOND

    if not _config:
        load_config()
    _config.update(kwargs)

    # Update module-level constants
    LOG_DIR = _config.get("log_dir", "logs")
    BATCH_SIZE = _config.get("batch_size", 100)
    PAYLOAD_BATCH_SIZE = _config.get("payload_batch_size", 20)
    MAX_WORKERS = _config.get("max_workers", 5)
    RETRY_ATTEMPTS = _config.get("retry_attempts", 10)
    RETRY_WAIT_MIN = _config.get("retry_wait_min", 4)
    RETRY_WAIT_MAX = _config.get("retry_wait_max", 10)
    MESSAGES_PER_SECOND = _config.get("messages_per_second", 40)


# Load configuration on module import
load_config()

# Export commonly used config values as module-level constants.
#
# OUTPUT_DIR and CREDENTIALS_FILE are deliberately NOT here: they are the two
# values the CLI lets you override, and as constants they were captured before
# the command line was parsed. Use output_dir() and credentials_file().
LOG_DIR = _config.get("log_dir", "logs")
BATCH_SIZE = _config.get("batch_size", 100)
PAYLOAD_BATCH_SIZE = _config.get("payload_batch_size", 20)
MAX_WORKERS = _config.get("max_workers", 5)
RETRY_ATTEMPTS = _config.get("retry_attempts", 10)
RETRY_WAIT_MIN = _config.get("retry_wait_min", 4)
RETRY_WAIT_MAX = _config.get("retry_wait_max", 10)
MESSAGES_PER_SECOND = _config.get("messages_per_second", 40)
