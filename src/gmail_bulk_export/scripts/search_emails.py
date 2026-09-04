"""Searches the exported metadata: by date, subject, sender, label...

    python -m gmail_bulk_export.scripts.search_emails -q "quarterly report" --since 2023-01
    python -m gmail_bulk_export.scripts.search_emails --subject invoice --mailbox a@example.com
    python -m gmail_bulk_export.scripts.search_emails --domain acme.com --with-attachment \
        --format csv --out output/acme.csv
    python -m gmail_bulk_export.scripts.search_emails --label "Assigned/Jane" --breakdown year
    python -m gmail_bulk_export.scripts.search_emails --thread 18f2a1b3c4d5e6f7 --order date-asc
    python -m gmail_bulk_export.scripts.search_emails --around "2024-03-14 09:32" --window 6h
    python -m gmail_bulk_export.scripts.search_emails -q "quarterly report" --format detail -n 3

Needs the index from `python -m gmail_bulk_export.scripts.build_search_index`.
Text searches (`-q`, `--subject`, `--from`, `--to`) go through FTS5 and are
**accent- and case-insensitive**: "resume" finds "résumé". They match whole
words; `invoic*` searches by prefix and `--contains` by literal substring.

Repeatable filters (`--mailbox`, `--label`, `--domain`, ...) are *or* within a
field and *and* across fields.

`--around` takes the place of `--since`/`--until` when what you have is a
single moment with some slack around it — a different time zone, a clock a few
minutes off. It reads the same date formats as `--since`, padded by `--window`
(default 6h) on both sides; it cannot be combined with `--since`/`--until`.

`--format detail` prints one unabridged block per message instead of a table
row — the only format where the full `snippet` is worth reading rather than
truncated to fit a column. `--fields +col,-col` adds or removes columns from
whichever default applies (add `+` to keep everything else); a plain
comma-separated list still replaces it outright.

Remember that only metadata is downloaded: the searchable text is the subject
and the `snippet` (the first ~200 characters of the body), not the whole email.
"""

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

from gmail_bulk_export.config import output_dir
from gmail_bulk_export.core.search_index import (
    DEFAULT_FIELDS,
    DEFAULT_INDEX_NAME,
    DETAIL_FIELDS,
    FIELDS,
    ORDERS,
    Query,
    around_bounds,
    breakdown,
    count,
    format_timestamp,
    index_stats,
    open_index,
    parse_date_bound,
    parse_window,
    search,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SIZE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([KMG]?)B?$", re.IGNORECASE)
SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}


def parse_size(text):
    """`2M`, `500K`, `1024` -> bytes."""
    match = SIZE_RE.match(str(text).strip())
    if not match:
        raise argparse.ArgumentTypeError(f"Unrecognized size: {text!r} (e.g. 2M, 500K, 1024)")
    number, unit = match.groups()
    return int(float(number.replace(",", ".")) * SIZE_UNITS[unit.upper()])


def build_parser():
    parser = argparse.ArgumentParser(
        prog="search_emails",
        description="Search the Gmail metadata index",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1],
    )

    text = parser.add_argument_group("text (FTS5: accent-insensitive, by word)")
    text.add_argument("-q", "--text", help="Searches subject, snippet, sender and recipients")
    text.add_argument("--subject", help="Subject only")
    text.add_argument("--from", dest="sender", help="Sender only")
    text.add_argument("--to", dest="recipient", help="Recipients only")
    text.add_argument(
        "--contains", help="Literal substring in subject or snippet (accent-aware, slower)"
    )
    text.add_argument("--raw-query", help="Raw FTS5 expression, for those who know the syntax")

    filters = parser.add_argument_group("filters")
    filters.add_argument("--since", help="From: 2023, 2023-05, 2023-05-17, 17/05/2023")
    filters.add_argument("--until", help="To, inclusive (--until 2023-05 reaches 31 May)")
    filters.add_argument("--around", help="A moment or period, padded by --window either side")
    filters.add_argument(
        "--window",
        type=parse_window,
        default="6h",
        help="Padding for --around: 90s, 45m, 6h, 2d (default 6h)",
    )
    filters.add_argument(
        "--mailbox", action="append", default=[], help="Mailbox (repeatable, accepts *)"
    )
    filters.add_argument(
        "--delivered-to", action="append", default=[], help="Alias in the Delivered-To header"
    )
    filters.add_argument("--domain", action="append", default=[], help="Sender domain")
    filters.add_argument("--label", action="append", default=[], help="Label (substring)")
    filters.add_argument("--id", action="append", default=[], dest="ids", help="Message id")
    filters.add_argument("--thread", help="Every message in a thread")
    filters.add_argument("--sent", action="store_true", help="Sent only (SENT label)")
    filters.add_argument("--received", action="store_true", help="Received only")
    filters.add_argument(
        "--with-attachment",
        action="store_true",
        help="multipart/mixed only (an attachment signal)",
    )
    filters.add_argument("--min-size", type=parse_size, help="Minimum size (2M, 500K)")
    filters.add_argument("--max-size", type=parse_size, help="Maximum size")

    out = parser.add_argument_group("output")
    out.add_argument(
        "-n", "--limit", type=int, default=None, help="Max results (50; all with --out)"
    )
    out.add_argument("--offset", type=int, default=0, help="Skip the first N")
    out.add_argument(
        "--order", choices=sorted(ORDERS), default="date", help="Order (date by default)"
    )
    out.add_argument(
        "--fields",
        help=(
            "Comma-separated columns, or +col/-col to add/remove from the current "
            f"default. Available: {', '.join(FIELDS)}"
        ),
    )
    out.add_argument(
        "--format",
        choices=["table", "csv", "json", "jsonl", "ids", "detail"],
        default=None,
        help="table by default; with --out it is inferred from the extension",
    )
    out.add_argument("--out", help="Write to a file instead of the console")
    out.add_argument(
        "--for-payloads",
        action="store_true",
        help="id,mailbox,date CSV ready for 'gmail-bulk-export payloads --csv-file'",
    )
    out.add_argument("--count", action="store_true", help="Just the number of hits")
    out.add_argument(
        "--breakdown",
        choices=["mailbox", "year", "month", "domain", "deliveredTo"],
        help="Grouped counts instead of a listing",
    )
    out.add_argument("--utc", action="store_true", help="Dates in UTC (local time by default)")
    out.add_argument("--info", action="store_true", help="Index summary, then exit")

    parser.add_argument("--output-dir", default=None, help="Output directory")
    parser.add_argument("--db", default=None, help="Index path")
    return parser


# What `core.csv_handler.read_email_ids_and_mailbox_from_csv` expects to read.
# `date` carries the folder day, not the message time: it is what the payload
# downloader uses to tell whether a body is already on disk and can be skipped.
PAYLOAD_FIELDS = ["id", "mailbox", "day"]


def as_payload_rows(rows):
    return [{"id": row["id"], "mailbox": row["mailbox"], "date": row["day"]} for row in rows]


# `--out hits.csv` with no `--format` used to write a space-padded table into a
# .csv file. Infer it from the name instead.
FORMAT_BY_SUFFIX = {
    ".csv": "csv",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".txt": "table",
}


def resolve_format(args):
    if args.format:
        return args.format
    if args.out:
        return FORMAT_BY_SUFFIX.get(Path(args.out).suffix.lower(), "csv")
    return "table"


def apply_field_overrides(base_fields, spec):
    """`--fields subject,snippet` replaces the field list; `--fields +snippet,-from`
    adds/removes from `base_fields` instead — handy when the default is almost
    right and typing it out in full is not worth it.
    """
    tokens = [token.strip() for token in spec.split(",") if token.strip()]
    if not any(token.startswith(("+", "-")) for token in tokens):
        return tokens
    fields = list(base_fields)
    for token in tokens:
        if token.startswith("-"):
            name = token[1:]
            if name in fields:
                fields.remove(name)
        else:
            name = token[1:] if token.startswith("+") else token
            if name not in fields:
                fields.append(name)
    return fields


def build_query(args):
    if args.around and (args.since or args.until):
        raise ValueError("--around cannot be combined with --since/--until")
    if args.around:
        since, until = around_bounds(args.around, args.window, utc=args.utc)
    else:
        since = parse_date_bound(args.since, utc=args.utc) if args.since else None
        until = parse_date_bound(args.until, end=True, utc=args.utc) if args.until else None
    return Query(
        text=args.text,
        subject=args.subject,
        sender=args.sender,
        recipient=args.recipient,
        raw_query=args.raw_query,
        contains=args.contains,
        since=since,
        until=until,
        mailboxes=args.mailbox,
        delivered_to=args.delivered_to,
        domains=args.domain,
        labels=args.label,
        ids=args.ids,
        thread=args.thread,
        direction="sent" if args.sent else "received" if args.received else None,
        with_attachment=args.with_attachment,
        min_size=args.min_size,
        max_size=args.max_size,
    )


def render_rows(rows, utc):
    """Formats dates and flattens multi-line text, leaving everything else alone."""
    for row in rows:
        if "date" in row:
            row["date"] = format_timestamp(row["date"], utc=utc)
        for key, value in row.items():
            if isinstance(value, str) and "\n" in value:
                row[key] = " ".join(value.split())
    return rows


def print_table(rows, stream):
    if not rows:
        return
    fields = list(rows[0])
    widths = {name: max(len(name), *(len(str(row[name] or "")) for row in rows)) for name in fields}

    # Spend the overflow on the widest column — the subject or the snippet —
    # rather than on the dates or the mailbox.
    available = max(shutil.get_terminal_size((160, 24)).columns - 2, 60)
    while sum(widths.values()) + 2 * (len(fields) - 1) > available:
        widest = max(widths, key=lambda name: widths[name])
        if widths[widest] <= 12:
            break
        widths[widest] -= 1

    def line(values):
        cells = []
        for name in fields:
            text = str(values[name] if values[name] is not None else "")
            width = widths[name]
            cells.append(text[: width - 1] + "…" if len(text) > width else text.ljust(width))
        return "  ".join(cells).rstrip()

    print(line({name: name for name in fields}), file=stream)
    print("  ".join("-" * widths[name] for name in fields), file=stream)
    for row in rows:
        print(line(row), file=stream)


def print_detail(rows, stream):
    """One `key: value` block per message, unabridged — the snippet included."""
    label_width = max(len(name) for row in rows for name in row)
    for index, row in enumerate(rows):
        if index:
            print(file=stream)
        for name, value in row.items():
            print(f"{name.rjust(label_width)}: {value if value is not None else ''}", file=stream)


def write_output(rows, args, stream):
    if args.format == "ids":
        # Whichever single column `--fields` asked for, not necessarily `id`.
        for row in rows:
            print(next(iter(row.values()), ""), file=stream)
    elif args.format == "csv":
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    elif args.format == "json":
        json.dump(rows, stream, ensure_ascii=False, indent=2)
        print(file=stream)
    elif args.format == "jsonl":
        for row in rows:
            print(json.dumps(row, ensure_ascii=False), file=stream)
    elif args.format == "detail":
        print_detail(rows, stream)
    else:
        print_table(rows, stream)


def print_info(connection, utc):
    stats = index_stats(connection)
    print(f"Index built on {stats.get('built_at', '?')} UTC from {stats.get('source', '?')}")
    print(f"  messages  {stats['rows']:>12,}")
    print(f"  threads   {stats['threads']:>12,}")
    print(f"  mailboxes {stats['mailboxes']:>12,}")
    print(
        f"  range     {format_timestamp(stats['first'], utc=utc, fmt='%Y-%m-%d')}"
        f" -> {format_timestamp(stats['last'], utc=utc, fmt='%Y-%m-%d')}"
    )


def main():
    args = build_parser().parse_args()
    args.format = resolve_format(args)
    if args.limit is None:
        # A console listing gets looked at; a file gets mined afterwards.
        # Truncating an export to 50 rows nobody asked to truncate is a trap.
        args.limit = None if args.out else 50

    base_path = Path(args.output_dir or output_dir())
    db_path = Path(args.db) if args.db else base_path / DEFAULT_INDEX_NAME
    try:
        connection = open_index(db_path)
    except FileNotFoundError:
        print(
            f"No index at {db_path}.\n"
            "Build it with: python -m gmail_bulk_export.scripts.build_search_index",
            file=sys.stderr,
        )
        return 1

    try:
        return run(args, connection)
    finally:
        connection.close()


def run(args, connection):
    if args.info:
        print_info(connection, args.utc)
        return 0

    try:
        query = build_query(args)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.count:
        print(f"{count(connection, query):,}")
        return 0

    if args.breakdown:
        total = 0
        for bucket, number in breakdown(connection, query, args.breakdown):
            print(f"  {str(bucket or '(empty)'):40s} {number:>9,}")
            total += number
        print(f"  {'TOTAL':40s} {total:>9,}")
        return 0

    if args.for_payloads:
        # Exactly what the body downloader knows how to read.
        fields, args.format = PAYLOAD_FIELDS, "csv"
    else:
        if args.format == "detail":
            # One block per message, unabridged — the only format where the
            # full snippet is worth reading rather than truncated in a column.
            base_fields = DETAIL_FIELDS
        elif args.format in ("csv", "json", "jsonl"):
            # An export is read later, not glanced at: it deserves the columns
            # that let it be joined with the rest of the analysis.
            base_fields = [
                "date",
                "mailbox",
                "id",
                "threadId",
                "from",
                "to",
                "subject",
                "labels",
                "snippet",
            ]
        else:
            base_fields = DEFAULT_FIELDS
        fields = apply_field_overrides(base_fields, args.fields) if args.fields else base_fields

    if args.format == "ids":
        # One column per line, to pipe into another command. If `--fields` was
        # given the first one wins: `--fields threadId --format ids` yields
        # thread ids, which is exactly what `--thread` wants.
        fields = [fields[0]] if args.fields else ["id"]

    try:
        rows = search(
            connection, query, fields=fields, order=args.order, limit=args.limit, offset=args.offset
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if not rows:
        print("No results.", file=sys.stderr)
        return 0

    rows = as_payload_rows(rows) if args.for_payloads else render_rows(rows, args.utc)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            write_output(rows, args, handle)
        total = count(connection, query)
        print(f"{len(rows):,} rows written to {path} (of {total:,} hits)", file=sys.stderr)
    else:
        write_output(rows, args, sys.stdout)
        sys.stdout.flush()  # the notice goes to stderr: without this it lands first
        total = count(connection, query)
        if total > len(rows) + args.offset:
            print(
                f"\n{len(rows):,} of {total:,} hits. Use -n / --offset to see more.",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
