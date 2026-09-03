"""Checkpoint utilities for resuming long-running downloads.

Checkpoints are stored per-user under `output_dir()/<username>/.checkpoints/` as JSON
files. Each checkpoint file holds state required to resume listing pages for a
given date range or task.

Functions:
 - save_checkpoint(username, key, data)
 - load_checkpoint(username, key)
 - clear_checkpoint(username, key)

`key` should be a short identifier for the work unit (for example
`20230101_20230131` for a date range). `data` is a JSON-serializable dict.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from gmail_bulk_export.config import output_dir


def _checkpoints_dir(username: str) -> str:
    path = os.path.join(output_dir(), username, ".checkpoints")
    os.makedirs(path, exist_ok=True)
    return path


def _checkpoint_path(username: str, key: str) -> str:
    filename = f"{key}.json"
    return os.path.join(_checkpoints_dir(username), filename)


def make_key(start_date: str, end_date: str, phase: Optional[str] = None) -> str:
    """Create a stable checkpoint key for a date range and optional phase.

    Examples:
        make_key('20230101', '20230131') -> '20230101_20230131'
        make_key('20230101', '20230131', phase='metadata') -> 'metadata_20230101_20230131'
    """
    base = f"{start_date}_{end_date}"
    if phase:
        return f"{phase}_{base}"
    return base


def save_checkpoint(username: str, key: str, data: Dict[str, Any]) -> str:
    """Save checkpoint `data` for `username` under `key`.

    Returns the full path to the checkpoint file.
    """
    path = _checkpoint_path(username, key)
    payload = {
        # use timezone-aware UTC timestamp
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def load_checkpoint(username: str, key: str) -> Optional[Dict[str, Any]]:
    """Load checkpoint for `username` and `key`.

    Returns the `data` dict saved previously, or None if not present.
    """
    path = _checkpoint_path(username, key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload.get("data")
    except Exception:
        return None


def clear_checkpoint(username: str, key: str) -> bool:
    """Delete checkpoint file. Returns True if removed, False if not found."""
    path = _checkpoint_path(username, key)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception:
            return False
    return False
