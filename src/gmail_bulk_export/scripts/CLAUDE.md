# scripts/

Orchestration: **N mailboxes × N months, with checkpoints and resumption.**
This is what actually gets run. The subcommands in
[../cli/](../cli/CLAUDE.md) are the low-level layer these modules call
in-process.

All of them are invoked as a module from the repo root:

```bash
python -m gmail_bulk_export.scripts.download_metadata --mailboxes-file mailboxes.txt --start 2016-01 --end 2025-12
```

`python scripts/download_metadata.py` **fails**: the `config` and `core`
imports need the repo root on `sys.path`.

Full operational guide (options, resumption, running in the background):
[../docs/RUNBOOK.md](../../../docs/RUNBOOK.md).

## Map

| File | Phase | Output |
| --- | --- | --- |
| [download_metadata.py](download_metadata.py) | 1 and 2 | `output/<mailbox>/<date>/<date>.csv` + `labels.csv` |
| [download_payloads.py](download_payloads.py) | 3 and 4 | `<msg_id>.jsonl.gz`, `<msg_id>_attachments.gz` |
| [progress_report.py](progress_report.py) | — | Mailbox × month grid in the console |
| [build_search_index.py](build_search_index.py) | search | `output/search_index.sqlite3` |
| [search_emails.py](search_emails.py) | search | Console, CSV or JSON |
| [chunking.py](chunking.py) | — | Monthly work units (no I/O) |

## The shared shape

The downloaders share a structure, and a new one should copy it:

1. `month_chunks(start, end)` slices the range into months (`chunking.py`).
2. `order_by_priority(...)` puts the years you care about first, so the
   highest-value data arrives in the first hour instead of the tenth.
3. Per (mailbox, month): check the checkpoint, skip if `done`, download, save
   the checkpoint.
4. An inaccessible mailbox is **skipped with a warning**, not fatal to the
   run. In a batch of 10 mailboxes, one missing delegation can't cost the
   other 9 hours of work.
5. A `reconfigure(encoding="utf-8", errors="replace")` block on `stdout` and
   `stderr` — without it, the Windows console crashes with
   `UnicodeEncodeError` on the first accented or emoji subject line.

## chunking.py — why months overlap

`MonthChunk` adds **a one-day margin on each side** of its query. Two
Gmail-specific reasons:

- `before:` is **exclusive**.
- `after:`/`before:` dates resolve in the **mailbox's timezone**, not UTC.

The overlap guarantees no gaps at month boundaries. The duplicates it
generates are intentional, and are absorbed by the `id`-based dedup in
`core.save_to_csv.merge_csv_files`. "Optimizing" by removing the margin
reintroduces silent gaps at month boundaries.

It's the only module in the package with no I/O and no network, and
consequently the best-tested
([../tests/test_chunking.py](../../../tests/test_chunking.py)).

## progress_report.py

The answer to "what's missing?" without reading logs. Walks checkpoints and
on-disk CSVs.

```text
mailbox                      labels  2024
                                     JFMAMJJASOND
user1@example.com            yes     ############
user2@example.com            yes     #####.......

  # complete   ! with failures   x error   . pending
```

Exit code `0` if everything is covered, `2` if work remains — useful for
chaining in a script.

## search_emails.py / build_search_index.py

The only two scripts in this package that **never touch the network**: they
read what has already been downloaded. The logic lives in
[../core/CLAUDE.md](../core/CLAUDE.md); this layer is `argparse` and formatting.

- **Building is all or nothing**: there is no incremental update of the index.
  After downloading new months, `--force` and rebuild.
- The default source is the **Parquet** if it exists, then the consolidated
  CSV, and as a last resort `output/` itself (which walks every per-day CSV).
- `search_emails.py` does not import pandas, not even by accident: that is
  0.7 s of start-up in a command run twenty times in a row. If you add
  something here, keep it that way.
- Date formatting and the conversion to local time happen **on display**, not
  on indexing (`--utc` switches both ends). See the time-zone note in
  `core/search_index.py`.
- `--for-payloads` is the bridge to phase 3: it writes `id,mailbox,date` for
  `gmail-bulk-export payloads --csv-file`. Its `date` is the `day` field — the
  day of the **folder** holding the per-day CSV, taken from `file_path` — not
  the message's UTC date: the two differ for about 1% of messages, and that
  column is what decides whether the downloader skips a body it already has.
