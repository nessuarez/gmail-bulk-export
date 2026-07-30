# Security Policy

## What's sensitive here

This tool handles a **service-account key with domain-wide delegation**
(`client_secret.json`). That key can impersonate any mailbox in the Google
Workspace domain it's authorized for and read the full content of every
email in it. Treat any issue touching credential handling, impersonation, or
output-path resolution as security-relevant, not just a bug.

A few things worth knowing up front, already documented in
[docs/AUTH.md](docs/AUTH.md#security):

- `client_secret.json` must never be committed. It's covered by
  [.gitignore](.gitignore) — verify it stays there before pushing.
- If the key leaks, **revoke it** in Google Cloud Console. Rotating it alone
  isn't enough while the Client ID is still authorized for domain-wide
  delegation in the Workspace Admin console.
- The Gmail scope this project requests is `gmail.readonly` — intentionally
  read-only. A PR that widens the requested scope needs a clear justification.

## Reporting a vulnerability

Please **don't** open a public issue for a security vulnerability. Instead:

- Use [GitHub's private security advisory feature](../../security/advisories/new)
  for this repository, or
- Email `nestor.suarez.alfonso@gmail.com` with details.

Include what you found, how to reproduce it, and its potential impact. We'll
acknowledge reports as soon as we can and work with you on a fix and
disclosure timeline.

## Scope

This policy covers the code in this repository. It does not cover
vulnerabilities in the Gmail API itself, in Google Cloud/Workspace, or in
third-party dependencies — please report those directly to Google or the
relevant maintainer.
