# What to do with your exported data

None of this requires changing the tool itself — these are things you run
yourself against the CSV (or Parquet) it already produces.

## The schema

`output/emails_with_mailboxes.csv` (and the typed `.parquet` copy from
`--also-parquet`) has one row per message:

```
id, threadId, labelIds, sizeEstimate, historyId, internalDate,
deliveredTo, subject, from, to, cc, bcc, date, contentType, snippet,
mailbox, file_path
```

Two things to know before querying it, in either format:

- **`labelIds`** is a Gmail label-id list. In the CSV it's a Python list repr
  (`"['INBOX', 'SENT']"`, not JSON) — parse it with `ast.literal_eval`, not
  `json.loads`. The Parquet copy already stores it as a real list column, no
  parsing needed.
- **`internalDate`** is epoch milliseconds and is the reliable date field. The
  CSV's `date` column is the raw `Date:` header and doesn't always parse
  cleanly (mixed timezones, malformed headers). The Parquet copy already
  stores `internalDate` as a real datetime column.

## Starting point: plain pandas

The baseline everyone already knows, and a fine choice up to a few hundred
thousand rows:

```python
import ast
import pandas as pd

df = pd.read_csv("output/emails_with_mailboxes.csv", dtype=str)
df["labelIds"] = df["labelIds"].apply(ast.literal_eval)
df["internalDate"] = pd.to_datetime(pd.to_numeric(df["internalDate"], errors="coerce"), unit="ms")
```

If you generated the Parquet copy, skip the parsing entirely:

```python
import pandas as pd

df = pd.read_parquet("output/emails_with_mailboxes.parquet")
```

Past a few million rows across several years and mailboxes, pandas alone
starts to strain — the whole file has to fit in memory, and every fresh
Python session re-parses it from scratch. The options below are for that
point.

## Option 1 — DuckDB

[DuckDB](https://duckdb.org/) runs SQL directly against a Parquet file, no
server, no import step, and — because it reads Parquet's own column types —
no `ast.literal_eval` or date parsing needed. It comfortably handles files
far larger than what pandas is happy holding in memory.

```bash
pip install duckdb   # or: uv add --optional duckdb, in a scratch project
```

```python
import duckdb

con = duckdb.connect()
con.sql("""
    SELECT
        mailbox,
        date_trunc('month', internalDate) AS month,
        count(*) AS messages
    FROM 'output/emails_with_mailboxes.parquet'
    GROUP BY mailbox, month
    ORDER BY mailbox, month
""").show()
```

Or from the DuckDB CLI directly:

```sql
SELECT "from", count(*) AS n
FROM 'output/emails_with_mailboxes.parquet'
GROUP BY "from"
ORDER BY n DESC
LIMIT 20;
```

This is also a good fit if you'd rather keep just the CSV: DuckDB can query a
CSV directly too (`FROM 'output/emails_with_mailboxes.csv'`), just without
the pre-resolved types.

## Option 2 — SQLite + full-text search (FTS5)

If what you actually want is to **search** years of subject lines and
snippets rather than run aggregate analytics, SQLite's FTS5 extension is a
good fit — no server, ships with Python's standard library, and gives you
fast text search that a CSV/pandas workflow doesn't.

```python
import ast
import sqlite3

import pandas as pd

df = pd.read_csv("output/emails_with_mailboxes.csv", dtype=str)

con = sqlite3.connect("mail_search.db")
con.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS messages
    USING fts5(id UNINDEXED, mailbox UNINDEXED, subject, snippet)
""")
con.executemany(
    "INSERT INTO messages (id, mailbox, subject, snippet) VALUES (?, ?, ?, ?)",
    df[["id", "mailbox", "subject", "snippet"]].itertuples(index=False, name=None),
)
con.commit()

# search
for row in con.execute(
    "SELECT mailbox, subject FROM messages WHERE messages MATCH ? LIMIT 20",
    ("invoice OR receipt",),
):
    print(row)
```

## Option 3 — a data-quality pass before you trust a big export

Before building anything on top of a large backfill, it's worth checking the
export itself is complete and consistent — the same instinct behind
[`scripts/progress_report.py`](../scripts/progress_report.py), one level up.
A cheap pandas-only version:

```python
# duplicate ids across the whole export (should be none after load_metadatas' dedup)
df["id"].duplicated().sum()

# months with suspiciously few messages compared to their neighbors
df.groupby(df["internalDate"].dt.to_period("M")).size()

# rows missing a mailbox or a date — usually a sign a source CSV was malformed
df[df["mailbox"].isna() | df["internalDate"].isna()]
```

If you want something more structured, a library like
[Great Expectations](https://greatexpectations.io/) can encode these checks
as reusable rules instead of ad hoc snippets — worth it if you'll be
re-running the same validation on every future backfill.

If your goal from here is a dashboard to share with non-technical
stakeholders rather than further ad hoc querying, pointing a lightweight BI
tool (e.g. a notebook with Plotly, or something like Metabase/Evidence
against the DuckDB file above) at the Parquet export is a reasonable next
step — outside the scope of this guide, but the Parquet file from
`--also-parquet` is exactly what most of those tools expect as input.
