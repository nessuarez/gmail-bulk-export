# `gmail-bulk-export` CLI reference

```bash
uv run gmail-bulk-export [global options] <command> [command options]
uv run python -m cli.main <command> ...   # equivalent, without relying on the entry point
```

The examples below drop the `uv run` prefix for brevity; add it back, or
activate the environment with `uv sync` and `.venv\Scripts\Activate.ps1`.

> **These subcommands operate on one mailbox and one date range, with no
> checkpoints and no resumption.** For a real download across several
> mailboxes and years, use the `scripts/` orchestrators — see
> [RUNBOOK.md](RUNBOOK.md). This reference is for one-off runs and debugging.

## Global options

| Option | Effect |
| --- | --- |
| `-v, --verbose` | DEBUG-level logging on the console and in `logs/log_<UUID>.txt` |
| `--credentials-file` | Path to the service-account key |
| `--batch-size`, `--payload-batch-size`, `--max-workers` | Config overrides |
| `--output-dir` | Output root ([detail](CONFIG.md#redirecting-the-output)) |

All download commands verify credentials before starting ([AUTH.md](AUTH.md)).

## `metadata`

Headers: sender, recipients, subject, labels, dates.

```bash
gmail-bulk-export metadata --username user@example.com --start_date 20230101 --end_date 20230131
```

| Option | |
| --- | --- |
| `--username` | required |
| `--start_date` | required, `YYYYMMDD` format |
| `--end_date` | required, `YYYYMMDD` format |

Output: `output/<mailbox>/<YYYY-MM-DD>/<YYYY-MM-DD>.csv`, one CSV per day. The
schema is defined by `METADATA_FIELDNAMES` in
[../core/save_to_csv.py](../core/save_to_csv.py) — check it there instead of
trusting a list copied into a document.

## `payloads`

Bodies (plain text and HTML) and, optionally, the attachment binaries.

```bash
gmail-bulk-export payloads --username user@example.com --csv-file output/emails_with_mailboxes.csv
gmail-bulk-export payloads --username user@example.com --start_date 20230101 --end_date 20230131
gmail-bulk-export payloads --username user@example.com --email_ids abc123 def456
```

| Option | |
| --- | --- |
| `--csv-file` | CSV with ids to download; can mix mailboxes |
| `--start_date` / `--end_date` | Range alternative |
| `--email_ids` | Individual ids |
| `--allowed_attachment_types` | MIME types to keep (defaults to PDF, images, and office documents) |
| `--with-attachments` | Also saves the binaries (much more disk) |

Output: `output/<mailbox>/<YYYY-MM-DD>/<msg_id>.jsonl.gz`, plus
`<msg_id>_attachments.gz` with `--with-attachments`.

Each body contains `id`, `date`, `from`, `to`, `subject`, `content_type`,
`body_plain`, `body_html`, `attachments` (name, type, and size), and
`attachments_with_content`. **The attachment listing already travels inside
the body**: you don't need `--with-attachments` to know what attachments an
email has, only to get the actual bytes.

An email whose `.jsonl.gz` already exists is skipped, so repeating the
command is safe.

## `labels`

```bash
gmail-bulk-export labels --username user@example.com
```

| Option | |
| --- | --- |
| `--username` | required |
| `--label-filename` | defaults to `labels` |

To change the destination, use the **global** `--output-dir`, before the
subcommand: `gmail-bulk-export --output-dir ./other labels --username ...`. `labels`
used to have its own flag that silently overrode the global one; it was
removed.

Output: `output/<mailbox>/labels.csv`. If you consolidate a mailbox without
its labels, anything that translates `labelIds` to names will fail.

## `token`

```bash
gmail-bulk-export token
```

Checks that the credentials file exists and is a valid service-account JSON.
**It doesn't generate any file** — the name is inherited from when the
project used interactive OAuth. It also doesn't validate delegation; for
that, use `scripts.download_metadata`'s `--dry-run`
([AUTH.md](AUTH.md#4-verify)).

## `attachments` — removed

An `attachments` subcommand used to exist that called
`service.users().get(...)` instead of `service.users().messages().get(...)`
and saved flat files to `output/<mailbox>/attachments/<name>`, where two
same-named attachments from different emails **overwrote each other**. It was
removed.

Attachments are downloaded like this, saved next to their message:

```bash
python -m scripts.download_payloads --year 2024 --with-attachments
```

## Exit codes

`0` success, `1` error (check `logs/`).

## VS Code

`.vscode/launch.json` **isn't versioned** (it's caught by the `*.json` rule,
and often contains real addresses in its arguments). If you're working in
this repo, create your own with one entry per subcommand — `metadata`,
`payloads` by CSV and by range, `labels`, `token` — and debug with F5.

## Common issues

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: No module named 'google.auth'` | `uv sync`, or launch the command with `uv run` |
| `AuthenticationError` | [AUTH.md § Common errors](AUTH.md#common-errors) |
| `429 Too many concurrent requests` | Lower `max_workers` ([CONFIG.md](CONFIG.md#batch_size--max_workers-is-the-number-that-matters)) |
| Nothing downloads | Repeat with `-v` and check the range and mailbox; logs are in `logs/` |
| `UnicodeEncodeError` on the console | If it's your own script, it's missing the `reconfigure(encoding="utf-8")` block |
