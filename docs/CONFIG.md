# Configuration

A layered system. Priority, highest to lowest:

1. Command-line arguments — **with important exceptions, see below**
2. Environment variables (`.env`)
3. [`config.json`](../config.json)
4. Defaults in [`config.py`](../src/gmail_bulk_export/config.py)

## Values

| Key | `config.json` | Environment variable | What it's for |
| --- | --- | --- | --- |
| `output_directory` | `./output` | `OUTPUT_DIR` | Root of all downloaded data |
| `credentials_filename` | — | `CREDENTIALS_FILE` | The service account's key |
| `log_dir` | `logs` | `LOG_DIR` | Where logs go |
| `batch_size` | `25` | `BATCH_SIZE` | Sub-requests per HTTP batch (max 100) |
| `payload_batch_size` | `20` | `PAYLOAD_BATCH_SIZE` | Same, for bodies |
| `max_workers` | `2` | `MAX_WORKERS` | Concurrent threads per mailbox |
| `messages_per_second` | `40` | `MESSAGES_PER_SECOND` | Target rate for the token bucket |
| `detailed_labels` | see below | `DETAILED_LABELS` | Labels to request message/thread counts for |
| `retry_attempts` | `8` | `RETRY_ATTEMPTS` | Retries on transient failures |
| `retry_wait_min` | `4` | `RETRY_WAIT_MIN` | Minimum backoff wait (s) |
| `retry_wait_max` | `64` | `RETRY_WAIT_MAX` | Backoff ceiling (s) |

> `config.py`'s defaults (`batch_size` 100, `max_workers` 5) are the old,
> **aggressive** ones. The good values, already tuned against the real API,
> are `config.json`'s. If you're wondering which one is active, look at
> `config.json`.

## `batch_size` × `max_workers` is the number that matters

Gmail limits **concurrent** requests per user, separately from the per-second
quota. It's not the same as the rate:

| Configuration | Measured result |
| --- | --- |
| `100 × 5` | Mass `429 Too many concurrent requests` — over a third of a month's messages failed |
| `25 × 2` | ~37-42 msg/s sustained, zero failures |

On 429s, lower **`max_workers`** first. Raising `batch_size` doesn't
compensate.

Reference quota: 250 units per user per second; `messages.list` and
`messages.get` cost 5 each → a theoretical ceiling of ~50 msg/s per mailbox.
And 1,200,000 units per minute per project.

**The quota is per user, not per credential.** Adding API keys doesn't speed
anything up; parallelizing across mailboxes does.

## `detailed_labels`

Phase 1 lists all of a mailbox's labels at once, but message/thread counts
have to be requested **one request per label**. This list says which ones are
worth spending that on.

The default is just Gmail's standard labels — `INBOX`, `SENT`, `UNREAD`,
`CHAT`. Any label of your own goes here rather than in the code.
Add them per deployment:

```json
"detailed_labels": ["INBOX", "SENT", "UNREAD", "CHAT", "MY_CUSTOM_LABEL"]
```

```bash
DETAILED_LABELS="INBOX,SENT,MY_CUSTOM_LABEL" python -m gmail_bulk_export.cli.main labels --username ...
```

A label that doesn't exist in a mailbox is **silently skipped** (at DEBUG
level). It used to be requested anyway with `id=None`, which spent a request
just to fail and logged a misleading error; with a configurable list, absence
is the normal case, not the exception.

## `.env`

Copy [`.env.example`](../.env.example), which ships with every key commented:

```bash
cp .env.example .env
```

It's **optional**: `config.json` is already versioned with tuned values. Use
`.env` only to deviate from them on your machine. Don't version it.

## Redirecting the output

All three paths work and apply in this order:

```bash
gmail-bulk-export --output-dir /other/path metadata --username ...   # flag
OUTPUT_DIR=/other/path python -m gmail_bulk_export.scripts.download_metadata ... # environment variable
```

`--output-dir` used to be **accepted and silently ignored** for a long time:
the modules did `from config import OUTPUT_DIR`, which captures the value at
import time, before `main.py` had processed the arguments. Only the
environment variable arrived in time.

The path is now resolved by calling `config.output_dir()` at the point of
use, and the constant no longer exists, so a careless import fails instead of
lying. If you write new code, don't use it as a parameter's default value:
defaults are evaluated once, at function-definition time, and that recreates
the exact same bug.

The same applies to `--credentials-file`, which had the same defect and is
now resolved with `config.credentials_file()`.
