# Gmail Bulk Export

A CLI toolkit to export Gmail **in bulk** — several mailboxes, years of
history — using a Google service account with domain-wide delegation, and
turn the result into plain CSV (or Parquet) for analysis.

It's built for long-running exports: the download is split into one-month
chunks per mailbox, each with its own checkpoint, so an interruption costs at
most the current month, and re-running the same command picks up where it
left off.

- Metadata, bodies, attachments, and labels, as independent phases
- Resumable: per (mailbox, month) checkpoints and dedup by message `id`
- A token bucket tuned to Gmail's quota, retrying only transient failures
- Dead-letters: whatever fails is recorded, not silently dropped
- A coverage report that tells you what's missing without reading logs

## Is this for you?

- You need to export mail from mailboxes you (or your organization)
  administer in **Google Workspace** — not personal `@gmail.com` accounts.
  Domain-wide delegation is a Workspace-only feature.
- You're comfortable with a command line and a `.json` service-account key.
- You need more than one mailbox, or more than "the last year" — for a single
  personal mailbox, [Google Takeout](https://takeout.google.com/) is simpler
  and requires none of this setup.
- **Before pointing this at mailboxes that aren't only your own: read
  [docs/LEGAL_FAQ.md](docs/LEGAL_FAQ.md).** This tool can read the full
  content of every email in every mailbox you target, without the mailbox
  owner seeing or approving it in the moment. That's exactly what
  domain-wide delegation is for, but it also means you're taking on real
  responsibilities before you run it against anyone else's mail.

## Install

The project uses [uv](https://docs.astral.sh/uv/):

```bash
uv sync                  # creates .venv from uv.lock, with dev dependencies
uv run gmail-bulk-export --help # uv resolves the environment on its own; no activation needed
```

Don't have uv? `winget install astral-sh.uv` on Windows, or
`curl -LsSf https://astral.sh/uv/install.sh | sh` on Unix.

Requires Python ≥3.10 (developed on 3.13). uv downloads the interpreter for
you if you don't have one.

## Credentials

You need a **service account with domain-wide delegation** and the
`https://www.googleapis.com/auth/gmail.readonly` scope authorized in the
Google Workspace Admin console. Download its JSON key and save it as
`client_secret.json` in the repo root.

Step-by-step: [docs/AUTH.md](docs/AUTH.md).

```bash
uv run python -m cli.main token       # does the credentials file exist and look valid?
```

## Quickstart (one mailbox, one month)

The fastest way to see it work, before setting up the full multi-mailbox
pipeline:

```bash
uv run gmail-bulk-export labels --username user@example.com
uv run gmail-bulk-export metadata --username user@example.com \
    --start_date 20240101 --end_date 20240131
```

This writes `output/user@example.com/labels.csv` and
`output/user@example.com/2024-01-*/2024-01-*.csv`. It has **no checkpoints or
resumption** — it's meant for a first look and for debugging, not for a real
export. Full command reference: [docs/CLI.md](docs/CLI.md).

## Real, incremental, multi-mailbox usage

```bash
# one mailbox per line
echo "user@example.com" > mailboxes.txt

# phases 1 and 2: labels + metadata
uv run python -m scripts.download_metadata --mailboxes-file mailboxes.txt \
    --start 2016-01 --end 2025-12 --priority 2024,2023

# what's left?
uv run python -m scripts.progress_report

# consolidate (add --also-parquet for a typed copy alongside the CSV)
uv run python -m core.load_metadatas --mailboxes-file mailboxes.txt --also-parquet

# phase 3: bodies (phase 4: add --with-attachments)
uv run python -m scripts.download_payloads --year 2024
```

Re-running any of these commands resumes; it doesn't re-download what's
already complete.

**The full operational guide is [docs/RUNBOOK.md](docs/RUNBOOK.md)**: options,
resumption, running in the background, dead-letters, and known issues.

## Structure

```text
auth/      Gmail authentication (service account + impersonation)
cli/       gmail-bulk-export subcommands: one mailbox, one range
core/      rate limiting, checkpoints, CSV schema
scripts/   multi-mailbox orchestration with checkpoints  ← what you actually run
tests/     pytest; no test touches the network
docs/      auth, CLI, config, legal FAQ, analysis guide
```

Each directory has its own `CLAUDE.md` with that area's invariants and
pitfalls. The overall map is in [CLAUDE.md](CLAUDE.md).

## Output

```text
output/
├── emails_with_mailboxes.csv        # consolidated, the input to analysis
├── emails_with_mailboxes.parquet    # optional typed copy (--also-parquet)
└── <mailbox>/
    ├── labels.csv
    ├── .checkpoints/                # resumption state and dead-letters
    └── <YYYY-MM-DD>/
        ├── <YYYY-MM-DD>.csv         # that day's metadata
        ├── <msg_id>.jsonl.gz        # body
        └── <msg_id>_attachments.gz  # attachments
```

## What to do with the exported data

[docs/ANALYSIS.md](docs/ANALYSIS.md) covers a few concrete options beyond
plain pandas for querying a large export — DuckDB over the Parquet copy,
SQLite full-text search over subjects, and a data-quality pass before you
trust a big backfill.

## Development

```bash
uv run pytest                        # coverage over auth/ cli/ core/ scripts/
uv run pre-commit run --all-files    # ruff (format, lint, imports), 100-char lines
```

Every push runs the same in CI, across Python 3.10–3.13, plus a check that
the wheel installs and imports from a clean environment. See
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

## Notice

Not affiliated with Google. `client_secret.json` grants read access to the
entire domain's mail: don't version it, and revoke the key immediately if it
leaks. Use this tool in accordance with Gmail's Terms of Service and whatever
data-protection law applies to you — see
[docs/LEGAL_FAQ.md](docs/LEGAL_FAQ.md) before running this against mail that
isn't only your own.

## License

MIT — see [LICENSE](LICENSE).
