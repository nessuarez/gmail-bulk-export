<!-- Instructions for AI agents working in this repo -->

# Instructions for agents

**The reference documentation is [`CLAUDE.md`](../CLAUDE.md) at the repo
root.** Read it before touching code: it has the architecture map, the
commands, and — most importantly — the invariants you must not break.

Each package also has its own file with that area's pitfalls:

| Area | File |
| --- | --- |
| Gmail authentication | [`auth/CLAUDE.md`](../src/gmail_bulk_export/auth/CLAUDE.md) |
| `gmail-bulk-export` subcommands | [`cli/CLAUDE.md`](../src/gmail_bulk_export/cli/CLAUDE.md) |
| Rate limiting, checkpoints, CSV | [`core/CLAUDE.md`](../src/gmail_bulk_export/core/CLAUDE.md) |
| Multi-mailbox orchestration | [`scripts/CLAUDE.md`](../src/gmail_bulk_export/scripts/CLAUDE.md) |
| Tests | [`tests/CLAUDE.md`](../tests/CLAUDE.md) |

Operational download guide: [`docs/RUNBOOK.md`](../docs/RUNBOOK.md).

## The minimum you need to know

- **`scripts/` is what actually gets run**; `cli/` is the low-level layer it
  orchestrates. The standalone subcommands have no checkpoints and no
  resumption.
- **Run modules with `python -m`.** The `config` and `core` imports need the
  repo root on `sys.path`.
- **Every network call**: `pace()` before it, `@gmail_retry` around it, both
  from `core/rate_limit.py`. Never `time.sleep(random.uniform(...))` — the
  token bucket exists precisely to have eliminated that.
- **Inside threads**: `get_cached_gmail_service()`, cached per-thread because
  `httplib2.Http` isn't thread-safe.
- **A batch's errors arrive at the callback**, not as an exception from
  `batch.execute()`. Ignoring them means a month can finish "fine" with
  messages silently missing.
- **The output path is resolved with `config.output_dir()`**, never by
  importing a constant. `config.OUTPUT_DIR` doesn't exist: it was removed so
  a stray import fails instead of lying. And never as a parameter's default
  value — defaults are evaluated once.
- **No test touches the network.** If testing something needs mocking Gmail,
  that logic probably wants to move out into a pure function.

The reasoning behind each point is in the linked files above. Don't duplicate
those explanations here: this file is an index, not a copy.
