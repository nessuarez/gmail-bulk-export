# Contributing

Thanks for considering a contribution. This project originated as the
extracted, generic engine of a private internal tool, so the scope here is
intentionally narrow: **Gmail bulk export mechanics** — authentication,
checkpointing, rate limiting, CSV/Parquet output. Business-logic-shaped
contributions (classification rules, domain-specific scoring, anything that
only makes sense for one organization's workflow) aren't a fit for this
repo — that kind of logic belongs in a downstream project that depends on
this one, the same way this project itself was split out of one.

## Setup

```bash
uv sync                              # creates .venv from uv.lock
uv run pytest                        # run the test suite
uv run pre-commit run --all-files    # lint + format (ruff)
```

Read [CLAUDE.md](CLAUDE.md) before making non-trivial changes — it documents
the architecture and, more importantly, the invariants that already cost a
debugging session once each. The per-directory `CLAUDE.md` files
(`auth/`, `cli/`, `core/`, `scripts/`, `tests/`) have area-specific detail.

## Ground rules

- **Run modules with `python -m`**, e.g. `python -m gmail_bulk_export.core.load_metadatas`, not
  `python core/load_metadatas.py`.
- **No test touches the network.** If something needs Gmail to be tested,
  extract the logic into a pure function first — see
  [tests/CLAUDE.md](tests/CLAUDE.md).
- **Don't reintroduce `from config import OUTPUT_DIR`** or use
  `config.output_dir()`/`config.credentials_file()` as a function's default
  parameter value. Both bugs already shipped once; see
  [CLAUDE.md § Invariants](CLAUDE.md#invariants-do-not-break-these).
- Formatting and linting are **ruff only**, 100-char lines — `pre-commit run
  --all-files` before you push.

## Opening a pull request

1. Fork the repo and branch off `main`.
2. Make your change, with tests for new behavior.
3. `uv run pytest` and `uv run pre-commit run --all-files` locally.
4. Open a PR against `main`. CI runs the same checks (pytest across
   Python 3.10–3.13, lint, and a wheel-install smoke test) — it needs to be
   green before review.

Small, focused PRs are much easier to review than large ones bundling
several unrelated changes.

## Reporting bugs

Open an issue with your OS, Python version, uv version, and whether you're
using the only supported auth mode (service account + domain-wide
delegation) — most confusing issues trace back to that setup step. See
[docs/AUTH.md](docs/AUTH.md) if you're not sure.

## Security issues

Don't open a public issue for a security vulnerability — see
[SECURITY.md](SECURITY.md).
