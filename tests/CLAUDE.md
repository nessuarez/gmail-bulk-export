# tests/

```bash
pytest                                  # everything, with coverage over auth/ cli/ core/ scripts/
pytest tests/test_chunking.py -v        # a single file
pytest -k chunking                      # by name
pytest --no-cov                         # no coverage, faster while iterating
```

`testpaths = ["tests"]` in [../pyproject.toml](../pyproject.toml): only this
directory is collected. There is no `conftest.py`.

## Rule: no test touches the network

There are no credentials in CI or in the default dev environment, and a test
that depends on Gmail is a test nobody runs. Two ways this is kept true:

- **Extract the logic into pure functions.** It's why `scripts/chunking.py`
  is separated from the modules that call the API — it's the
  best-covered module in the repo, and not by accident.
- **`tmp_path` for anything that writes to disk.** The dominant pattern: build
  a fake day directory, run the merge, check the result. Never write to a
  real `output/` from a test.

`test_auth_check.py` checks signatures and interface with `inspect`, without
authenticating.

## What each file covers

| Test | Protects |
| --- | --- |
| `test_merge_incremental.py` | Deduplication, idempotency, concurrent merges, new columns landing on old CSVs |
| `test_chunking.py` | Monthly chunking, boundary overlap, priority ordering |
| `test_load_metadatas.py` | Day-CSV discovery, consolidation, the optional `--also-parquet` export |
| `test_data_transforms.py` | Idempotency of the shared dataframe transforms |
| `test_checkpoints.py` | Saving/loading/clearing resumption state |
| `test_save_to_csv*.py`, `test_csv_handler.py`, `test_file_handler.py`, `test_attachment_handler.py`, `test_utils.py` | I/O and helpers |
| `test_no_domain_leaks.py` | That the repo stays generic and in English |

## Cases you must keep

These tests aren't ceremony: each one pins down a behaviour that already
broke once. If one of these gets in the way of a refactor, the problem is the
refactor.

- **`test_repeated_merges_are_idempotent`** — chunks overlap on purpose, so
  re-merging the same data happens on every normal run.
- **`test_concurrent_merges_do_not_lose_rows`** — several threads routinely
  land on the same date; without the per-file lock, rows get silently lost.
- **`test_new_column_survives_an_older_day_csv`** — adding a field to
  `METADATA_FIELDNAMES` must not force re-downloading months already fetched.
- **Idempotency in `test_data_transforms.py`** — downstream consumers may
  apply the same transform more than once.
- **`test_no_domain_leaks.py`** — this engine was extracted from a private,
  Spanish-language internal tool. The tests scan the whole tree for that
  tool's business vocabulary, for non-ASCII in `.py` files, and for date
  defaults that pin one deployment's backfill window. Two fixtures keep
  accented text on purpose (`Résumé` exercises RFC 2047, `Zürich` the FTS5
  diacritic folding); they are listed in `INTENTIONAL_NON_ASCII`. The
  conventions being enforced are in [../CLAUDE.md](../CLAUDE.md).

## When adding tests

Cover the pure logic first. If testing something requires mocking the Gmail
service, that's usually a sign the logic wants to move into a network-free
function — the same way `scripts/chunking.py` was split out.
