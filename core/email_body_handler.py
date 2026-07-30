"""Module for handling email body extraction and processing."""

import email.message
from typing import Optional, Tuple

import html2text

from config_logger import get_app_logger

logger = get_app_logger()


def extract_message_body_parts(
    msg: email.message.Message,
) -> Tuple[Optional[str], Optional[str]]:
    """Extracts the plain text and HTML body parts from an email message object."""
    plain_body = None
    html_body = None
    for part in msg.walk():
        content_type = part.get_content_type()
        charset = part.get_content_charset() or "utf-8"
        payload = part.get_payload(decode=True)
        if payload:
            try:
                decoded = payload.decode(charset, errors="replace")
                if content_type == "text/plain" and not plain_body:
                    plain_body = decoded
                elif content_type == "text/html" and not html_body:
                    html_body = decoded
                else:
                    logger.debug(
                        f"Skipping part with content type {content_type} and charset {charset}"
                    )
            except Exception as e:
                logger.error(f"Error decoding part: {e}")
    # Fallback: convertir html a texto si plain_body no está
    if not plain_body and html_body:
        plain_body = html2text.html2text(html_body)
    return plain_body, html_body


def process_email_raw_response(
    response: dict, allowed_attachment_types: Optional[list] = None
) -> dict:
    """Processes a raw email response from the Gmail API and extracts relevant information."""
    import base64

    from core.attachment_handler import (
        get_attachment_metadata,
        get_filtered_attachments,
    )

    msg_str = base64.urlsafe_b64decode(response.get("raw")).decode("utf-8")
    msg = email.message_from_string(msg_str)

    # Extraer cabeceras
    msg_id = msg.get("Message-ID") if msg.get("Message-ID") else response.get("id")
    subject = msg.get("Subject")
    sender = msg.get("From")
    recipient = msg.get("To")
    email_received_date = msg.get("Date")
    content_type = msg.get_content_type()

    # Extraer cuerpos
    plain_body, html_body = extract_message_body_parts(msg)

    # Extraer adjuntos
    attachments = get_attachment_metadata(msg)
    attachments_with_content = get_filtered_attachments(msg, allowed_attachment_types)

    # Almacenar email. The record is JSON-serialized, so the raw `payload` bytes
    # that get_attachment_metadata carries must be dropped here — only the
    # descriptive fields belong in the body file. The bytes themselves are
    # written separately by save_attachments().
    attachments_metadata = [
        {key: value for key, value in attachment.items() if key != "payload"}
        for attachment in attachments
    ]

    return {
        "id": msg_id,
        "date": email_received_date,
        "from": sender,
        "to": recipient,
        "subject": subject,
        "content_type": content_type,
        "body_plain": plain_body,
        "body_html": html_body,
        "attachments": attachments_metadata,
        "attachments_with_content": [
            (attachment.get("filename"), attachment.get("content_type"))
            for attachment in attachments_with_content
        ],
    }
