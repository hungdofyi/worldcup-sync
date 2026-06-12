-- World Cup history backfill schema (editions 1930–2022).
-- Run once by backfill-history.py with an OWNER/admin NEON_DATABASE_URL —
-- the wc_sync role used by the live GitHub Action has no DDL rights.
-- After creating, grant the Holistics reporting role SELECT on these tables
-- if it is not already covered by default privileges.

CREATE TABLE IF NOT EXISTS wc_history_matches (
  fifa_match_id text PRIMARY KEY,
  year          int  NOT NULL,
  season_id     text NOT NULL,
  match_num     int,
  match_date    timestamptz,
  stage         text,   -- normalized: group / group2 / r16 / qf / sf / third_place / final / other
  stage_name    text,   -- raw FIFA stage name (kept verbatim; formats vary by era)
  group_name    text,
  home_code     text,
  home_name     text,
  away_code     text,
  away_name     text,
  home_score    int,
  away_score    int,
  home_pen      int,
  away_pen      int,
  winner_code   text,   -- FIFA Winner field (covers shootouts and replay outcomes)
  result_type   int,
  attendance    int,
  venue         text,
  city          text
);

CREATE INDEX IF NOT EXISTS idx_wc_history_matches_year  ON wc_history_matches (year);
CREATE INDEX IF NOT EXISTS idx_wc_history_matches_home  ON wc_history_matches (home_code);
CREATE INDEX IF NOT EXISTS idx_wc_history_matches_away  ON wc_history_matches (away_code);

CREATE TABLE IF NOT EXISTS wc_history_editions (
  year       int  PRIMARY KEY,
  season_id  text NOT NULL,
  name       text,
  host_codes text  -- comma-separated host nation codes (curated)
);

-- Every team code that appears in history, mapped to its modern successor where
-- FIFA attributes records that way (FRG→GER, URS→RUS, ...). canonical_code is the
-- join key toward wc_teams; NULL means no successor (e.g. East Germany).
CREATE TABLE IF NOT EXISTS wc_team_aliases (
  alias_code     text PRIMARY KEY,
  display_name   text,
  canonical_code text
);

-- Per (edition, team) rollup derived from wc_history_matches. W/D/L use the
-- statistical convention: a shootout is a draw; advancement flags carry pedigree.
CREATE TABLE IF NOT EXISTS wc_history_team_editions (
  year      int  NOT NULL,
  team_code text NOT NULL,
  played    int, won int, drawn int, lost int,
  gf        int, ga int,
  in_semi   boolean,
  in_final  boolean,
  champion  boolean,
  PRIMARY KEY (year, team_code)
);
