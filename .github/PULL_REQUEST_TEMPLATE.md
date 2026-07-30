## What this changes and why

## Checklist

- [ ] `uv run pytest` passes locally
- [ ] `uv run pre-commit run --all-files` passes locally (ruff format + lint)
- [ ] New behavior has test coverage; no test touches the network
- [ ] No organization-specific assumptions baked into generic code (see
      [CONTRIBUTING.md](../CONTRIBUTING.md) on scope)
- [ ] If this touches `config.output_dir()` / `credentials_file()` resolution
      or module-level imports, it doesn't reintroduce the
      import-time-constant bug described in
      [CLAUDE.md § Invariants](../CLAUDE.md#invariants-do-not-break-these)
