import logging
import os
from typing import Optional

from dateutil import parser as date_parser

from gmail_bulk_export.config import output_dir


def ensure_dir_exists(path):
    """Ensure the directory exists for the given path."""
    if not os.path.exists(path):
        os.makedirs(path)


def parse_email_date(date: str, logger: logging.Logger) -> str:
    """Parses a date string from an email and returns it in 'YYYY-MM-DD' format.
    Args:
        date (str): The date string to parse, which may include " (UTC)" at the end.
    """
    try:
        date = date.replace(" (UTC)", "")
        date_obj = date_parser.parse(date)
        date_str = date_obj.strftime("%Y-%m-%d")
    except (ValueError, TypeError) as e:
        logger.info("Could not convert the date: %s", e)
        date_str = "Unknown"
    return date_str


def generate_base_dir(
    username: str, email_datetime: Optional[str] = None, output_dir: str = output_dir()
) -> str:
    """Generate base directory path for email storage."""
    # For generate_base_dir, we need a logger to call parse_email_date.
    # Create a minimal logger inline to avoid circular imports.
    logger = logging.getLogger(__name__)
    date_str = parse_email_date(email_datetime, logger) if email_datetime else "Unknown"
    base_dir = (
        os.path.join(output_dir, username, date_str)
        if date_str
        else os.path.join(output_dir, username)
    )

    return base_dir


# Backwards-compatible alias used in some modules
def ensure_directory(path):
    return ensure_dir_exists(path)
