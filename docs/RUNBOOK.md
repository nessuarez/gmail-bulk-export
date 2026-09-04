# Runbook — Incremental Gmail download

Operational guide for downloading metadata, bodies, and attachments across
several Gmail mailboxes, **incrementally and resumably**, up to a consolidated
CSV (and optionally Parquet) ready for analysis.

All commands run **from the repo root**, with the virtual environment
activated.

---

## 0. Prerequisites

| Requirement | Detail |
|---|---|
| Service account | With **domain-wide delegation** enabled in Google Cloud Console |
| JSON key | Downloaded and saved as `client_secret.json` in the repo root (or the path `CREDENTIALS_FILE` points to) |
| Authorized scope | `https://www.googleapis.com/auth/gmail.readonly`, granted in the Google Workspace Admin console |
| Dependencies | `uv sync` (creates `.venv` from `uv.lock`) |

Quick check:

```bash
python -m gmail_bulk_export.cli.main token                    # validates the credentials file
python -m gmail_bulk_export.scripts.download_metadata --mailboxes-file mailboxes.txt \
    --start 2024-01 --end 2024-01 --dry-run   # validates access to each mailbox
```

`--dry-run` prints a `✓` or `✗` per mailbox. An inaccessible mailbox is
**skipped**, not fatal to the run.

### Mailbox list

One mailbox per line; lines starting with `#` are ignored. Start from
[mailboxes.example.txt](../mailboxes.example.txt) — the real `mailboxes*.txt`
files are in `.gitignore` because they typically contain real addresses, so
on a fresh clone you'll need to create your own.

---

## 1. Configuration

Priority: command-line arguments > environment variables (`.env`) >
`config.json` > defaults.

`config.json`:

| Key | Current value | What it's for |
|---|---|---|
| `output_directory` | `./output` | Root of all downloaded data |
| `batch_size` | `25` | Sub-requests per HTTP batch (max 100 per Gmail's limit) |
| `max_workers` | `2` | Concurrent threads per mailbox |
| `messages_per_second` | `40` | Target rate for the token bucket |
| `retry_attempts` | `8` | Retries on transient failures |
| `retry_wait_max` | `64` | Backoff ceiling, in seconds |

> **`batch_size` × `max_workers` is the number that matters.** Gmail limits
> *concurrent* requests per user separately from the per-second quota. With
> `100 × 5` you get mass `429 Too many concurrent requests` (in one test, over
> a third of a month's messages failed). With `25 × 2` the real rate is ~37-42
> messages/s with zero failures. On 429s, lower `max_workers` first.

### Throughput: parallelize by mailbox, not by credential

Gmail's quota is **250 units per user per second** (`messages.get` and
`messages.list` cost 5 each → a ceiling of ~50 messages/s **per mailbox**).
It's a quota *per mailbox*, not per credential or per Google Cloud project:

- Creating more service accounts or API keys **doesn't speed anything up**.
  Ten credentials against the same mailbox still share the same 250 units/s.
- Running several mailboxes at once **does** multiply the rate, because each
  one has its own quota. The project-wide limit (20,000 units/s) is far away.

Hence `--jobs` on `download_metadata`. By default it attacks every mailbox in
the list in parallel.

The token bucket is **per mailbox** (`pace(n, name=mailbox)`). Sharing a
single global one would mean a dozen mailboxes in parallel throttle each other
down to the rate of one, and the parallelism buys nothing.

The final tail doesn't parallelize: once only the largest mailbox is left,
the rate drops back to a single mailbox's. In practice the last handful of
chunks take as long as the hundreds before them.

> To change the destination, either of these works:
>
> ```bash
> OUTPUT_DIR=/other/path python -m gmail_bulk_export.scripts.download_metadata ...
> gmail-bulk-export --output-dir /other/path metadata --username ...
> ```
>
> See [CONFIG.md](CONFIG.md#redirecting-the-output).

---

## 2. The four phases

```
  1. labels      →  output/<mailbox>/labels.csv
  2. metadata    →  output/<mailbox>/<YYYY-MM-DD>/<YYYY-MM-DD>.csv
     ↓ consolidation
     output/emails_with_mailboxes.csv (+ .parquet with --also-parquet)
  3. bodies      →  output/<mailbox>/<YYYY-MM-DD>/<msg_id>.jsonl.gz
  4. attachments →  output/<mailbox>/<YYYY-MM-DD>/<msg_id>_attachments.gz
```

Phase 3 needs the consolidated CSV from phase 2. Phases 1 and 2 run together
with a single command.

---

## 3. Phases 1 and 2 — Labels and metadata

```bash
python -m gmail_bulk_export.scripts.download_metadata \
    --mailboxes-file mailboxes.txt \
    --start 2023-01 --end 2024-12 \
    --priority 2024
```

What it does:

- Slices the range into **months**. Each (mailbox, month) is a unit with its
  own checkpoint, so an interruption only costs the current month.
- Downloads the `--priority` years first (for every mailbox), then the rest
  from most recent to oldest. The highest-value data arrives in the first
  hour.
- Downloads each mailbox's labels the first time it's touched.
- Overlaps each month by one day on each side. Gmail's `before:` is
  **exclusive** and dates resolve in the mailbox's timezone; the overlap
  guarantees no gaps at month boundaries. Duplicates are absorbed by the
  merge's `id`-based dedup.

Useful options:

| Option | Effect |
|---|---|
| `--dry-run` | Shows the plan and each chunk's status, without downloading |
| `--only <mailbox>` | Processes a single mailbox from the list |
| `--force` | Reprocesses chunks already marked `done` |
| `--skip-auth-check` | Skips the pre-flight check (faster when resuming) |
| `--mailboxes a@x,b@x` | Inline list, an alternative to `--mailboxes-file` |

### Resuming

**The same command.** Already-complete chunks are skipped:

```
Summary: 0 chunks completed, 1 already done, 0 with failures, 0 new messages
```

A chunk is only marked `done` if it finished with **zero** per-message
failures. If there were failures it stays `partial`, and the next run retries
it whole (days already saved aren't lost: the merge is incremental and
deduplicates).

### Long background runs

```bash
python -u -m gmail_bulk_export.scripts.download_metadata --mailboxes-file mailboxes.txt \
    --start 2023-01 --end 2024-12 --priority 2024 \
    > logs/full_run.log 2>&1 &

# follow along
grep -E "^\[" logs/full_run.log | tail -3
```

Rate reference: ~37-42 messages/s, i.e. roughly **40 minutes per 100,000
emails**.

---

## 4. Seeing what's missing

```bash
python -m gmail_bulk_export.scripts.progress_report                  # mailbox × month grid
python -m gmail_bulk_export.scripts.progress_report --detail          # + a list of what's pending
python -m gmail_bulk_export.scripts.progress_report --start 2024-01 --end 2024-12
```

With no `--start`/`--end`, the range is the span of day folders already
in `output/` — the current year when there are none yet.

```
mailbox                       labels  2024
                                      JFMAMJJASOND
user1@example.com             yes     ############
user2@example.com             yes     #####.......

  # complete   ! with failures   x error   . pending
```

Exit codes: `0` everything covered, `2` work remains.

### Dead-letters

When an individual `messages.get` fails inside a batch, Gmail delivers the
error to the callback instead of raising it from `batch.execute()`. Those
messages are retried up to 6 rounds with backoff; whatever survives that gets
recorded at:

```
output/<mailbox>/.checkpoints/failed_metadata_<start>_<end>.jsonl
```

To retry them, re-run the month: the chunk is marked `partial`, so it isn't
skipped.

---

## 5. Consolidation

```bash
python -m gmail_bulk_export.core.load_metadatas --mailboxes-file mailboxes.txt --also-parquet
```

Walks `output/<mailbox>/<date>/<date>.csv`, adds the `mailbox` and
`file_path` columns, deduplicates by `(id, mailbox)`, and writes
`output/emails_with_mailboxes.csv`. Prints the per-mailbox and per-year row
counts — the fastest way to spot a gap.

`--also-parquet` additionally writes a typed `emails_with_mailboxes.parquet`
next to it — see [ANALYSIS.md](ANALYSIS.md) for what that buys you.

`--mailboxes-file` restricts consolidation to the mailboxes you care about
(useful if `output/` still has earlier test mailboxes lying around).

> Run it as a module. `python core/load_metadatas.py` fails on
> `from config import output_dir`.

---

## 6. Phase 3 — Bodies

```bash
# how much work, and how much disk
python -m gmail_bulk_export.scripts.download_payloads --year 2024 --dry-run

# download (bodies only)
python -m gmail_bulk_export.scripts.download_payloads --year 2024
```

Work units are (mailbox × month) with a checkpoint, same as metadata. An
email whose `.jsonl.gz` already exists is always skipped, so the command is
safe to repeat.

Each body is a compressed JSON with: `id`, `date`, `from`, `to`, `subject`,
`content_type`, `body_plain`, `body_html`, `attachments` (metadata: name,
type, size), and `attachments_with_content`.

**The attachment listing is already in the body**; you don't need phase 4 to
know what attachments an email has, only to get the actual bytes.

### Only the emails a search returns

`download_payloads.py` works in whole months. To fetch the bodies of a handful
of specific emails — the ones § 8 bis locates — download **by id**:

```bash
# 1. export the hits in the shape the downloader reads
python -m gmail_bulk_export.scripts.search_emails --subject invoice --since 2025-02     --for-payloads --out output/ids.csv
gmail-bulk-export payloads --csv-file output/ids.csv        # + --with-attachments

# 2. or straight away, for a handful of ids
gmail-bulk-export payloads --username desk@example.com --email_ids 1952bd04b8165682
```

The CSV needs `id` and `mailbox`. With `--csv-file` no `--username` is needed:
each row carries its own, so one export can span several mailboxes.

**A body is always written to the folder the message itself dictates** —
`process_email` derives it from the `Date` header of what it just downloaded —
so it lands next to its per-day CSV no matter where the list came from. The
CSV's `date` column only decides **whether an already-downloaded body is
skipped**, which is why `--for-payloads` emits the folder day (taken from
`file_path`) rather than the UTC date: the two differ for about 1% of messages,
and with the wrong date the downloader does not recognise what it already has
and fetches it again.

Neither path carries checkpoints. For a whole month, or to resume, use
`download_payloads.py`.

Recommended order for a full backfill:

```bash
python -m gmail_bulk_export.scripts.download_payloads --year 2024
python -m gmail_bulk_export.scripts.download_payloads --year 2023
python -m gmail_bulk_export.scripts.download_payloads --year 2025
python -m gmail_bulk_export.scripts.download_payloads --year 2022,2021,2020
python -m gmail_bulk_export.scripts.download_payloads --year 2019,2018,2017,2016
```

## 7. Phase 4 — Attachments

Attachments travel inside the same `format=raw` payload as the body, so they
download with the same command plus a flag:

```bash
python -m gmail_bulk_export.scripts.download_payloads --year 2024 --with-attachments
```

Saved as `<msg_id>_attachments.gz` next to the body, filtered by MIME type
(PDFs, images, and office documents by default; `--all-attachment-types`
disables the filter). The checkpoint is separate from the bodies-only one, so
you can do a first pass of bodies and add attachments later without any
inconsistent re-download.

---

## 8. Analysis

```bash
python -m gmail_bulk_export.core.load_metadatas --mailboxes-file mailboxes.txt --also-parquet
```

See [ANALYSIS.md](ANALYSIS.md) for what to do next with
`output/emails_with_mailboxes.csv` (or the typed `.parquet` copy) — starting
with plain pandas, and a few concrete options (DuckDB, SQLite full-text
search, a data-quality pass) for exports too large for pandas alone to
handle comfortably.

---

## 8 bis. Finding specific emails

The analysis path is for **understanding** the corpus; for **finding** a
message — "that customer's May 2023 email with 'invoice' in the subject" — it
is the wrong instrument: loading the consolidated CSV costs hundreds of MB and
tens of seconds every time. There is a SQLite index with FTS5 for that.

```bash
python -m gmail_bulk_export.core.load_metadatas --also-parquet
python -m gmail_bulk_export.scripts.build_search_index   # writes output/search_index.sqlite3
```

Each query then takes milliseconds:

```bash
# free text (subject + snippet + sender + recipients)
python -m gmail_bulk_export.scripts.search_emails -q "quarterly report" --since 2023-01 --until 2023-06

# by subject, in one mailbox, first 100
python -m gmail_bulk_export.scripts.search_emails --subject invoice --mailbox desk@example.com -n 100

# by customer domain, with an attachment, exported to CSV
python -m gmail_bulk_export.scripts.search_emails --domain acme.com --with-attachment     --out output/acme.csv

# by label, counted per year instead of listed
python -m gmail_bulk_export.scripts.search_emails --label "Assigned/Jane" --breakdown year

# the whole thread behind a hit, oldest first
python -m gmail_bulk_export.scripts.search_emails --thread 18f2a1b3c4d5e6f7 --order date-asc
```

`--help` lists every option. What is worth knowing up front:

| | |
| --- | --- |
| **Text** | `-q`, `--subject`, `--from`, `--to` go through FTS5: they **ignore accents and case** ("resume" finds "résumé") and match **whole words**. `invoic*` is a prefix search; `--contains` is a literal substring, accent-aware and slower |
| **Dates** | `2023`, `2023-05`, `2023-05-17`, `17/05/2023` or `2023-05-17 08:45`. `--until` is **inclusive**: `--until 2023-05` reaches 31 May. Read in local time; `--utc` switches both input and output |
| **Repeatable** | `--mailbox`, `--label`, `--domain`, `--delivered-to`, `--id` are **OR within the field** and **AND across fields** |
| **Labels** | `--label` matches the **name**, not `Label_1234`: the index resolves them with **each mailbox's** `labels.csv` (ids are per mailbox). Needs phase 1 |
| **Phase 3** | `--for-payloads` writes an `id,mailbox,date` CSV that `gmail-bulk-export payloads --csv-file` reads as-is (§ 6) |
| **Output** | `--format table\|csv\|json\|jsonl\|ids`, `--out FILE`, `--fields`, `--count`, `--breakdown mailbox\|year\|month\|domain\|deliveredTo`. With `--out` the format is inferred from the extension and **every** hit is exported, not the console's 50 |
| **Attachments** | `--with-attachment` filters on `multipart/mixed`. That is a **signal**, not a fact: the metadata carries no attachment field |

Two limits that come from what was downloaded, not from the index:

- **Metadata only.** The searchable text is the subject and the `snippet` (the
  first ~200 characters of the body). Searching inside bodies means downloading
  phase 3 first.
- **The index is a snapshot.** It is a rebuildable derivative: after downloading
  new months, `python -m gmail_bulk_export.scripts.build_search_index --force`.

---

## 9. `output/` structure

```
output/
├── emails_with_mailboxes.csv      # consolidated (phase 2) — analysis input
├── emails_with_mailboxes.parquet  # optional typed copy (--also-parquet)
├── search_index.sqlite3           # search index (§ 8 bis), a derivative
├── _last_metadata_run.json        # summary of the last run
└── <mailbox>/
    ├── labels.csv                 # phase 1
    ├── .checkpoints/
    │   ├── metadata_20240101_20240131.json    # state per (mailbox, month)
    │   └── failed_metadata_*.jsonl            # dead-letters
    └── <YYYY-MM-DD>/
        ├── <YYYY-MM-DD>.csv           # that day's metadata (phase 2)
        ├── <msg_id>.jsonl.gz          # body (phase 3)
        └── <msg_id>_attachments.gz    # attachments (phase 4)
```

Metadata CSV columns:

```
id, threadId, labelIds, sizeEstimate, historyId, internalDate,
deliveredTo, subject, from, to, cc, bcc, date, contentType, snippet
```

The consolidated file adds `mailbox` and `file_path`.

Format notes:

- `labelIds` is a **Python list repr**, not JSON → `ast.literal_eval`, not
  `json.loads`. `data_transforms.transform_list_columns` already does this
  (and the Parquet copy from `--also-parquet` stores it as a real list).
- `internalDate` is epoch **milliseconds** (reliable). `date` is the raw
  `Date` header (not always parseable).

---

## 10. Known issues

| Symptom | Cause and fix |
|---|---|
| `429 Too many concurrent requests for user` | `batch_size` × `max_workers` too high. Lower `max_workers` in `config.json`. |
| `AuthenticationError` on one mailbox | Missing delegation for that mailbox, or it doesn't exist. The orchestrator skips it and continues. |
| A month always stays `partial` | Check the dead-letter `.jsonl`: if the errors aren't 429s, they're probably messages deleted between the `list` and the `get`. |
| `UnicodeEncodeError` on the console | The scripts force UTF-8 on stdout; if you write a new one, copy that block. |

### Gmail quotas

- 250 units per user per second. `messages.list` and `messages.get` cost 5
  units each → a theoretical ceiling of ~50 messages/s per mailbox.
- There's also a **concurrent**-request limit per user, independent of the
  above. It's what shows up as `Too many concurrent requests`.
- 1,200,000 units per minute per project.

The token bucket in [core/rate_limit.py](../src/gmail_bulk_export/core/rate_limit.py) sets the
pace, and `gmail_retry` retries with exponential backoff and jitter **only**
transient failures (429, 5xx, and 403-by-quota). A 404 or a permissions error
fails immediately instead of burning 8 retries.
