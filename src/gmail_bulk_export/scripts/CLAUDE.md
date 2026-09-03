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
