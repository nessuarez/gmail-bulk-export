# core/

Shared infrastructure: rate limiting, checkpoints, CSV/gzip I/O, and message
parsing.

**Package rule**: nothing here should depend on `argparse`. If a module needs
Gmail credentials, it probably belongs in [../scripts/](../scripts/CLAUDE.md)
instead.

## Map

| File | What it does | Watch out for |
| --- | --- | --- |
| [rate_limit.py](rate_limit.py) | Token bucket + retry policy | Every network call goes through here |
| [checkpoints.py](checkpoints.py) | JSON state per (mailbox, work unit) | Resumption depends on this |
| [save_to_csv.py](save_to_csv.py) | Metadata schema + incremental merge | Source of truth for the schema |
| [load_metadatas.py](load_metadatas.py) | Consolidates the daily CSVs | `python -m core.load_metadatas [--also-parquet]` |
| [load_email_bodies.py](load_email_bodies.py) | Loads the `.jsonl.gz` bodies into a DataFrame | |
| [email_body_handler.py](email_body_handler.py) | Extracts plain text and HTML from the MIME payload | |
| [attachment_handler.py](attachment_handler.py) | Filters attachments by MIME type and saves them | |
| [csv_handler.py](csv_handler.py), [file_handler.py](file_handler.py), [utils.py](utils.py) | Id reading, path building, helpers | |

## rate_limit.py — the contract

Two pieces, and both are required:

```python
from core.rate_limit import gmail_retry, pace


@gmail_retry
def _fetch(service, msg_id):
    pace()  # wait your turn in the token bucket
    return service.users().messages().get(...).execute()
```

- **`pace()`** claims tokens from a shared bucket targeting
  `messages_per_second` (40 by default). Gmail's quota is 250 units per user
  per second, and both `messages.list` and `messages.get` cost 5 → the real
  ceiling is ~50 msg/s per mailbox.
- **`gmail_retry`** retries with exponential backoff + jitter, but **only
  transient failures**: 429, 5xx, and 403 *when the `reason` is quota-related*
  (`rateLimitExceeded`, `userRateLimitExceeded`, `quotaExceeded`,
  `backendError`). A 404, or a 403 from missing delegation, fails immediately
  instead of burning 8 multi-second retries. That distinction lives in
  `is_retryable` — it's the place to look if something retries when it
  shouldn't, or the reverse.

This replaced a `time.sleep(random.uniform(1, 5))` every 20 messages that cost
~3s per batch whether it was needed or not, and didn't react at all when the
API actually did complain. Don't reintroduce fixed waits.

## checkpoints.py

One JSON file per work unit, at `OUTPUT_DIR/<mailbox>/.checkpoints/<key>.json`.
The key comes from `make_key(start_date, end_date, phase)`; `phase` is what
lets bodies and attachments for the same month have separate checkpoints
without colliding.

**A chunk is only marked `done` if it finished with zero per-message
failures.** With any failures it stays `partial`, and the next run retries it
whole. That's a cheap re-process — the days already saved aren't lost, since
the merge deduplicates — and it's what makes "resume" reliable instead of
approximate. Don't relax it to save re-work.

## save_to_csv.py

- `METADATA_FIELDNAMES` is the **source of truth for the schema**.
  `merge_csv_files` unions the columns found on disk with the constant, so
  adding a field there is enough for it to also appear in days downloaded
  before that field existed. No re-download needed.
- **One lock per destination file** (`_lock_for`). `process_emails` runs
  across several threads, and two of them routinely land on the same date;
  the merge's read-modify-write has to be serialized per file.
- **The `id`-based dedup is workload, not defense.** Month chunks overlap by
  one day on purpose (see `../scripts/chunking.py`), so duplicates arrive on
  every normal run. Removing the dedup produces repeated rows at every month
  boundary.
- `csv.field_size_limit` is raised to 10 MB: some subjects and headers blow
  past the default limit.

## load_metadatas.py

Walks every `output/<mailbox>/<date>/<date>.csv`, adds the `mailbox` and
`file_path` columns, deduplicates by `(id, mailbox)`, and writes the
consolidated `output/emails_with_mailboxes.csv`.

`--also-parquet` additionally writes a typed `emails_with_mailboxes.parquet`
next to it: `labelIds` becomes a real list column and `internalDate` a real
datetime column (using the same idempotent transforms
[data_transforms.py](../data_transforms.py) already applies elsewhere),
instead of the CSV's string repr and epoch-milliseconds text. The CSV itself
is unaffected either way — this is purely an additional, typed copy for tools
like DuckDB that read Parquet natively.
