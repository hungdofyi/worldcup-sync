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

## Ratings + predictions (`update-predictions.py`)

Nightly job (predictions.yml, also workflow_dispatch): rolls our own Elo
(eloratings.net methodology in `elo.py`) over the martj42 all-internationals
dataset (topped up with FT results from wc_matches the dataset hasn't ingested
yet), snapshots the 48 teams into `wc_team_ratings`, writes per-match outcome
probabilities (`wc_match_predictions`) from the fitted Elo→Poisson model in
`match_model.py`, and Monte-Carlos the remaining tournament 10k times into
`wc_advance_probs`. Every row carries `run_date` → odds movement is queryable.

```
python update-predictions.py --init               # first run, OWNER url (DDL + grants)
python update-predictions.py --backfill-ratings   # adds monthly Elo snapshots since 2024-07
python update-predictions.py                      # nightly (wc_sync role suffices)
python backtest-2022.py                           # calibration backtest on Qatar 2022
```

Model acceptance is enforced by `backtest-2022.py` (honest protocol: params
fitted strictly pre-Qatar, ratings rolled sequentially): multi-class Brier
0.626 vs 0.667 uniform; predicted draw rate 23.5% vs realized 23.4%; favorites
calibrated within sample noise (the ~90% bucket lost both matches — those were
Argentina–Saudi Arabia-class shocks, n=2).
