# worldcup-sync

ETL for the World Cup 2026 dashboard warehouse (FIFA API → Neon Postgres).
This repo is the canonical source for all ETL code.

## Live sync (`sync-live.py`)

Runs every 15 min during tournament match windows via GitHub Actions; also
runnable manually via the Actions tab (workflow_dispatch) or locally:
`python sync-live.py` with `NEON_DATABASE_URL` set.

Single secret: `NEON_SYNC_URL` — a DML-only Postgres role (`wc_sync`,
SELECT/INSERT/UPDATE on `wc_*` tables only; no DDL, no DELETE).

## History backfill (`backfill-history.py`)

One-time load of every finished World Cup edition (1930–2022, 964 matches)
from the same FIFA API into `wc_history_matches`, `wc_history_editions`,
`wc_team_aliases`, and the derived `wc_history_team_editions` (per-edition
team rollups + pedigree flags). Schema in `schema-history.sql`.

```
python backfill-history.py --dry-run   # fetch + verify, no DB writes
python backfill-history.py             # load (needs an OWNER NEON_DATABASE_URL — DDL)
```

The `--dry-run` output prints derived champions per edition and every raw
stage name with its normalization — eyeball both before loading. The wc_sync
role cannot run this (no DDL); use an owner connection string, then grant the
Holistics reporting role SELECT on the four new tables if default privileges
don't cover them.
