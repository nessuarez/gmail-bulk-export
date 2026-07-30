"""Tests for the metadata consolidation step.

No test previously covered this module. It walks disk, so every test builds a
fake `output/<mailbox>/<date>/<date>.csv` tree under `tmp_path` rather than
touching the real `output/` directory.
"""

import csv
import os

import pandas as pd
import pytest

from core.load_metadatas import find_day_csvs, load_metadatas, main, to_parquet_typed

FIELDNAMES = ["id", "threadId", "labelIds", "internalDate", "subject"]


def write_day_csv(base_path, mailbox, date, rows):
    day_dir = base_path / mailbox / date
    day_dir.mkdir(parents=True, exist_ok=True)
    with open(day_dir / f"{date}.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def row(msg_id, label_ids="['INBOX']", internal_date="1700000000000"):
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": label_ids,
        "internalDate": internal_date,
        "subject": "s",
    }


def test_find_day_csvs_ignores_batch_temporaries(tmp_path):
    """A plain `*.csv` glob would double-count `<date>_<uuid>.csv` leftovers."""
    write_day_csv(tmp_path, "user@example.com", "2024-01-15", [row("m1")])
    stray = tmp_path / "user@example.com" / "2024-01-15" / "2024-01-15_abc123.csv"
    stray.write_text("id\nm2\n", encoding="utf-8")

    found = find_day_csvs(tmp_path)

    assert [f.name for f in found] == ["2024-01-15.csv"]


def test_find_day_csvs_can_restrict_to_a_mailbox_roster(tmp_path):
    write_day_csv(tmp_path, "keep@example.com", "2024-01-15", [row("m1")])
    write_day_csv(tmp_path, "drop@example.com", "2024-01-15", [row("m2")])

    found = find_day_csvs(tmp_path, mailboxes=["keep@example.com"])

    assert len(found) == 1
    assert found[0].parts[-3] == "keep@example.com"


def test_load_metadatas_adds_mailbox_and_file_path_columns(tmp_path):
    write_day_csv(tmp_path, "user@example.com", "2024-01-15", [row("m1")])

    df = load_metadatas(tmp_path)

    assert df.loc[0, "mailbox"] == "user@example.com"
    assert df.loc[0, "file_path"].endswith("2024-01-15.csv")


def test_load_metadatas_concatenates_multiple_mailboxes_and_days(tmp_path):
    write_day_csv(tmp_path, "a@example.com", "2024-01-15", [row("m1"), row("m2")])
    write_day_csv(tmp_path, "b@example.com", "2024-02-01", [row("m3")])

    df = load_metadatas(tmp_path)

    assert len(df) == 3
    assert set(df["mailbox"]) == {"a@example.com", "b@example.com"}


def test_load_metadatas_raises_when_nothing_found(tmp_path):
    with pytest.raises(SystemExit):
        load_metadatas(tmp_path)


def test_load_metadatas_skips_empty_files(tmp_path):
    day_dir = tmp_path / "user@example.com" / "2024-01-15"
    day_dir.mkdir(parents=True)
    with open(day_dir / "2024-01-15.csv", "w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=FIELDNAMES).writeheader()  # header only
    write_day_csv(tmp_path, "user@example.com", "2024-02-01", [row("m1")])

    df = load_metadatas(tmp_path)

    assert len(df) == 1


def test_to_parquet_typed_converts_label_ids_and_internal_date(tmp_path):
    """The CSV keeps labelIds/internalDate as text; Parquet should not."""
    df = pd.DataFrame([row("m1", label_ids="['INBOX', 'SENT']", internal_date="1700000000000")])
    out = tmp_path / "typed.parquet"

    to_parquet_typed(df, str(out))
    read_back = pd.read_parquet(out)

    assert list(read_back.loc[0, "labelIds"]) == ["INBOX", "SENT"]
    assert pd.api.types.is_datetime64_any_dtype(read_back["internalDate"])


def test_to_parquet_typed_does_not_mutate_the_original_dataframe(tmp_path):
    """The CSV write happens from the same dataframe; typing it must not affect that write."""
    df = pd.DataFrame([row("m1")])
    original_label_ids = df.loc[0, "labelIds"]

    to_parquet_typed(df, str(tmp_path / "typed.parquet"))

    assert df.loc[0, "labelIds"] == original_label_ids
    assert isinstance(df.loc[0, "internalDate"], str)


def test_main_also_parquet_writes_alongside_an_unchanged_csv(tmp_path, monkeypatch):
    write_day_csv(tmp_path, "user@example.com", "2024-01-15", [row("m1")])

    monkeypatch.setattr(
        "sys.argv",
        ["load_metadatas", "--output-dir", str(tmp_path), "--also-parquet"],
    )
    exit_code = main()

    assert exit_code == 0
    csv_path = tmp_path / "emails_with_mailboxes.csv"
    parquet_path = tmp_path / "emails_with_mailboxes.parquet"
    assert csv_path.exists()
    assert parquet_path.exists()

    # The CSV keeps the raw string form; only the Parquet copy is typed.
    csv_df = pd.read_csv(csv_path, dtype=str)
    assert csv_df.loc[0, "labelIds"] == "['INBOX']"

    parquet_df = pd.read_parquet(parquet_path)
    assert list(parquet_df.loc[0, "labelIds"]) == ["INBOX"]


def test_main_without_also_parquet_does_not_write_one(tmp_path, monkeypatch):
    write_day_csv(tmp_path, "user@example.com", "2024-01-15", [row("m1")])

    monkeypatch.setattr("sys.argv", ["load_metadatas", "--output-dir", str(tmp_path)])
    main()

    assert not os.path.exists(tmp_path / "emails_with_mailboxes.parquet")
