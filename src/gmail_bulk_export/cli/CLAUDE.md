# cli/

`gmail-bulk-export` subcommands: **one mailbox, one date range, no resumption**. This
is the low-level layer. For a real download across several mailboxes and
years, use [../scripts/](../scripts/CLAUDE.md), which imports these modules
and adds month-chunking and checkpoints on top.

Command and option reference: [../docs/CLI.md](../../../docs/CLI.md).

## Map

| File | What it does |
| --- | --- |
| [main.py](main.py) | `argparse`, subcommand dispatcher, config overrides |
| [bulk_emails_downloader.py](bulk_emails_downloader.py) | Phase 2: metadata. The canonical pattern for this repo |
| [gmail_payloads_downloader.py](gmail_payloads_downloader.py) | Phases 3 and 4: bodies and attachments |
| [gmail_labels_downloader.py](gmail_labels_downloader.py) | Phase 1: labels |

## The canonical pattern

`bulk_emails_downloader.fetch_email_metadata` is the flow to imitate when
adding any new download:

```text
fetch_email_metadata(username, date_range)
  └─ fetch_email_messages     lists ids, paginating (messages.list)
       └─ process_emails      ThreadPoolExecutor over the pages
            └─ _fetch_metadata_with_retries
                 └─ run_batch_requests          batch HTTP
                      └─ batch_email_response_handler   per-message callback
  └─ save_emails_to_csv       grouped by date, incremental merge
```

Points that matter:

- `pace()` before every request and `@gmail_retry` on whatever hits the
  network (both from [../core/rate_limit.py](../core/rate_limit.py)). No
  `time.sleep(random.uniform(...))`.
- `get_cached_gmail_service()`, not `get_gmail_service()`, inside threads.
- **The batch callback receives the error; `batch.execute()` doesn't raise
  it.** The signature is `(request_id, response, exception)` and `exception`
  has to be checked explicitly — otherwise the batch "finishes fine" with
  messages missing. Failures get retried up to 6 rounds with backoff, and
  whatever survives that goes to the dead-letter file
  `output/<mailbox>/.checkpoints/failed_metadata_<start>_<end>.jsonl`
  (`_dead_letter_path`, `_record_failures`, `_clear_dead_letter`).

## cmd_payloads and the per-mailbox fan-out

`retrieve_emails_data` accepts three id sources — `--csv-file`, a date range,
or `--email_ids` — and returns `(email_id, mailbox, date)` tuples. The
consolidated CSV mixes mailboxes, so `cmd_payloads` regroups by `mailbox`
before downloading: each mailbox has its own Gmail quota and its own
impersonated service.

`download_email_bodies` **skips any email whose `.jsonl.gz` already exists**,
so re-running the command is safe and cheap.

## Config overrides

`main.py` applies the global flags with `update_config()` **before**
dispatching the subcommand, and consumers resolve at the point of use:
`output_dir()`, `credentials_file()`, `get_config(...)`. That's why they work
today.

Two things not to undo:

- **Don't reintroduce `from config import OUTPUT_DIR`.** The constant no
  longer exists; it captured the value at import time, before argument
  parsing, and made `--output-dir` get accepted and silently ignored for
  months.
- **Don't put a duplicate flag on a subparser.** The `labels` subparser used
  to have its own `--output-dir`, and argparse applies a subparser's default
  *after* the main parser's — `--output-dir X labels` silently lost the X.

## The `attachments` subcommand no longer exists

It was removed along with `gmail_attachs_downloader.py`. It called
`service.users().get(...)` instead of `service.users().messages().get(...)`,
and saved flat files to `output/<mailbox>/attachments/<name>`, where two
same-named attachments from different emails overwrote each other.

Attachments are downloaded with
`python -m gmail_bulk_export.scripts.download_payloads --with-attachments`, which saves them
next to their message.

**Don't confuse this with
[../core/attachment_handler.py](../core/attachment_handler.py)**, which is
healthy and is what the payloads path actually uses: it filters by MIME type
and resolves destination paths.
