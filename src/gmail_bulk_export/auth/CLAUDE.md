# auth/

Authentication against Gmail via a **service account with domain-wide
delegation**. No interactive OAuth flow, no `token.json`: it reads
`client_secret.json` and impersonates each user with
`Credentials.with_subject(username)`.

Everything lives in [service.py](service.py) (~250 lines). Setup and common
errors: [../docs/AUTH.md](../../../docs/AUTH.md).

## API

| Function | When to use it |
| --- | --- |
| `get_cached_gmail_service(username)` | **Default.** Inside any loop or thread. |
| `get_gmail_service(username)` | Only if you need a guaranteed-fresh service. |
| `get_gmail_credentials(username)` | If you need the credentials, not the service. |
| `check_authentication(username)` | Pre-flight before a long download. |
| `AuthenticationError` | The module's single exception. |

## Why the cache is per-thread

`get_gmail_service` does a `creds.refresh()` (a network round trip) **plus** a
discovery build on every call, which is ruinous if invoked once per message.
Hence the cache.

But it's `threading.local()`, not a global dict, and that's deliberate: the
underlying `httplib2.Http` **isn't thread-safe**. Sharing a service across a
`ThreadPoolExecutor`'s threads corrupts responses under load — intermittent
parse errors, responses crossed between requests — a failure that doesn't
reproduce with a single thread. If you add another layer of caching, keep it
per-thread.

## Details

- **Scope**: `gmail.readonly`, constant `SCOPES`. Read-only on purpose;
  widening it requires re-authorizing in the Workspace Admin console.
- **gzip**: `build()` receives `requestBuilder=gzip_request_builder`, which
  adds `Accept-Encoding: gzip`. The bandwidth savings are large on bulk
  downloads and cost nothing.
- **`check_authentication` raises, it doesn't return `False`.** On invalid
  credentials it propagates `AuthenticationError`, so callers must catch the
  exception rather than check a return value. That's what
  `cli.main.verify_auth` does, and `scripts/download_metadata.py` uses it to
  **skip** an inaccessible mailbox and continue with the rest instead of
  aborting the whole run.

## When touching this module

A 403 has two very different causes that must not be treated the same way:
**quota** (transient, retryable) or **missing delegation** (permanent, should
fail immediately). That distinction lives in `core.rate_limit.is_retryable`,
looking at the `HttpError`'s `reason`; if you move error-handling logic here,
don't duplicate it.

The tests ([../tests/test_auth_check.py](../../../tests/test_auth_check.py)) check
signatures and interface, without hitting the network. Any test that needs
real credentials doesn't belong in this suite.
