---
name: Bug report
about: Something isn't working as expected
title: ""
labels: bug
---

**What happened**

A clear description of what went wrong, and what you expected instead.

**Environment**

- OS:
- Python version (`python --version`):
- uv version (`uv --version`):
- Are you using the only supported auth mode — service account with
  domain-wide delegation (see [docs/AUTH.md](../../docs/AUTH.md))? If not,
  this is very likely the cause; most confusing issues trace back to auth
  setup.

**Steps to reproduce**

```bash
# the command(s) you ran
```

**Relevant log output**

Run with `-v` for DEBUG logging (console + `logs/log_<UUID>.txt`), and paste
the relevant part here. Redact any real mailbox addresses, message ids, or
subject lines before pasting.

**Anything else**
