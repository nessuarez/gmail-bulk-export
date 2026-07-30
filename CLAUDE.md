# CLAUDE.md

CLI toolkit to bulk-export Gmail — several mailboxes, years of history — via a
service account with domain-wide delegation.

The repo is built for **long, incremental, resumable** runs. Almost every
unusual decision in the code traces back to that; each one is explained in
[Invariants](#invariants-do-not-break-these).

## Quick orientation

| If you're going to... | Start with |
| --- | --- |
| Run a download | [docs/RUNBOOK.md](docs/RUNBOOK.md) — the real operational guide |
| Touch authentication | [auth/CLAUDE.md](auth/CLAUDE.md) |
| Touch a `gmail-bulk-export` subcommand | [cli/CLAUDE.md](cli/CLAUDE.md) |
| Touch I/O, checkpoints, rate limiting, CSV schema | [core/CLAUDE.md](core/CLAUDE.md) |
| Touch multi-mailbox orchestration | [scripts/CLAUDE.md](scripts/CLAUDE.md) |
| Write or fix tests | [tests/CLAUDE.md](tests/CLAUDE.md) |
| Configure credentials or `.env` | [docs/AUTH.md](docs/AUTH.md), [docs/CONFIG.md](docs/CONFIG.md) |

## Architecture

```text
                    auth/service.py  ──►  Gmail API
                           │
      ┌────────────────────┴────────────────────┐
      │                                          │
   cli/  (one mailbox, one range)         scripts/ (N mailboxes, N months,
   gmail-bulk-export subcommands                          with checkpoints)
      │                                          │
      └────────────────┬─────────────────────────┘
                        │
                     core/  (rate limit, retries, checkpoints, CSV, gzip)
                        │
                     output/
```

**`cli/` is the low-level layer, `scripts/` orchestrates it.**
`scripts/download_metadata.py` imports `cli.bulk_emails_downloader` and calls
it month by month, in-process. For a real download always use `scripts/`; the
standalone `gmail-bulk-export` subcommands have no checkpoints and no resumption.

### The 4 download phases

```text
1. labels    → output/<mailbox>/labels.csv
2. metadata  → output/<mailbox>/<YYYY-MM-DD>/<YYYY-MM-DD>.csv   (phases 1 and 2 in one command)
   ↓ consolidation: python -m core.load_metadatas [--also-parquet]
   output/emails_with_mailboxes.csv (+ .parquet)
3. bodies    → output/<mailbox>/<YYYY-MM-DD>/<msg_id>.jsonl.gz
4. attachments → output/<mailbox>/<YYYY-MM-DD>/<msg_id>_attachments.gz
```

Full detail, options, and resumption: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Commands

The package manager is **uv**. `uv run` resolves the environment on its own —
nothing to activate or install up front.

```bash
uv sync                     # creates .venv from uv.lock

uv run pytest
uv run pytest tests/test_chunking.py -v   # a single file
uv run pytest -k chunking                 # by name

uv run ruff format . && uv run ruff check --fix .
uv run pre-commit run --all-files

uv run python -m cli.main token           # check credentials
```

When changing dependencies, edit `pyproject.toml` and run `uv lock`; never
install by hand into the environment. `uv.lock` is versioned and is the
source of truth for versions.

## Invariants (do not break these)

Each one cost a long debugging session. They're written down so it doesn't
happen again.

1. **Run modules with `python -m`**, never `python core/load_metadatas.py`.
   The modules do `from config import output_dir`, which only resolves with
   the repo root on `sys.path`.

2. **The output path is resolved by calling `config.output_dir()`, never by
   importing a constant.** There is no `config.OUTPUT_DIR`: it was removed on
   purpose so that a stray `from config import OUTPUT_DIR` fails immediately
   instead of silently capturing the pre-argument-parsing value — which is
   exactly what made `--output-dir` get accepted and silently ignored. Same
   for `credentials_file()`. **Never use either as a function parameter's
   default value**: a default is evaluated once, at function-definition time,
   and reintroduces the same bug. Use `None` and resolve inside the function
   ([tests/test_output_dir_override.py](tests/test_output_dir_override.py)
   pins this down).

3. **`batch_size` × `max_workers` is the number that matters.** Gmail limits
   *concurrent* requests per user separately from the per-second quota. With
   `100 × 5` you get mass `429 Too many concurrent requests`; with `25 × 2` you
   sustain ~37-42 msg/s with no failures. On 429s, lower `max_workers` first.
   The tuned values live in [config.json](config.json), not in config.py's
   defaults (which are the old, aggressive ones).

4. **The Gmail service is cached per thread, not globally**
   ([auth/service.py](auth/service.py) → `get_cached_gmail_service`). The
   underlying `httplib2.Http` isn't thread-safe: sharing one service across a
   `ThreadPoolExecutor`'s threads corrupts responses under load.

5. **A failure inside a batch does not raise.** Gmail delivers the error to the
   `(request_id, response, exception)` callback, so `batch.execute()` finishes
   "fine" with messages missing. Every callback's `exception` has to be
   inspected, and whatever fails gets routed to a retry queue → dead-letters
   at `output/<mailbox>/.checkpoints/failed_metadata_*.jsonl`.

6. **Month chunks deliberately overlap by one day on each side.** Gmail's
   `before:` is **exclusive** and dates resolve in the mailbox's timezone. The
   overlap guarantees no gaps at month boundaries; the resulting duplicates
   are absorbed by `id`-based dedup in `core.save_to_csv.merge_csv_files`.

7. **A chunk is only marked `done` with zero failures.** If any failed it stays
   `partial`, and the next run retries the whole chunk. Don't relax this: it's
   what makes "resume" mean something.

8. **`labelIds` in the CSVs is a Python list repr, not JSON.** Parse it with
   `ast.literal_eval`, never `json.loads`
   ([data_transforms.py](data_transforms.py) → `transform_list_columns`). The
   Parquet copy produced by `--also-parquet` stores it as a real list instead.

9. **`internalDate` is epoch milliseconds** and is the reliable date field. The
   `date` column is the raw `Date` header and doesn't always parse.

10. **The transforms in [data_transforms.py](data_transforms.py) are
    idempotent.** Anything downstream may apply the same transform twice;
    reapplying one must be a no-op, not destroy the column.

## Conventions

- **Logging**: always `from config_logger import get_app_logger`. A single
  `bulk_gmail` logger with a file handler in `logs/`. Don't use `print` for
  diagnostics — only for the final human-facing summary.
- **Retries**: decorate network calls with `gmail_retry` from
  [core/rate_limit.py](core/rate_limit.py) and call `pace()` before every
  request. Retry **only** transient failures (429, 5xx, 403-by-quota); a 404
  or a permissions 403 should fail immediately instead of burning 8 retries.
  Don't add `time.sleep(random.uniform(...))`: that's exactly what the token
  bucket exists to replace.
- **CSV schema**: the source of truth is `METADATA_FIELDNAMES` in
  [core/save_to_csv.py](core/save_to_csv.py). Adding a field there is enough
  for it to also show up in days downloaded before the field existed
  (`merge_csv_files` unions the columns found on disk with the schema).
- **New scripts**: copy the `reconfigure(encoding="utf-8")` block over
  `stdout`/`stderr` that everything in `scripts/` carries, or the Windows
  console will crash with `UnicodeEncodeError` the moment a subject line has
  an accent or emoji in it.
- Python ≥3.10 in `pyproject.toml`; development runs on 3.13. Formatting and
  linting: **ruff only**, 100-char lines. `ruff format` replaces black, and
  the `I` rules replace isort — running both in parallel used to deadlock
  commits, since each formatter kept undoing what the other had just written.

## Data that must never leave this repo

Covered by [.gitignore](.gitignore): `client_secret.json` (and any stray
`*.json`), `.env`, `output/`, `logs/`, and `mailboxes*.txt` (except the
`.example` template).

Check with `git check-ignore -v <file>` before adding any data file.
