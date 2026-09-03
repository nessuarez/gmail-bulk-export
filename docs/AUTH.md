# Authentication

This project uses a **service account with domain-wide delegation** and user
impersonation. There is no interactive OAuth flow, no browser ever opens, and
**no `token.json` is generated**: `client_secret.json` is read on every
startup, and each mailbox is impersonated with
`Credentials.with_subject(username)`.

Implementation: [../auth/service.py](../src/gmail_bulk_export/auth/service.py).

## Why there's no OAuth consent screen

If you've followed a typical Gmail API tutorial before, you probably expect a
browser window asking a user to click "Allow." This project intentionally
doesn't do that.

**Domain-wide delegation** is a different model, available only in Google
Workspace: an admin authorizes a service account *once*, for the whole
domain, to impersonate any user for a specific scope. After that one-time
setup, the service account can act as any mailbox in the domain without that
mailbox's owner clicking anything — which is exactly what makes bulk,
unattended, multi-mailbox export possible. It's also why this only works for
Workspace-managed mail, never for a personal `@gmail.com` account: there's no
domain admin to grant that authorization on a personal account.

## Setup

### 1. Create the service account and its key

You need **Service Account Admin** (or a broader role like Editor/Owner that
includes it) on the Google Cloud project. Google Cloud Console → *IAM &
Admin* → *Service Accounts* → create account → *Keys* → *Add key* → *JSON*.
Save the downloaded file as `client_secret.json` in the repo root.

The JSON must contain `"type": "service_account"`, `private_key`, and
`client_email`. If the file has `"installed"` or `"web"` at the top level,
you downloaded OAuth client credentials instead, which **won't work** here.

### 2. Enable domain-wide delegation

In the same console, enable *domain-wide delegation* for the service account
and copy its **Client ID**.

### 3. Authorize the scope in Workspace

Google Admin Console → *Security* → *API Controls* → *Domain-wide Delegation*
→ add the Client ID from the previous step with the scope:

```text
https://www.googleapis.com/auth/gmail.readonly
```

Read-only, on purpose. Widening the scope means re-authorizing here.

### 4. Verify

```bash
python -m gmail_bulk_export.cli.main token
```

Verifies that `client_secret.json` exists and is a valid service-account
JSON. **It does not check delegation** — for that you need to actually try
impersonating a real mailbox:

```bash
python -m gmail_bulk_export.scripts.download_metadata --mailboxes-file mailboxes.txt \
    --start 2024-01 --end 2024-01 --dry-run
```

Prints a `✓` or `✗` per mailbox. This is the check that actually validates
the whole setup end to end.

## Using it from code

```python
from auth.service import get_cached_gmail_service

service = get_cached_gmail_service("user@example.com")
res = service.users().messages().list(userId="me", q="after:2024/01/01").execute()
```

Always use the `cached` variant inside loops or threads: `get_gmail_service`
does a network `refresh()` plus a discovery build on every call. The cache is
**per-thread** because the underlying `httplib2.Http` isn't thread-safe
(detail in [../auth/CLAUDE.md](../src/gmail_bulk_export/auth/CLAUDE.md)).

## Pre-flight check before downloading

`check_authentication(username)` is the pre-flight the `gmail-bulk-export`
subcommands run before starting. It surfaces a credentials problem in
milliseconds instead of half an hour into a download.

**It never returns `False`**: on invalid credentials it raises
`AuthenticationError`. Catch the exception, don't check a return value:

```python
from auth.service import check_authentication, AuthenticationError

try:
    check_authentication("user@example.com")
except AuthenticationError as e:
    ...
```

The `scripts/` orchestrators use it to **skip** an inaccessible mailbox and
continue with the rest: in a batch of 10 mailboxes, one missing delegation
shouldn't cost the other 9 hours of work.

## Common errors

| Message | Cause | Fix |
| --- | --- | --- |
| `Service account file not found: client_secret.json` | Missing file, or `CREDENTIALS_FILE` points elsewhere | Download it from GCP Console and save it in the repo root |
| `File does not appear to be a service account JSON` | You downloaded OAuth client credentials | Create a service-account *key*, not a client ID |
| `Failed to load or refresh...: unauthorized_client` | Missing Client ID in the Workspace console, or scope mismatch | Repeat step 3; the scope must be exactly `gmail.readonly` |
| `Failed to load or refresh...` for one mailbox | That user doesn't exist, or is outside the service account's domain | Check the address; the orchestrator skips it and continues |
| Sporadic `403`s during download | Quota, not permissions | Transient, and `gmail_retry` handles it. If constant, lower `max_workers` |

A 403 has two very different causes: **quota** (transient, retried) or
**missing delegation** (permanent, fails immediately). The distinction is in
`core.rate_limit.is_retryable`, which looks at the `HttpError`'s `reason`.

## Diagnostics

```bash
python -m gmail_bulk_export.cli.main -v token
```

`-v` raises logging to DEBUG on the console and in `logs/log_<UUID>.txt`.
Look for `✓ Credentials verified for user:` to confirm impersonation worked.

## Security

`client_secret.json` is a private key that grants read access to the mail of
**the entire domain**. It's in [.gitignore](../.gitignore); check that it
stays there before committing. If it leaks, revoke it in GCP Console —
rotating the key alone isn't enough if the Client ID is still authorized in
Workspace.
