import csv
from pathlib import Path

from core.csv_handler import read_email_ids_and_mailbox_from_csv


def write_csv(path: Path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for r in rows:
            writer.writerow(r)


def test_read_email_ids_and_mailbox_from_csv(tmp_path):
    p = tmp_path / "emails.csv"
    headers = ["id", "mailbox", "date"]
    rows = [["m1", "inbox", "2025-11-19"], ["m2", "inbox", "2025-11-18"]]
    write_csv(p, headers, rows)

    result = read_email_ids_and_mailbox_from_csv(str(p))
    assert len(result) == 2
    assert result[0] == ("m1", "inbox", "2025-11-19")


def test_read_csv_missing_columns(tmp_path):
    p = tmp_path / "bad.csv"
    headers = ["not_id", "mailbox"]
    rows = [["x", "y"]]
    write_csv(p, headers, rows)

    try:
        read_email_ids_and_mailbox_from_csv(str(p))
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "CSV must contain 'id' and 'mailbox' columns" in str(e)
