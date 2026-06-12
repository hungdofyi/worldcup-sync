-- Ratings prior + prediction model outputs. Run once with an OWNER
-- NEON_DATABASE_URL (update-predictions.py applies it idempotently).
-- Grants let the wc_sync role (GitHub Action) run the nightly job.

CREATE TABLE IF NOT EXISTS wc_team_ratings (
  team_code     text NOT NULL,
  snapshot_date date NOT NULL,
  elo           numeric(7,1) NOT NULL,
  fifa_rank     int,           -- optional garnish; Elo is the model input
  PRIMARY KEY (team_code, snapshot_date)
);

CREATE TABLE IF NOT EXISTS wc_match_predictions (
  match_num      int  NOT NULL,
  run_date       date NOT NULL,
  p_home         numeric(6,4) NOT NULL,
  p_draw         numeric(6,4) NOT NULL,
  p_away         numeric(6,4) NOT NULL,
  exp_home_goals numeric(5,2) NOT NULL,
  exp_away_goals numeric(5,2) NOT NULL,
  PRIMARY KEY (match_num, run_date)
);

CREATE TABLE IF NOT EXISTS wc_advance_probs (
  team_code  text NOT NULL,
  run_date   date NOT NULL,
  p_r32      numeric(6,4) NOT NULL,
  p_r16      numeric(6,4) NOT NULL,
  p_qf       numeric(6,4) NOT NULL,
  p_sf       numeric(6,4) NOT NULL,
  p_final    numeric(6,4) NOT NULL,
  p_champion numeric(6,4) NOT NULL,
  PRIMARY KEY (team_code, run_date)
);

GRANT SELECT, INSERT, UPDATE ON wc_team_ratings, wc_match_predictions, wc_advance_probs TO wc_sync;
