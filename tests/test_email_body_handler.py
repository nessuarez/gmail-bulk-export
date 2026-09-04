"""Turning a raw Gmail API response into the record the body downloader saves."""

import base64
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from gmail_bulk_export.core.email_body_handler import (
    collect_headers,
    decode_header_value,
    process_email_raw_response,
)


def make_response(**overrides):
    """A `messages().get(..., format="raw")` response, with a small canonical MIME
    message: an RFC 2047-encoded subject, a forwarded message's two
    `Delivered-To` headers, and both a plain and an HTML body part.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header("Confirmación de reserva", "utf-8").encode()
    msg["From"] = "Agencia <reservas@agencia.com>"
    msg["To"] = "cliente@example.com"
    msg["Date"] = "Wed, 17 May 2023 11:30:00 +0200"
    msg["Message-ID"] = "<abc123@agencia.com>"
    # Two Delivered-To: the alias chain a forward/alias leaves behind, and
    # exactly what phase 2's metadata step collapses to just the last one.
    msg["Delivered-To"] = "peticiones10@aervio.com"
    msg["Delivered-To"] = "peticiones@aervio.com"
    msg.attach(MIMEText("Confirmación de su reserva.", "plain", "utf-8"))
    msg.attach(MIMEText("<p>Confirmación de su reserva.</p>", "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    response = {
        "id": "1952bd04b8165682",
        "threadId": "1952bd04b8165682",
        "labelIds": ["INBOX", "AERVIO_OK"],
        "internalDate": "1684315800000",
        "historyId": "998877",
        "sizeEstimate": 4096,
        "snippet": "Confirmacion de su reserva.",
        "raw": raw,
    }
    response.update(overrides)
    return response


def test_decode_header_value_handles_rfc2047():
    encoded = Header("Confirmación", "utf-8").encode()
    assert decode_header_value(encoded) == "Confirmación"


def test_decode_header_value_leaves_plain_ascii_alone():
    assert decode_header_value("Booking confirmed") == "Booking confirmed"


def test_decode_header_value_of_none_is_none():
    assert decode_header_value(None) is None


def test_collect_headers_groups_repeated_headers_into_a_list():
    msg = MIMEMultipart()
    msg["Delivered-To"] = "peticiones10@aervio.com"
    msg["Delivered-To"] = "peticiones@aervio.com"
    msg["Subject"] = "Single header"

    headers = collect_headers(msg)

    assert headers["Delivered-To"] == ["peticiones10@aervio.com", "peticiones@aervio.com"]
    assert headers["Subject"] == "Single header"


def test_process_email_raw_response_keeps_every_header():
    email_data = process_email_raw_response(make_response(), mailbox="peticiones@aervio.com")

    assert "headers" in email_data
    assert email_data["headers"]["Delivered-To"] == [
        "peticiones10@aervio.com",
        "peticiones@aervio.com",
    ]
    assert email_data["headers"]["From"] == "Agencia <reservas@agencia.com>"


def test_process_email_raw_response_decodes_the_subject():
    email_data = process_email_raw_response(make_response())
    assert email_data["subject"] == "Confirmación de reserva"


def test_process_email_raw_response_exposes_delivered_to_as_a_list():
    email_data = process_email_raw_response(make_response())
    assert email_data["deliveredTo"] == ["peticiones10@aervio.com", "peticiones@aervio.com"]


def test_process_email_raw_response_carries_the_gmail_fields():
    email_data = process_email_raw_response(make_response(), mailbox="peticiones@aervio.com")

    assert email_data["gmailId"] == "1952bd04b8165682"
    assert email_data["threadId"] == "1952bd04b8165682"
    assert email_data["mailbox"] == "peticiones@aervio.com"
    assert email_data["internalDate"] == "1684315800000"
    assert email_data["labelIds"] == ["INBOX", "AERVIO_OK"]
    assert email_data["sizeEstimate"] == 4096


def test_process_email_raw_response_id_is_still_the_rfc822_message_id():
    """`id` keeps its old meaning on purpose — `gmailId` is the additive field."""
    email_data = process_email_raw_response(make_response())
    assert email_data["id"] == "<abc123@agencia.com>"


def test_process_email_raw_response_keeps_both_bodies():
    email_data = process_email_raw_response(make_response())
    assert email_data["body_plain"] == "Confirmación de su reserva."
    assert email_data["body_html"] == "<p>Confirmación de su reserva.</p>"


def test_process_email_raw_response_falls_back_to_the_gmail_id_without_message_id():
    response = make_response()
    del response["raw"]
    response["raw"] = base64.urlsafe_b64encode(
        b"Subject: no message id\r\nFrom: a@b.com\r\n\r\nBody"
    ).decode("ascii")
    email_data = process_email_raw_response(response)
    assert email_data["id"] == response["id"]
