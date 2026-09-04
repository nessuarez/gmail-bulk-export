"""A SQLite search index over the metadata already downloaded.

Pure disk functions: nothing here touches the Gmail API or `argparse`. The two
command-line frontends live in `scripts/build_search_index.py` (build) and
`scripts/search_emails.py` (query).

Why an index rather than plain pandas: a consolidated export of ~970,000
messages is 657 MB of CSV. Loading it costs ~19 s and 0.76 GB **every time**,
and even then you cannot filter by date without first converting
`internalDate` (epoch milliseconds stored as text), nor by label without
parsing the Python-list repr in `labelIds`. The index pays that once.

What it stores:

* One row per `(id, mailbox)` in `emails`, already typed: `internal_date` as an
  epoch-millisecond integer, the size as an integer, and the derived columns
  the CSV does not carry — `from_domain`, `has_attachment`, `is_sent`.
* Labels resolved from `Label_1234` to their name using **each mailbox's own**
  `labels.csv`: user label ids are per mailbox, the `Label_175` of one is not
  the `Label_175` of another. They go into `emails.labels` for display and into
  the `email_labels` table so they can be filtered with an index.
* An external-content FTS5 table over `subject`, `snippet`, `sender` and
  `recipients`, tokenized with `unicode61 remove_diacritics 2`: searching for
  "resume" finds "résumé".

Time zones: `internal_date` and `sent_at` are **UTC**, which is what the API
returns. Converting to local time is the caller's business — `search_emails.py`
does it by default — so that the index file does not depend on the machine that
built it.
"""

import calendar
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# pandas is imported **inside** the build functions, not here: importing it
# costs 0.7 s, and the query path — the one run twenty times in a row from a
# terminal — has no use for it. With the import at module level every search
# took 1.1 s instead of 0.4 s.

DEFAULT_INDEX_NAME = "search_index.sqlite3"

# Consolidated-CSV columns, with the name each one takes in SQL. `from` and
# `to` are reserved words, hence `sender`/`recipients`.
SOURCE_COLUMNS = {
    "id": "id",
    "threadId": "thread_id",
    "mailbox": "mailbox",
    "internalDate": "internal_date",
    "subject": "subject",
    "from": "sender",
    "to": "recipients",
    "cc": "cc",
    "deliveredTo": "delivered_to",
    "snippet": "snippet",
    "sizeEstimate": "size_estimate",
    "contentType": "content_type",
    "labelIds": "labels",
    "file_path": "source_file",
}

# The order in which `_rows` unpacks each normalized row.
ROW_COLUMNS = [
    "id",
    "thread_id",
    "mailbox",
    "internal_date",
    "subject",
    "sender",
    "recipients",
    "cc",
    "delivered_to",
    "from_domain",
    "snippet",
    "labels",
    "size_estimate",
    "content_type",
    "has_attachment",
    "source_file",
]

# Column order for the INSERT (adds the two values `_rows` computes per row).
INSERT_COLUMNS = [
    "id",
    "thread_id",
    "mailbox",
    "internal_date",
    "sent_at",
    "subject",
    "sender",
    "recipients",
    "cc",
    "delivered_to",
    "from_domain",
    "snippet",
    "labels",
    "size_estimate",
    "content_type",
    "has_attachment",
    "is_sent",
    "source_file",
]

SCHEMA = """
CREATE TABLE emails (
    rowid          INTEGER PRIMARY KEY,
    id             TEXT NOT NULL,
    thread_id      TEXT,
    mailbox        TEXT NOT NULL,
    internal_date  INTEGER,
    sent_at        TEXT,
    subject        TEXT,
    sender         TEXT,
    recipients     TEXT,
    cc             TEXT,
    delivered_to   TEXT,
    from_domain    TEXT,
    snippet        TEXT,
    labels         TEXT,
    size_estimate  INTEGER,
    content_type   TEXT,
    has_attachment INTEGER,
    is_sent        INTEGER,
    source_file    TEXT
);

CREATE TABLE email_labels (
    email_rowid INTEGER NOT NULL,
    label       TEXT NOT NULL
);

CREATE TABLE index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Created **after** inserting: keeping them live while a million rows land
# multiplies the build time.
INDEXES = """
CREATE UNIQUE INDEX emails_id_mailbox ON emails(id, mailbox);
CREATE INDEX emails_date        ON emails(internal_date);
CREATE INDEX emails_mailbox     ON emails(mailbox, internal_date);
CREATE INDEX emails_thread      ON emails(thread_id);
CREATE INDEX emails_domain      ON emails(from_domain);
CREATE INDEX emails_delivered   ON emails(delivered_to);
CREATE INDEX email_labels_label ON email_labels(label, email_rowid);
CREATE INDEX email_labels_rowid ON email_labels(email_rowid);
"""

# External-content FTS: it does not duplicate the text, it reads it back from
# `emails` by rowid. That is why its columns must be named exactly as the
# columns of `emails`.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, snippet, sender, recipients,
    content='emails',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
"""

FTS_POPULATE = """
INSERT INTO emails_fts(rowid, subject, snippet, sender, recipients)
SELECT rowid, subject, snippet, sender, recipients FROM emails;
"""

DOMAIN_RE = re.compile(r"@([A-Za-z0-9.\-]+)")


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def load_label_names(labels_dir, mailboxes=None):
    """`{mailbox: {label_id: name}}` read from the `labels.csv` files.

    User label ids are **per mailbox**, so the resolution cannot be global. A
    mailbox with no `labels.csv` of its own falls back to the one at the root of
    `output/`, which at least covers the system labels (INBOX, SENT, ...) whose
    ids are the same in every account.
    """
    labels_dir = Path(labels_dir)
    fallback = {}
    root_csv = labels_dir / "labels.csv"
    if root_csv.is_file():
        fallback = read_label_csv(root_csv)

    names = {"": fallback}
    for path in sorted(labels_dir.glob("*/labels.csv")):
        mailbox = path.parts[-2]
        if mailboxes and mailbox not in mailboxes:
            continue
        merged = dict(fallback)
        merged.update(read_label_csv(path))
        names[mailbox] = merged
    return names


def read_label_csv(path):
    """`{id: name}` from a `labels.csv`; an empty dict if it cannot be read."""
    import pandas as pd

    try:
        frame = pd.read_csv(path, dtype=str)
    except (pd.errors.ParserError, OSError, UnicodeDecodeError):
        return {}
    if "id" not in frame.columns or "name" not in frame.columns:
        return {}
    frame = frame.dropna(subset=["id"])
    return dict(zip(frame["id"], frame["name"].fillna(frame["id"])))


def iter_source_frames(source, chunk_size=200_000):
    """Slices the source into DataFrames: Parquet, consolidated CSV or directory."""
    source = Path(source)

    if source.is_dir():
        # No consolidated export at hand: walk the per-day CSVs. This is the
        # slow path (tens of thousands of files), but it saves having to build
        # the consolidated copy just to index.
        from gmail_bulk_export.core.load_metadatas import load_metadatas

        yield load_metadatas(source)
        return

    if source.suffix == ".parquet":
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(source)
        for batch in parquet.iter_batches(batch_size=chunk_size):
            yield batch.to_pandas()
        return

    import pandas as pd

    for chunk in pd.read_csv(source, dtype=str, chunksize=chunk_size):
        yield chunk


def _normalize(frame):
    """Leaves the chunk with the columns of `ROW_COLUMNS`, already typed."""
    import pandas as pd

    from gmail_bulk_export.data_transforms import transform_list_columns

    frame = frame.rename(columns=SOURCE_COLUMNS)
    for column in SOURCE_COLUMNS.values():
        if column not in frame.columns:
            frame[column] = None

    # `internal_date` arrives as epoch-ms text (CSV) or as a real datetime
    # (typed Parquet). Both paths have to end up as the same integer.
    dates = frame["internal_date"]
    if pd.api.types.is_datetime64_any_dtype(dates):
        if getattr(dates.dtype, "tz", None) is not None:
            dates = dates.dt.tz_convert("UTC").dt.tz_localize(None)
        # The cast to `datetime64[ms]` is not decorative: pandas picks the unit
        # of the dtype (ns, us or ms depending on where the Parquet came from),
        # so a bare `astype("int64")` returns a number on another scale.
        millis = dates.astype("datetime64[ms]").astype("int64").where(dates.notna())
    else:
        millis = pd.to_numeric(dates, errors="coerce")
    frame["internal_date"] = millis

    # A list repr in the CSV, a real list in the Parquet: `transform_list_columns`
    # absorbs both.
    frame = transform_list_columns(frame, ["labels"])
    frame["size_estimate"] = pd.to_numeric(frame["size_estimate"], errors="coerce")
    frame["from_domain"] = (
        frame["sender"].fillna("").str.extract(DOMAIN_RE, expand=False).str.lower()
    )

    # The metadata carries no attachment field: `multipart/mixed` is the best
    # signal available, and that is what it is — a signal, not a fact.
    content = frame["content_type"].fillna("").str.lower()
    frame["has_attachment"] = content.str.startswith("multipart/mixed").astype(int)

    return frame[ROW_COLUMNS]


def _missing(value):
    """True for None and for pandas NaN/NaT, without importing pandas."""
    return value is None or value != value


def _text(value):
    """Text or None; absorbs pandas NaN and the already-stringified 'nan'."""
    if _missing(value):
        return None
    text = str(value)
    return text if text and text != "nan" else None


def _rows(frame, label_names):
    """Yields `(emails tuple, label names)` for each row of the chunk."""
    for values in frame.itertuples(index=False, name=None):
        (
            msg_id,
            thread_id,
            mailbox,
            millis,
            subject,
            sender,
            recipients,
            cc,
            delivered_to,
            from_domain,
            snippet,
            label_ids,
            size,
            content_type,
            has_attachment,
            source_file,
        ) = values

        millis = None if _missing(millis) else int(millis)
        sent_at = (
            datetime.fromtimestamp(millis / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if millis is not None
            else None
        )

        mailbox = _text(mailbox) or ""
        per_mailbox = label_names.get(mailbox) or label_names.get("", {})
        names = [per_mailbox.get(label_id, label_id) for label_id in label_ids]

        yield (
            (
                _text(msg_id),
                _text(thread_id),
                mailbox,
                millis,
                sent_at,
                _text(subject),
                _text(sender),
                _text(recipients),
                _text(cc),
                _text(delivered_to),
                _text(from_domain),
                _text(snippet),
                " | ".join(names) if names else None,
                None if _missing(size) else int(size),
                _text(content_type),
                int(has_attachment),
                int("SENT" in label_ids),
                _text(source_file),
            ),
            names,
        )


def build_index(source, db_path, labels_dir=None, chunk_size=200_000, progress=None):
    """Builds the index from scratch and returns a summary.

    `db_path` is overwritten if it exists: the index is a derivative, always
    rebuildable from `output/`, so there is nothing in it worth keeping.
    """
    source = Path(source)
    db_path = Path(db_path)
    if labels_dir is None:
        labels_dir = source if source.is_dir() else source.parent
    label_names = load_label_names(labels_dir)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    # No journal, no fsync: an interrupted build is simply run again.
    connection.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
    connection.executescript(SCHEMA)

    placeholders = ",".join("?" * len(INSERT_COLUMNS))
    insert_email = (
        f"INSERT INTO emails (rowid,{','.join(INSERT_COLUMNS)}) VALUES (?,{placeholders})"
    )

    # The unique index is created at the end, so de-duplication by
    # `(id, mailbox)` — the same key `core.load_metadatas` uses — happens here.
    # Month chunks overlap on purpose, so duplicates do arrive.
    seen = set()
    inserted = 0
    duplicates = 0

    for frame in iter_source_frames(source, chunk_size):
        if frame.empty:
            continue
        email_rows = []
        label_rows = []
        for values, names in _rows(_normalize(frame), label_names):
            key = (values[0], values[2])
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            rowid = len(seen)
            email_rows.append((rowid,) + values)
            label_rows.extend((rowid, name) for name in names)

        connection.executemany(insert_email, email_rows)
        connection.executemany(
            "INSERT INTO email_labels (email_rowid, label) VALUES (?, ?)", label_rows
        )
        inserted += len(email_rows)
        connection.commit()
        if progress:
            progress(inserted, "rows")

    if progress:
        progress(inserted, "indexes")
    connection.executescript(INDEXES)
    if progress:
        progress(inserted, "fts")
    connection.executescript(FTS_SCHEMA)
    connection.execute(FTS_POPULATE)
    connection.execute("INSERT INTO emails_fts(emails_fts) VALUES ('optimize')")

    meta = {
        "source": str(source),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "rows": str(inserted),
        "duplicates": str(duplicates),
    }
    connection.executemany("INSERT INTO index_meta (key, value) VALUES (?, ?)", list(meta.items()))
    connection.commit()
    connection.execute("ANALYZE")
    connection.commit()

    summary = index_stats(connection)
    summary["duplicates"] = duplicates
    connection.close()
    return summary


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    ("%Y-%m-%d %H:%M:%S", "second"),
    ("%Y-%m-%d %H:%M", "minute"),
    ("%Y-%m-%d", "day"),
    ("%d/%m/%Y", "day"),
    ("%Y-%m", "month"),
    ("%Y/%m", "month"),
    ("%Y", "year"),
]


def parse_date_bound(text, end=False, utc=False):
    """Turns `2023`, `2023-05`, `2023-05-17` or `17/05/2023` into epoch-ms.

    `end=True` returns the **inclusive end** of the period: `--until 2023-05`
    reaches 31 May at 23:59:59.999, which is what whoever typed it expects.
    Dates without a time are read in local time unless `utc=True`; what is
    stored is always UTC.
    """
    text = str(text).strip()
    for pattern, precision in _DATE_FORMATS:
        try:
            moment = datetime.strptime(text, pattern)
        except ValueError:
            continue
        if end:
            moment = _end_of(moment, precision)
        return _to_millis(moment, utc)
    raise ValueError(
        f"Unrecognized date: {text!r}. Accepted: YYYY, YYYY-MM, YYYY-MM-DD, "
        "DD/MM/YYYY, 'YYYY-MM-DD HH:MM'"
    )


def _end_of(moment, precision):
    if precision == "year":
        moment = moment.replace(month=12, day=31)
        precision = "day"
    if precision == "month":
        last = calendar.monthrange(moment.year, moment.month)[1]
        moment = moment.replace(day=last)
        precision = "day"
    if precision == "day":
        moment = moment.replace(hour=23, minute=59)
        precision = "minute"
    if precision == "minute":
        moment = moment.replace(second=59)
    return moment.replace(microsecond=999_999)


def _to_millis(moment, utc):
    if utc:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def format_timestamp(millis, utc=False, fmt="%Y-%m-%d %H:%M"):
    """Epoch-ms to readable text, in local time unless `utc=True`."""
    if millis is None:
        return ""
    zone = timezone.utc if utc else None
    return (
        datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc).astimezone(zone).strftime(fmt)
    )


_WINDOW_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([smhd])$", re.IGNORECASE)
_WINDOW_UNITS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}


def parse_window(text):
    """`90s`, `45m`, `6h`, `2d` -> milliseconds. Used to pad `--around`."""
    match = _WINDOW_RE.match(str(text).strip())
    if not match:
        raise ValueError(f"Unrecognized window: {text!r} (e.g. 90s, 45m, 6h, 2d)")
    number, unit = match.groups()
    return int(float(number.replace(",", ".")) * _WINDOW_UNITS[unit.lower()])


def around_bounds(around, window, utc=False):
    """`(since, until)` for `--around`: the period `around` names, padded by `window`.

    `around` goes through `parse_date_bound` in both its modes, so the padding
    behaves the same regardless of the precision typed: `--around 2024-03-14
    --window 6h` pads the whole day, `--around "2024-03-14 09:32" --window 6h`
    pads just that minute.
    """
    since = parse_date_bound(around, utc=utc) - window
    until = parse_date_bound(around, end=True, utc=utc) + window
    return since, until


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------

# Exposed name -> SQL expression. The names mirror the CSV's so that an export
# can be joined against `emails_with_mailboxes.csv` without renaming anything.
FIELDS = {
    "date": "e.internal_date",
    "id": "e.id",
    "threadId": "e.thread_id",
    "mailbox": "e.mailbox",
    "subject": "e.subject",
    "from": "e.sender",
    "to": "e.recipients",
    "cc": "e.cc",
    "deliveredTo": "e.delivered_to",
    "fromDomain": "e.from_domain",
    "labels": "e.labels",
    "snippet": "e.snippet",
    "sizeEstimate": "e.size_estimate",
    "contentType": "e.content_type",
    "hasAttachment": "e.has_attachment",
    "isSent": "e.is_sent",
    "sentAtUTC": "e.sent_at",
    "filePath": "e.source_file",
    # The day of the folder the message's metadata lives in, which is the one
    # phase 2 derived from the raw `Date` header. It cannot be recomputed from
    # `internal_date`: those are dates in different time zones and they differ
    # for about 1% of messages. It is read back from the source file name,
    # `<YYYY-MM-DD>.csv`.
    "day": (
        "CASE WHEN e.source_file GLOB '*[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].csv' "
        "THEN substr(e.source_file, length(e.source_file) - 13, 10) "
        "ELSE substr(e.sent_at, 1, 10) END"
    ),
}

DEFAULT_FIELDS = ["date", "id", "mailbox", "deliveredTo", "from", "subject"]

# Everything that helps identify a message and cross-reference it against the
# consolidated CSV or another download phase. Does not fit in a table row — it
# is what `--format detail` uses to print one block per message instead.
DETAIL_FIELDS = [
    "date",
    "id",
    "threadId",
    "mailbox",
    "deliveredTo",
    "from",
    "to",
    "cc",
    "subject",
    "labels",
    "snippet",
    "sizeEstimate",
    "hasAttachment",
    "day",
    "filePath",
]

ORDERS = {
    "date": "e.internal_date DESC",
    "date-asc": "e.internal_date ASC",
    "size": "e.size_estimate DESC",
    "relevance": "f.rank",
}

_TERM_RE = re.compile(r'"([^"]*)"|(\S+)')


@dataclass
class Query:
    """Filters for one search. Every `None`/empty value means "do not filter".

    Lists are **OR within a field** and **AND across fields**: two `--mailbox`
    values mean "either of them", but a `--mailbox` and a `--label` both have to
    match.
    """

    text: str = None
    subject: str = None
    sender: str = None
    recipient: str = None
    raw_query: str = None
    contains: str = None
    since: int = None
    until: int = None
    mailboxes: list = field(default_factory=list)
    delivered_to: list = field(default_factory=list)
    domains: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    ids: list = field(default_factory=list)
    thread: str = None
    direction: str = None  # "sent" | "received"
    with_attachment: bool = False
    min_size: int = None
    max_size: int = None


def open_index(db_path):
    """Opens the index read-only and returns rows as dicts."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def fts_expression(query):
    """The FTS5 MATCH expression, or None if the query asks for no text.

    User text is quoted term by term rather than passed through: a stray
    apostrophe or hyphen is syntax in FTS5 and would make the query fail
    outright. `--raw-query` is the escape hatch for anyone who does want that
    syntax.
    """
    parts = []
    if query.raw_query:
        parts.append(f"({query.raw_query})")
    for value, column in (
        (query.text, None),
        (query.subject, "subject"),
        (query.sender, "sender"),
        (query.recipient, "recipients"),
    ):
        if not value:
            continue
        terms = _quote_terms(value)
        if not terms:
            continue
        parts.append(f"{{{column}}} : ({terms})" if column else f"({terms})")
    return " AND ".join(parts) if parts else None


def _quote_terms(value):
    """Each word quoted and ANDed together; `word*` stays a prefix search."""
    terms = []
    for phrase, word in _TERM_RE.findall(value):
        if phrase:
            terms.append(f'"{phrase}"')
            continue
        prefix = word.endswith("*")
        cleaned = word.rstrip("*").replace('"', "")
        if not cleaned:
            continue
        terms.append(f'"{cleaned}"*' if prefix else f'"{cleaned}"')
    return " AND ".join(terms)


def _match_any(column, values, params, like=False):
    """`col = ?` per value, or `LIKE` when the value carries a wildcard."""
    clauses = []
    for value in values:
        if like or "*" in value or "%" in value:
            clauses.append(f"{column} LIKE ?")
            params.append(f"%{value}%" if like else value.replace("*", "%"))
        else:
            clauses.append(f"{column} = ?")
            params.append(value)
    return "(" + " OR ".join(clauses) + ")"


def build_sql(query, select, order="date", limit=None, offset=0):
    """Returns `(sql, params)` for whichever SELECT clause it is handed."""
    params = []
    joins = []
    where = []

    match = fts_expression(query)
    if match:
        # A subquery rather than `MATCH` in the WHERE clause, so that `rank` is
        # available to order by relevance.
        joins.append(
            "JOIN (SELECT rowid, rank FROM emails_fts WHERE emails_fts MATCH ?) f "
            "ON f.rowid = e.rowid"
        )
        params.append(match)

    if query.contains:
        where.append("(e.subject LIKE ? OR e.snippet LIKE ?)")
        params.extend([f"%{query.contains}%"] * 2)
    if query.since is not None:
        where.append("e.internal_date >= ?")
        params.append(query.since)
    if query.until is not None:
        where.append("e.internal_date <= ?")
        params.append(query.until)
    if query.mailboxes:
        where.append(_match_any("e.mailbox", query.mailboxes, params))
    if query.delivered_to:
        where.append(_match_any("e.delivered_to", query.delivered_to, params))
    if query.domains:
        where.append(_match_any("e.from_domain", query.domains, params))
    if query.ids:
        where.append(_match_any("e.id", query.ids, params))
    if query.thread:
        where.append("e.thread_id = ?")
        params.append(query.thread)
    if query.labels:
        # Substring, not equality: real labels look like
        # "@Assigned/Jane Doe" and nobody wants to type that in full.
        condition = _match_any("l.label", query.labels, params, like=True)
        where.append(
            f"EXISTS (SELECT 1 FROM email_labels l WHERE l.email_rowid = e.rowid AND {condition})"
        )
    if query.direction == "sent":
        where.append("e.is_sent = 1")
    elif query.direction == "received":
        where.append("e.is_sent = 0")
    if query.with_attachment:
        where.append("e.has_attachment = 1")
    if query.min_size is not None:
        where.append("e.size_estimate >= ?")
        params.append(query.min_size)
    if query.max_size is not None:
        where.append("e.size_estimate <= ?")
        params.append(query.max_size)

    sql = f"SELECT {select} FROM emails e {' '.join(joins)}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if order:
        if order == "relevance" and not match:
            order = "date"
        sql += " ORDER BY " + ORDERS[order]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    elif offset:
        # SQLite has no OFFSET without LIMIT; -1 is its way of saying "all".
        sql += " LIMIT -1 OFFSET ?"
        params.append(offset)
    return sql, params


def search(connection, query, fields=None, order="date", limit=50, offset=0):
    """Runs the search and returns a list of dicts holding `fields`."""
    fields = fields or DEFAULT_FIELDS
    unknown = [name for name in fields if name not in FIELDS]
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(unknown)}")
    select = ", ".join(f'{FIELDS[name]} AS "{name}"' for name in fields)
    sql, params = build_sql(query, select, order=order, limit=limit, offset=offset)
    return [dict(row) for row in connection.execute(sql, params)]


def count(connection, query):
    sql, params = build_sql(query, "COUNT(*)", order=None)
    return connection.execute(sql, params).fetchone()[0]


def breakdown(connection, query, dimension="mailbox"):
    """Counts grouped by mailbox, year, domain or label for the same filters."""
    expressions = {
        "mailbox": "e.mailbox",
        "year": "strftime('%Y', e.internal_date / 1000, 'unixepoch')",
        "month": "strftime('%Y-%m', e.internal_date / 1000, 'unixepoch')",
        "domain": "e.from_domain",
        "deliveredTo": "e.delivered_to",
    }
    if dimension not in expressions:
        raise ValueError(f"Unknown dimension: {dimension}")
    expression = expressions[dimension]
    sql, params = build_sql(query, f"{expression} AS bucket, COUNT(*) AS n", order=None)
    # Time reads in order; domains and labels run to the hundreds and
    # what matters about them is who is on top.
    chronological = dimension in ("year", "month")
    sql += " GROUP BY bucket ORDER BY " + ("bucket" if chronological else "n DESC, bucket")
    return [(row["bucket"], row["n"]) for row in connection.execute(sql, params)]


def index_stats(connection):
    """Index summary: rows, mailboxes, date range and build metadata."""
    connection.row_factory = sqlite3.Row
    stats = {row["key"]: row["value"] for row in connection.execute("SELECT * FROM index_meta")}
    row = connection.execute(
        "SELECT COUNT(*) AS rows, COUNT(DISTINCT mailbox) AS mailboxes, "
        "COUNT(DISTINCT thread_id) AS threads, MIN(internal_date) AS first, "
        "MAX(internal_date) AS last FROM emails"
    ).fetchone()
    stats.update(
        {
            "rows": row["rows"],
            "mailboxes": row["mailboxes"],
            "threads": row["threads"],
            "first": row["first"],
            "last": row["last"],
        }
    )
    return stats
