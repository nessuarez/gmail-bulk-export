"""Module for handling file-related operations."""

import os
from typing import Optional

# Aliased because the parameter below is also called output_dir.
from config import output_dir as configured_output_dir
from config_logger import get_app_logger
from core.utils import ensure_dir_exists, generate_base_dir, parse_email_date

logger = get_app_logger()


def check_email_downloaded(
    username: str,
    msg_id: str,
    msg_datetime: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> bool:
    """Checks if an email with the given message ID has already been downloaded."""
    file_path = generate_email_file_path(username, msg_id, msg_datetime, output_dir)
    return os.path.exists(file_path)


def generate_email_file_path(
    username: str,
    msg_id: str,
    email_datetime: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Generates the file path for saving an email's payload as a JSON file.

    `output_dir` defaults to None and is resolved here rather than in the
    signature: a default is evaluated once, when the function is defined, which
    would capture the configured path before the command line was parsed.
    """
    output_dir = output_dir if output_dir is not None else configured_output_dir()
    date_str = parse_email_date(email_datetime, logger) if email_datetime else "Unknown"
    base_dir = generate_base_dir(username, date_str, output_dir)
    ensure_dir_exists(base_dir)
    file_path = os.path.join(base_dir, f"{msg_id}.jsonl.gz")
    return file_path


def find_email_file(base_dir: str, message_id: str) -> Optional[str]:
    """Busca el archivo JSON del email en subcarpetas del directorio base."""
    for root, _, files in os.walk(base_dir):
        if f"{message_id}.json" in files:
            return os.path.join(root, f"{message_id}.json")
    return None
