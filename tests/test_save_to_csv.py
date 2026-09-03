import csv
from pathlib import Path

from gmail_bulk_export.core.save_to_csv import merge_csv_files


def write_temp_csv(path: Path, date_prefix: str, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "subject"])
        writer.writeheader()
        writer.writerows(rows)


def test_merge_csv_files_combines_and_removes(tmp_path):
    date = "20250101"
    dir_path = tmp_path / "output" / "user@example.com" / date
    dir_path.mkdir(parents=True)

    # create two temp files matching the pattern
    file1 = dir_path / f"{date}_part1.csv"
    file2 = dir_path / f"{date}_part2.csv"

    rows1 = [{"id": "m1", "subject": "First"}, {"id": "m2", "subject": "Second"}]
    rows2 = [{"id": "m3", "subject": "Third"}]

    write_temp_csv(file1, date, rows1)
    write_temp_csv(file2, date, rows2)

    merged_path = merge_csv_files(str(dir_path), date)

    merged_file = Path(merged_path)
    assert merged_file.exists()

    # read merged content
    with open(merged_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        merged_rows = list(reader)

    assert len(merged_rows) == 3
    assert any(r["id"] == "m1" for r in merged_rows)
    assert any(r["id"] == "m3" for r in merged_rows)

    # temp files should be removed
    assert not file1.exists()
    assert not file2.exists()
