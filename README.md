# worldcup-sync

Scheduled runner for the World Cup 2026 dashboard warehouse (FIFA API → Neon).
**Deploy artifact only** — canonical source lives in the Holistics showcase repo
under `Heo Sao Mai/worldcup/etl/`; edit there and re-copy.

Single secret: `NEON_SYNC_URL` — a DML-only Postgres role (`wc_sync`,
SELECT/INSERT/UPDATE on `wc_*` tables only; no DDL, no DELETE).

Runs every 15 min during tournament match windows; also runnable manually via
the Actions tab (workflow_dispatch) or locally: `python sync-live.py` with
`NEON_DATABASE_URL` set.
