"""Gmail Bulk Export — a resumable engine for exporting Gmail at scale.

The package is laid out in four layers:

* `auth`   — service-account impersonation and the per-thread service cache.
* `core`   — rate limiting, retries, checkpoints, CSV/gzip I/O, the search index.
* `cli`    — one mailbox, one date range, no resumption (`gmail-bulk-export`).
* `scripts`— the orchestrators: N mailboxes x N months, with checkpoints.

Nothing here is imported at package level on purpose: `config` reads
`config.json` from the working directory the moment it is imported, and every
module wants to pay that cost explicitly.
"""

__version__ = "0.2.0"
