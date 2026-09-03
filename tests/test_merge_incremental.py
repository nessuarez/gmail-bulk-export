"""Regression tests for the incremental merge.

Before this behaviour existed, `merge_csv_files` opened the day's CSV with
mode="w+" and excluded it from its own merge, so any second write to a date
destroyed the first one. That silently lost rows whenever a date spanned two
API pages, and whenever an overlapping range was re-downloaded.
"""

import csv
import threading
from pathlib import Path

from gmail_bulk_export.core.save_to_csv import merge_csv_files

DATE = "2024-03-15"
FIELDNAMES = ["id", "subject", "date"]


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def row(msg_id, subject="s"):
    return {"id": msg_id, "subject": subject, "date": DATE}


def test_existing_day_csv_is_preserved(tmp_path):
    """A second batch must add to the day, not replace it."""
    day_dir = tmp_path / DATE
    write_csv(day_dir / f"{DATE}.csv", [row("m1"), row("m2")])
    write_csv(day_dir / f"{DATE}_batch.csv", [row("m3")])

    merged = read_csv(merge_csv_files(str(day_dir), DATE))

    assert {r["id"] for r in merged} == {"m1", "m2", "m3"}


def test_duplicate_ids_are_deduplicated(tmp_path):
    """Overlapping chunks re-fetch boundary days; duplicates must collapse."""
    day_dir = tmp_path / DATE
    write_csv(day_dir / f"{DATE}.csv", [row("m1", "old"), row("m2", "old")])
    write_csv(day_dir / f"{DATE}_batch.csv", [row("m2", "new"), row("m3", "new")])

    merged = read_csv(merge_csv_files(str(day_dir), DATE))

    assert len(merged) == 3
    assert {r["id"] for r in merged} == {"m1", "m2", "m3"}
    # Last write wins, so the freshly downloaded copy survives.
    assert next(r for r in merged if r["id"] == "m2")["subject"] == "new"


def test_repeated_merges_are_idempotent(tmp_path):
    """Re-running a completed range must not change the result."""
    day_dir = tmp_path / DATE
    write_csv(day_dir / f"{DATE}_a.csv", [row("m1"), row("m2")])
    first = read_csv(merge_csv_files(str(day_dir), DATE))

    write_csv(day_dir / f"{DATE}_b.csv", [row("m1"), row("m2")])
    second = read_csv(merge_csv_files(str(day_dir), DATE))

    assert len(first) == 2
    assert first == second


def test_temp_files_removed_only_after_merge(tmp_path):
    day_dir = tmp_path / DATE
    temp = day_dir / f"{DATE}_batch.csv"
    write_csv(temp, [row("m1")])

    merged_path = Path(merge_csv_files(str(day_dir), DATE))

    assert merged_path.exists()
    assert not temp.exists()
    assert not (day_dir / f"{DATE}.csv.tmp").exists()


def test_concurrent_merges_do_not_lose_rows(tmp_path):
    """Five worker threads landing on the same date is the normal case."""
    day_dir = tmp_path / DATE
    day_dir.mkdir(parents=True)

    def worker(index):
        write_csv(day_dir / f"{DATE}_t{index}.csv", [row(f"m{index}")])
        merge_csv_files(str(day_dir), DATE)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    merged = read_csv(day_dir / f"{DATE}.csv")
    assert {r["id"] for r in merged} == {f"m{i}" for i in range(20)}


def test_new_column_survives_an_older_day_csv(tmp_path):
    """Adding a field to the schema must not be swallowed by the existing file.

    The pre-existing {date}.csv is read first. Taking the header from that first
    source alone would drop `snippet` from every row of the current run.
    """
    day_dir = tmp_path / DATE
    write_csv(day_dir / f"{DATE}.csv", [row("m1")])  # old 3-column schema

    with open(day_dir / f"{DATE}_batch.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES + ["snippet"])
        writer.writeheader()
        writer.writerow({**row("m2"), "snippet": "Estamos procesando tu petición"})

    merged_path = merge_csv_files(str(day_dir), DATE)
    merged = read_csv(merged_path)

    with open(merged_path, newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert "snippet" in header

    by_id = {r["id"]: r for r in merged}
    assert by_id["m2"]["snippet"] == "Estamos procesando tu petición"
    # Rows written before the column existed simply have it empty.
    assert by_id["m1"]["snippet"] == ""


def test_older_rows_keep_columns_a_newer_batch_lacks(tmp_path):
    """The union must work in both directions, not just append."""
    day_dir = tmp_path / DATE
    day_dir.mkdir(parents=True)
    with open(day_dir / f"{DATE}.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES + ["snippet"])
        writer.writeheader()
        writer.writerow({**row("m1"), "snippet": "viejo"})

    write_csv(day_dir / f"{DATE}_batch.csv", [row("m2")])  # no snippet column

    merged = {r["id"]: r for r in read_csv(merge_csv_files(str(day_dir), DATE))}

    assert merged["m1"]["snippet"] == "viejo"
    assert merged["m2"]["snippet"] == ""


def test_missing_sources_is_a_noop(tmp_path):
    day_dir = tmp_path / DATE
    day_dir.mkdir(parents=True)

    merged_path = Path(merge_csv_files(str(day_dir), DATE))

    assert not merged_path.exists()
