import csv
from pathlib import Path

from gmail_bulk_export.core.save_to_csv import save_emails_to_csv, save_labels_csv


def make_email_dict(id_val):
    return {
        "id": id_val,
        "threadId": "t1",
        "labelIds": "[]",
        "sizeEstimate": "123",
        "historyId": "h1",
        "internalDate": "1234567890",
        "deliveredTo": "user@example.com",
        "subject": "Test",
        "from": "sender@example.com",
        "to": "user@example.com",
        "cc": "",
        "bcc": "",
        "date": "2025-11-19",
        "contentType": "text/plain",
    }


def test_save_emails_to_csv_creates_merged_file(tmp_path):
    username = "user@example.com"
    date = "2025-11-19"
    emails_by_date = {date: [make_email_dict("m1"), make_email_dict("m2")]}

    out_dir = str(tmp_path / "output")
    save_emails_to_csv(username, emails_by_date, out_dir)

    merged_file = Path(out_dir) / username / date / f"{date}.csv"
    assert merged_file.exists()

    with open(merged_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 2
    assert any(r["id"] == "m1" for r in rows)


def test_save_labels_csv_writes_and_handles_empty(tmp_path):
    dir_path = tmp_path / "labels"
    dir_path.mkdir()

    labels = [
        {
            "id": "L1",
            "name": "INBOX",
            "type": "system",
            "messageListVisibility": "show",
            "labelListVisibility": "labelShow",
            "messagesTotal": 10,
            "messagesUnread": 1,
            "threadsTotal": 5,
            "threadsUnread": 0,
            "color": {"backgroundColor": "#ffffff", "textColor": "#000000"},
        },
    ]

    file_path = dir_path / "labels.csv"
    save_labels_csv(labels, str(dir_path), file_name="labels")
    assert file_path.exists()

    with open(file_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["id"] == "L1"

    # empty labels should not create a file
    dir2 = tmp_path / "labels2"
    dir2.mkdir()
    save_labels_csv([], str(dir2), file_name="labels")
    assert not (dir2 / "labels.csv").exists()
