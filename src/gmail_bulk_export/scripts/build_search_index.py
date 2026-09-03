"""Builds the SQLite search index over the metadata already downloaded.

    python -m gmail_bulk_export.scripts.build_search_index                # automatic source
    python -m gmail_bulk_export.scripts.build_search_index --force        # rebuild
    python -m gmail_bulk_export.scripts.build_search_index --source output/emails.csv

Without `--source` it looks, in this order, for the consolidated Parquet, the
consolidated CSV and — failing both — the output directory itself, which works
but walks every per-day CSV and takes several minutes longer.

The index is a derivative: it can always be deleted and rebuilt. Querying it is
the job of `python -m gmail_bulk_export.scripts.search_emails`.
"""

import argparse
import sys
import time
from pathlib import Path

from gmail_bulk_export.config import output_dir
from gmail_bulk_export.core.search_index import (
    DEFAULT_INDEX_NAME,
    build_index,
    format_timestamp,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def default_source(base_path):
    """Parquet > CSV > output directory."""
    for name in ("emails_with_mailboxes.parquet", "emails_with_mailboxes.csv"):
        candidate = base_path / name
        if candidate.is_file():
            return candidate
    return base_path


def make_progress():
    """Traces progress without pulling in tqdm: these are four phases, not a tight loop."""
    phases = {
        "rows": "rows indexed",
        "indexes": "creating indexes",
        "fts": "building the full-text index (FTS5)",
    }
    state = {"phase": None}

    def progress(count, phase):
        if phase == "rows":
            print(f"  {count:>9,} {phases[phase]}", end="\r", flush=True)
        elif state["phase"] != phase:
            print(f"  {count:>9,} rows. {phases[phase]}...", flush=True)
        state["phase"] = phase

    return progress


def main():
    parser = argparse.ArgumentParser(
        prog="build_search_index",
        description="Build the SQLite search index over the exported metadata",
    )
    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--source", default=None, help="Parquet, consolidated CSV or directory")
    parser.add_argument("--db", default=None, help=f"Index path (defaults to {DEFAULT_INDEX_NAME})")
    parser.add_argument(
        "--labels-dir",
        default=None,
        help="Where to look for each mailbox's labels.csv (defaults to the output directory)",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=200_000, help="Rows per batch when reading the source"
    )
    parser.add_argument("--force", action="store_true", help="Overwrite the index if it exists")
    args = parser.parse_args()

    base_path = Path(args.output_dir or output_dir())
    source = Path(args.source) if args.source else default_source(base_path)
    db_path = Path(args.db) if args.db else base_path / DEFAULT_INDEX_NAME

    if not source.exists():
        print(f"Source does not exist: {source}", file=sys.stderr)
        return 1
    if db_path.exists() and not args.force:
        print(f"The index already exists: {db_path}\nUse --force to rebuild it.", file=sys.stderr)
        return 1

    print(f"Source: {source}")
    if source.is_dir():
        print("  (a directory: the per-day CSVs will be walked, this takes several minutes)")
    print(f"Index:  {db_path}\n")

    started = time.time()
    stats = build_index(
        source,
        db_path,
        labels_dir=args.labels_dir or base_path,
        chunk_size=args.chunk_size,
        progress=make_progress(),
    )
    elapsed = time.time() - started

    size_mb = db_path.stat().st_size / 1e6
    print(f"\nIndex built in {elapsed / 60:.1f} min ({size_mb:,.0f} MB)")
    print(f"  messages   {stats['rows']:>12,}")
    print(f"  threads    {stats['threads']:>12,}")
    print(f"  mailboxes  {stats['mailboxes']:>12,}")
    if stats["duplicates"]:
        print(f"  duplicates {stats['duplicates']:>12,} (dropped by (id, mailbox))")
    print(
        f"  range      {format_timestamp(stats['first'], fmt='%Y-%m-%d')}"
        f" -> {format_timestamp(stats['last'], fmt='%Y-%m-%d')}"
    )
    print("\nSearch:  python -m gmail_bulk_export.scripts.search_emails --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
