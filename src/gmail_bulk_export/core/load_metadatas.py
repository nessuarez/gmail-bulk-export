"""Consolidates the per-day metadata CSVs into a single dataframe.

Walks `OUTPUT_DIR/<mailbox>/<YYYY-MM-DD>/<YYYY-MM-DD>.csv`, adds the `mailbox`
and `file_path` columns the EDA notebook expects, and writes
`OUTPUT_DIR/emails_with_mailboxes.csv`.

    python -m gmail_bulk_export.core.load_metadatas
    python -m gmail_bulk_export.core.load_metadatas --mailboxes-file mailboxes.txt
    python -m gmail_bulk_export.core.load_metadatas --also-parquet

Run it as a module from the repository root; `python core/load_metadatas.py`
fails on `from config import output_dir`.

`--also-parquet` writes a second, typed copy next to the CSV
(`emails_with_mailboxes.parquet`): `labelIds` becomes a real list column and
`internalDate` a real datetime column, instead of the CSV's string repr and
epoch-milliseconds text. Useful for DuckDB/pandas over large exports without
re-parsing on every load. The CSV itself is unchanged either way.
"""

import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from gmail_bulk_export.config import output_dir
from gmail_bulk_export.data_transforms import transform_date_columns, transform_list_columns

# Only the consolidated day files. A plain `*.csv` glob would also pick up
# `<date>_<uuid>.csv` batch temporaries left behind by an interrupted run and
# double-count their rows.
DAY_CSV_GLOB = "*/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/*.csv"
DAY_CSV_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.csv$")


def find_day_csvs(base_path: Path, mailboxes=None):
    """Every consolidated day CSV, optionally restricted to a mailbox roster."""
    files = [path for path in base_path.glob(DAY_CSV_GLOB) if DAY_CSV_NAME.match(path.name)]
    if mailboxes:
        allowed = set(mailboxes)
        files = [path for path in files if path.parts[-3] in allowed]
    return sorted(files)


def load_metadatas(base_path: Path, mailboxes=None) -> pd.DataFrame:
    """Loads and concatenates every day CSV into one dataframe."""
    csv_files = find_day_csvs(base_path, mailboxes)
    if not csv_files:
        raise SystemExit(f"No metadata CSVs found in {base_path}")

    frames = []
    for file in tqdm(csv_files, desc="Loading CSV files"):
        mailbox = file.parts[-3]  # <output>/<mailbox>/<date>/<date>.csv
        try:
            df = pd.read_csv(file, dtype=str)
        except (pd.errors.ParserError, OSError) as exc:
            print(f"  warning: could not read {file}: {exc}", file=sys.stderr)
            continue
        if df.empty:
            continue
        df["mailbox"] = mailbox
        df["file_path"] = str(file)
        frames.append(df)

    if not frames:
        raise SystemExit("Every CSV was empty or unreadable.")

    return pd.concat(frames, ignore_index=True)


def summarize(df: pd.DataFrame) -> None:
    """Prints per-mailbox and per-year counts, the quickest way to spot a gap."""
    year = pd.to_datetime(
        pd.to_numeric(df["internalDate"], errors="coerce"), unit="ms", errors="coerce"
    ).dt.year

    print("\nRows per mailbox:")
    for mailbox, count in df["mailbox"].value_counts().sort_index().items():
        print(f"  {mailbox:30s} {count:>9,}")

    print("\nRows per year:")
    counts = year.value_counts().sort_index()
    for value, count in counts.items():
        if pd.notna(value):
            print(f"  {int(value):<30d} {count:>9,}")
    missing = int(year.isna().sum())
    if missing:
        print(f"  {'(no date)':30s} {missing:>9,}")


def to_parquet_typed(df: pd.DataFrame, path: str) -> None:
    """Writes a typed Parquet copy alongside the CSV.

    The CSV keeps its raw string columns (`labelIds` as a Python-list repr,
    `internalDate` as text) for backward compatibility with anything already
    parsing it. Parquet readers (DuckDB, pandas, Polars) benefit from real
    types instead, so this applies the same idempotent transforms the EDA
    notebook already relies on before writing: `labelIds` becomes an actual
    list column, `internalDate` an actual datetime column.
    """
    typed = transform_date_columns(df.copy())
    typed = transform_list_columns(typed, ["labelIds"])
    typed.to_parquet(path, engine="pyarrow", index=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="load_metadatas", description="Consolidates the metadata CSVs into one"
    )
    parser.add_argument("--output-dir", default=output_dir(), help="Output directory")
    parser.add_argument("--mailboxes-file", help="Restrict to the mailboxes listed in this file")
    parser.add_argument(
        "--out", default=None, help="Path of the consolidated CSV (default: inside output-dir)"
    )
    parser.add_argument(
        "--also-parquet",
        action="store_true",
        help="Alongside the CSV, write a typed .parquet copy (same base name)",
    )
    args = parser.parse_args()

    mailboxes = None
    if args.mailboxes_file:
        with open(args.mailboxes_file, encoding="utf-8") as handle:
            mailboxes = [
                line.strip() for line in handle if line.strip() and not line.startswith("#")
            ]

    base_path = Path(args.output_dir)
    df = load_metadatas(base_path, mailboxes)

    before = len(df)
    # Overlapping month chunks re-fetch boundary days, and merge_csv_files
    # already de-duplicates within a day. This is the cross-file safety net.
    df = df.drop_duplicates(subset=["id", "mailbox"], keep="last").reset_index(drop=True)
    if before != len(df):
        print(f"Duplicates removed: {before - len(df):,}")

    consolidated_file = args.out or os.path.join(args.output_dir, "emails_with_mailboxes.csv")
    df.to_csv(consolidated_file, index=False)

    print(f"\nConsolidated file saved to: {consolidated_file}")
    print(f"Total rows: {len(df):,}")

    if args.also_parquet:
        parquet_file = os.path.splitext(consolidated_file)[0] + ".parquet"
        to_parquet_typed(df, parquet_file)
        print(f"Typed copy saved to: {parquet_file}")

    summarize(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
