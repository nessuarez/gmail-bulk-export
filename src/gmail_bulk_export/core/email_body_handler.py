"""Module for handling email body extraction and processing."""

import email.message
from email.header import decode_header
from typing import Optional, Tuple

import html2text

from gmail_bulk_export.config_logger import get_app_logger

logger = get_app_logger()


def decode_header_value(raw: Optional[str]) -> Optional[str]:
    """RFC 2047 -> text: `=?UTF-8?Q?Confirmaci=C3=B3n?=` -> `Confirmación`.

    Headers untouched by `process_email_raw_response` before this change went
    straight from `msg.get(...)` into the JSONL, encoded-word and all — any
    subject or name outside ASCII came out unreadable.
    """
    if raw is None:
        return None
    try:
        pieces = decode_header(raw)
    except Exception:
        return raw
    decoded = []
    for text, charset in pieces:
        if isinstance(text, bytes):
            decoded.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


def collect_headers(msg: email.message.Message) -> dict:
    """Every header on the MIME message, decoded.

    A header seen once comes back as a string; one repeated (`Received`, or
    `Delivered-To` on a forwarded message with several aliases) comes back as
    a list, in message order. Phase 2's metadata step keeps only the last
    `Delivered-To` — this is where the rest of the chain survives.
    """
    grouped: dict = {}
    for name, value in msg.items():
        grouped.setdefault(name, []).append(decode_header_value(value))
    return {name: values[0] if len(values) == 1 else values for name, values in grouped.items()}


def _header_list(headers: dict, name: str) -> list:
    """Case-insensitive lookup into `collect_headers()`'s output, always as a list."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value if isinstance(value, list) else [value]
    return []


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
    response: dict,
    allowed_attachment_types: Optional[list] = None,
    mailbox: Optional[str] = None,
) -> dict:
    """Processes a raw email response from the Gmail API and extracts relevant information.

    `response` is what `messages().get(..., format="raw")` returns: the full
    MIME message base64-encoded in `raw`, alongside `id`, `threadId`,
    `labelIds`, `internalDate`, `historyId` and `snippet` — fields the API
    hands over for free but that used to be discarded here, leaving the body
    file impossible to cross-reference against the metadata CSV or the search
    index except by the id embedded in its own file name.
    """
    import base64

    from gmail_bulk_export.core.attachment_handler import (
        get_attachment_metadata,
        get_filtered_attachments,
    )

    msg_str = base64.urlsafe_b64decode(response.get("raw")).decode("utf-8")
    msg = email.message_from_string(msg_str)

    # Every header on the message, decoded — Delivered-To, Received, Reply-To,
    # Return-Path, List-Id... all of it, not just the six fields below.
    headers = collect_headers(msg)

    # Extraer cabeceras
    msg_id = msg.get("Message-ID") if msg.get("Message-ID") else response.get("id")
    subject = decode_header_value(msg.get("Subject"))
    sender = decode_header_value(msg.get("From"))
    recipient = decode_header_value(msg.get("To"))
    cc = decode_header_value(msg.get("Cc"))
    bcc = decode_header_value(msg.get("Bcc"))
    reply_to = decode_header_value(msg.get("Reply-To"))
    email_received_date = msg.get("Date")
    content_type = msg.get_content_type()
    delivered_to = _header_list(headers, "Delivered-To")

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
        "gmailId": response.get("id"),
        "threadId": response.get("threadId"),
        "mailbox": mailbox,
        "date": email_received_date,
        "internalDate": response.get("internalDate"),
        "historyId": response.get("historyId"),
        "sizeEstimate": response.get("sizeEstimate"),
        "labelIds": response.get("labelIds", []),
        "snippet": response.get("snippet"),
        "from": sender,
        "to": recipient,
        "cc": cc,
        "bcc": bcc,
        "reply_to": reply_to,
        "deliveredTo": delivered_to,
        "subject": subject,
        "content_type": content_type,
        "headers": headers,
        "body_plain": plain_body,
        "body_html": html_body,
        "attachments": attachments_metadata,
        "attachments_with_content": [
            (attachment.get("filename"), attachment.get("content_type"))
            for attachment in attachments_with_content
        ],
    }
