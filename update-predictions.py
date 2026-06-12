"""Nightly ratings + predictions job (Elo prior → match probs → Monte Carlo).

1. Refresh the all-internationals dataset (martj42) and roll Elo to today,
   topping up with any FT result in wc_matches the dataset hasn't ingested yet.
2. Snapshot today's Elo for the 48 teams into wc_team_ratings.
3. Fit the Elo→goals model on its fixed pre-Qatar window (validated by
   backtest-2022.py) and write per-match probabilities for upcoming matches.
4. Simulate the remaining tournament (default 10k runs) → wc_advance_probs.

run_date is kept on every output row — daily history of odds movement is free.

Usage:
  python update-predictions.py --init               # first run, OWNER url (DDL+grants)
  python update-predictions.py                      # nightly (wc_sync role suffices)
  python update-predictions.py --backfill-ratings   # monthly Elo snapshots since 2024-07
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

from db import get_conn
from elo import (BASE_RATING, RESULTS_LOCAL, RESULTS_URL, apply_match,
                 load_results, ratings_as_of)
from match_model import fit_parameters, outcome_probs
from tournament_sim import HOSTS, Simulator, host_adv

# wc_teams.name → martj42 dataset name, where they differ
CSV_NAME_OVERRIDES = {
    "Côte d'Ivoire": "Ivory Coast", "Congo DR": "DR Congo", "Cabo Verde": "Cape Verde",
    "Czechia": "Czech Republic", "IR Iran": "Iran", "Korea Republic": "South Korea",
    "Türkiye": "Turkey", "USA": "United States",
}
RATINGS_BACKFILL_START = date(2024, 7, 1)


def refresh_results() -> None:
    RESULTS_LOCAL.parent.mkdir(exist_ok=True)
    resp = requests.get(RESULTS_URL, timeout=60)
    resp.raise_for_status()
    RESULTS_LOCAL.write_bytes(resp.content)


def fetch_db(cur) -> tuple[dict, list]:
    cur.execute("SELECT code, name FROM wc_teams")
    teams = dict(cur.fetchall())
    cur.execute("""SELECT match_num, stage, group_code, home_code, away_code,
                          home_slot, away_slot, home_score, away_score,
                          home_pen, away_pen, status, kickoff_utc
                   FROM wc_matches ORDER BY match_num""")
    cols = [c.name for c in cur.description]
    fixtures = [dict(zip(cols, r)) for r in cur.fetchall()]
    return teams, fixtures


def code_to_csv_name(teams: dict, csv_names: set) -> dict:
    mapping = {}
    for code, name in teams.items():
        cand = CSV_NAME_OVERRIDES.get(name, name)
        if cand not in csv_names:
            raise SystemExit(f"no dataset name for {code} ({name!r}) — extend CSV_NAME_OVERRIDES")
        mapping[code] = cand
    return mapping


def current_elo(results: list, fixtures: list, code2name: dict) -> dict[str, float]:
    """Full dataset roll + top-up with FT wc_matches results the CSV lacks."""
    ratings: dict[str, float] = {}
    seen = set()
    for m in results:
        apply_match(ratings, m)
        seen.add((m["date"], m["home"], m["away"]))
    topped = 0
    for f in fixtures:
        if f["status"] != "FT" or not f["home_code"]:
            continue
        key = (f["kickoff_utc"].date(), code2name[f["home_code"]], code2name[f["away_code"]])
        if key in seen:
            continue
        apply_match(ratings, {
            "home": key[1], "away": key[2], "hs": f["home_score"], "as": f["away_score"],
            "tournament": "FIFA World Cup", "neutral": f["home_code"] not in HOSTS,
        })
        topped += 1
    print(f"elo: rolled {len(results)} dataset matches, topped up {topped} from wc_matches")
    return ratings


def main() -> None:
    init = "--init" in sys.argv
    backfill = "--backfill-ratings" in sys.argv
    sims = 10000
    today = date.today()

    refresh_results()
    results = load_results()
    csv_names = {m["home"] for m in results} | {m["away"] for m in results}

    with get_conn() as conn, conn.cursor() as cur:
        if init:
            cur.execute((Path(__file__).resolve().parent / "schema-ratings.sql").read_text())
            print("schema applied")
        teams, fixtures = fetch_db(cur)
        code2name = code_to_csv_name(teams, csv_names)
        name2code = {v: k for k, v in code2name.items()}

        ratings_by_name = current_elo(results, fixtures, code2name)
        elo_by_code = {c: ratings_by_name.get(n, BASE_RATING) for c, n in code2name.items()}

        snap_rows = [{"team_code": c, "snapshot_date": today, "elo": round(v, 1)}
                     for c, v in elo_by_code.items()]
        if backfill:
            d = RATINGS_BACKFILL_START
            while d < today:
                hist = ratings_as_of(results, d)
                snap_rows += [{"team_code": c, "snapshot_date": d,
                               "elo": round(hist.get(n, BASE_RATING), 1)}
                              for c, n in code2name.items()]
                d = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        cur.executemany(
            """INSERT INTO wc_team_ratings (team_code, snapshot_date, elo)
               VALUES (%(team_code)s, %(snapshot_date)s, %(elo)s)
               ON CONFLICT (team_code, snapshot_date) DO UPDATE SET elo = EXCLUDED.elo""",
            snap_rows)
        print(f"ratings: {len(snap_rows)} snapshot rows")

        params = fit_parameters(results)
        pred_rows = []
        for f in fixtures:
            if f["status"] == "FT" or not f["home_code"] or not f["away_code"]:
                continue
            d = (elo_by_code.get(f["home_code"], BASE_RATING)
                 - elo_by_code.get(f["away_code"], BASE_RATING)
                 + host_adv(f["home_code"], f["away_code"]))
            ph, pd_, pa, lh, la = outcome_probs(d, params)
            pred_rows.append({"match_num": f["match_num"], "run_date": today,
                              "p_home": round(ph, 4), "p_draw": round(pd_, 4),
                              "p_away": round(pa, 4), "exp_home_goals": round(lh, 2),
                              "exp_away_goals": round(la, 2)})
        cur.executemany(
            """INSERT INTO wc_match_predictions (match_num, run_date, p_home, p_draw,
                 p_away, exp_home_goals, exp_away_goals)
               VALUES (%(match_num)s, %(run_date)s, %(p_home)s, %(p_draw)s, %(p_away)s,
                 %(exp_home_goals)s, %(exp_away_goals)s)
               ON CONFLICT (match_num, run_date) DO UPDATE SET p_home = EXCLUDED.p_home,
                 p_draw = EXCLUDED.p_draw, p_away = EXCLUDED.p_away,
                 exp_home_goals = EXCLUDED.exp_home_goals,
                 exp_away_goals = EXCLUDED.exp_away_goals""",
            pred_rows)
        print(f"match predictions: {len(pred_rows)} upcoming matches")

        probs = Simulator(fixtures, elo_by_code, params).run(sims)
        adv_rows = [{"team_code": t, "run_date": today,
                     **{k: round(v, 4) for k, v in p.items()}}
                    for t, p in probs.items()]
        cur.executemany(
            """INSERT INTO wc_advance_probs (team_code, run_date, p_r32, p_r16, p_qf,
                 p_sf, p_final, p_champion)
               VALUES (%(team_code)s, %(run_date)s, %(p_r32)s, %(p_r16)s, %(p_qf)s,
                 %(p_sf)s, %(p_final)s, %(p_champion)s)
               ON CONFLICT (team_code, run_date) DO UPDATE SET p_r32 = EXCLUDED.p_r32,
                 p_r16 = EXCLUDED.p_r16, p_qf = EXCLUDED.p_qf, p_sf = EXCLUDED.p_sf,
                 p_final = EXCLUDED.p_final, p_champion = EXCLUDED.p_champion""",
            adv_rows)
        top = sorted(probs.items(), key=lambda kv: -kv[1]["p_champion"])[:8]
        print(f"advance probs: {len(adv_rows)} teams over {sims} sims; title favorites:")
        for t, p in top:
            print(f"  {t}: {p['p_champion']:.1%} title, {p['p_r32']:.0%} advance")

        # Use the dataset's own name set as a tripwire for silent renames.
        unknown = [n for n in name2code if n not in csv_names]
        if unknown:
            print(f"WARNING: names missing from dataset: {unknown}")


if __name__ == "__main__":
    main()
