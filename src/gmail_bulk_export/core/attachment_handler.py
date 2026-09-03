"""Module for handling email attachments."""

import email.message
import gzip
import os
from typing import Any, Dict, List, Optional

from gmail_bulk_export.config_logger import get_app_logger
from gmail_bulk_export.core.utils import ensure_directory

logger = get_app_logger()


def get_attachment_metadata(msg: email.message.Message) -> List[Dict[str, Any]]:
    """Extract attachments metadata from the given email message."""
    attachments = []
    for part in msg.walk():
        if part.get_filename() and part.get_content_disposition():
            content_type = part.get_content_type()
            filename = part.get_filename()
            charset = part.get_content_charset() or "utf-8"
            attachments.append(
                {
                    "attachment_id": part.get("Content-ID"),
                    "filename": filename,
                    "content_type": content_type,
                    "disposition": part.get_content_disposition(),
                    "charset": charset,
                    "payload": part.get_payload(decode=True),
                    "size": (
                        len(part.get_payload(decode=True)) if part.get_payload(decode=True) else 0
                    ),
                }
            )
    return attachments


def get_filtered_attachments(
    msg: email.message.Message, allowed_content_types: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Extract attachments from the given email message, filtering by allowed content types."""
    attachments = []
    for part in msg.walk():
        if part.get_filename() and part.get_content_disposition() == "attachment":
            content_type = part.get_content_type()
            if allowed_content_types is None or content_type in allowed_content_types:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        attachments.append(
                            {
                                "filename": part.get_filename(),
                                "content_type": content_type,
                                "payload": payload,
                            }
                        )
                        logger.debug(
                            "Extracted attachment with content: %s (%s)",
                            part.get_filename(),
                            content_type,
                        )
                    else:
                        logger.warning(f"Attachment {part.get_filename()} has no payload.")
                except Exception as e:
                    logger.error("Error extracting attachment %s: %s", part.get_filename(), e)
            else:
                logger.debug(
                    "Skipping attachment %s due to content type: %s",
                    part.get_filename(),
                    content_type,
                )
    return attachments


def generate_attachment_file_path(
    base_dir: str,
    msg_id: str,
) -> str:
    """Generates the file path for saving attachments as a gzipped file."""
    file_path = os.path.join(base_dir, f"{msg_id}_attachments.gz")
    return file_path


def save_attachments(file_path: str, attachments: List[Dict[str, Any]], msg_id: str) -> None:
    """Saves attachments to a gzipped file."""
    if not attachments:
        logger.info(f"No attachments to save for email {msg_id}")
        return

    ensure_directory(os.path.dirname(file_path))

    try:
        with gzip.open(file_path, "wb") as gzfile:  # Open in binary write mode
            for attachment in attachments:
                filename = attachment.get("filename")
                content = attachment.get("payload")
                # Write each attachment as a separate file entry inside the gzipped file
                gzfile.write((f"--filename:{filename}--\n").encode("utf-8"))
                gzfile.write(content)
                gzfile.write(b"\n")
        logger.info(f"Attachments for email {msg_id} saved to {file_path}")
    except Exception as e:
        logger.error(f"Error saving attachments for email {msg_id}: {e}")
