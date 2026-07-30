import logging

from core.utils import parse_email_date


def test_parse_email_date_standard_formats():
    logger = logging.getLogger("test")

    # ISO format
    assert parse_email_date("2023-11-19T12:34:56Z", logger) == "2023-11-19"

    # RFC 2822-like
    assert parse_email_date("Wed, 29 Nov 2023 08:30:00 -0500", logger) == "2023-11-29"

    # With (UTC) suffix
    assert parse_email_date("2023-11-19 12:00:00 (UTC)", logger) == "2023-11-19"


def test_parse_email_date_invalid_returns_unknown(caplog):
    logger = logging.getLogger("test")
    # ensure caplog captures INFO messages from our logger
    caplog.set_level(logging.INFO)

    # invalid value
    result = parse_email_date("not a date", logger)
    assert result == "Unknown"
    # ensure we logged an info message about conversion error
    assert any("Error al convertir la fecha" in rec.getMessage() for rec in caplog.records)
