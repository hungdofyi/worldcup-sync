"""One-time backfill: all finished World Cup editions (1930–2022) → Neon.

Source: the same public FIFA API the live sync uses — the seasons endpoint
covers every edition back to 1930 (verified 2026-06-12, incl. the 1930 final
URU 4-2 ARG). Loads wc_history_matches, wc_history_editions, wc_team_aliases
and derives wc_history_team_editions (per-edition team rollups + pedigree flags).

Usage:
  python backfill-history.py --dry-run   # fetch + derive + print verification, no DB
  python backfill-history.py             # needs an OWNER NEON_DATABASE_URL (DDL)

Idempotent: tables are CREATE IF NOT EXISTS, loads are upserts/replaces.
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

from fifa_client import TEAM_CODE_RE, fetch_season_calendar, fetch_seasons, loc, sane_name

SEASON_CURRENT = "285023"  # WC2026 lives in wc_matches, not history

# Raw FIFA stage names seen across eras → normalized stage. Unknown names load
# as 'other' (raw name is kept in stage_name); --dry-run prints every raw name.
STAGE_NORMALIZE = {
    "group matches": "group", "group stage": "group", "first stage": "group",
    "first round": "group", "preliminary round": "group",
    "second round": "group2", "second stage": "group2", "second group stage": "group2",
    "final round": "group2",  # 1950: decisive 4-team round-robin
    "round of 16": "r16", "eighth-final": "r16", "eighth-finals": "r16", "1/8 final": "r16",
    "1st round": "r16",  # 1938: straight-knockout opening round of 16

    "quarter-final": "qf", "quarter-finals": "qf",
    "semi-final": "sf", "semi-finals": "sf",
    "match for third place": "third_place", "third place play-off": "third_place",
    "play-off for third place": "third_place", "third-place match": "third_place",
    "final": "final",
}

# Defunct nations → FIFA's successor attribution (canonical join key toward
# wc_teams). NULL canonical = records stand alone (no successor).
DEFUNCT_CANONICAL = {
    "FRG": ("West Germany", "GER"),
    "GDR": ("East Germany", None),
    "URS": ("Soviet Union", "RUS"),
    "TCH": ("Czechoslovakia", "CZE"),
    "YUG": ("Yugoslavia", "SRB"),
    "SCG": ("Serbia and Montenegro", "SRB"),
    "ZAI": ("Zaire", "COD"),
    "INH": ("Dutch East Indies", "IDN"),
}

# Some editions return null ShortClubName for a few teams (seen: ISL/PAN in 2018).
NAME_FALLBACK = {"ISL": "Iceland", "PAN": "Panama"}

HOSTS = {
    1930: "URU", 1934: "ITA", 1938: "FRA", 1950: "BRA", 1954: "SUI", 1958: "SWE",
    1962: "CHI", 1966: "ENG", 1970: "MEX", 1974: "FRG", 1978: "ARG", 1982: "ESP",
    1986: "MEX", 1990: "ITA", 1994: "USA", 1998: "FRA", 2002: "KOR,JPN", 2006: "GER",
    2010: "RSA", 2014: "BRA", 2018: "RUS", 2022: "QAT",
}


def build() -> tuple[list, list, dict]:
    """Fetch every finished edition → (match rows, edition rows, code→display name)."""
    matches, editions, names = [], [], {}
    for season in sorted(fetch_seasons(), key=lambda s: loc(s.get("Name"), "")):
        sid = str(season["IdSeason"])
        if sid == SEASON_CURRENT:
            continue
        name = sane_name((loc(season.get("Name")) or "").replace("™", ""), "season name") or sid
        year = int(re.search(r"(19|20)\d{2}", name).group(0))
        editions.append({"year": year, "season_id": sid, "name": name,
                         "host_codes": HOSTS.get(year)})
        for m in fetch_season_calendar(sid):
            row = {"fifa_match_id": str(m["IdMatch"]), "year": year, "season_id": sid,
                   "match_num": m.get("MatchNumber"), "match_date": m.get("Date"),
                   "group_name": loc(m.get("GroupName")), "result_type": m.get("ResultType"),
                   "attendance": m.get("Attendance"),
                   "venue": sane_name(loc((m.get("Stadium") or {}).get("Name")), "venue"),
                   "city": sane_name(loc((m.get("Stadium") or {}).get("CityName")), "city"),
                   "home_score": m.get("HomeTeamScore"), "away_score": m.get("AwayTeamScore"),
                   "home_pen": m.get("HomeTeamPenaltyScore"), "away_pen": m.get("AwayTeamPenaltyScore")}
            raw_stage = loc(m.get("StageName")) or ""
            row["stage_name"] = raw_stage
            row["stage"] = STAGE_NORMALIZE.get(raw_stage.strip().lower(), "other")
            id2code = {}
            for side in ("Home", "Away"):
                team = m.get(side) or {}
                code = team.get("Abbreviation")
                if code and not TEAM_CODE_RE.match(code):
                    raise ValueError(f"suspicious team code: {code!r}")
                row[f"{side.lower()}_code"] = code
                row[f"{side.lower()}_name"] = sane_name(loc(team.get("ShortClubName")), "team name")
                if code:
                    names[code] = (names.get(code) or row[f"{side.lower()}_name"]
                                   or NAME_FALLBACK.get(code) or code)
                    id2code[str(team.get("IdTeam"))] = code
            # Winner is the FIFA IdTeam (not the code) — resolve via this row's teams;
            # fall back to scores+pens (covers any edition where Winner is absent).
            row["winner_code"] = id2code.get(str(m.get("Winner")))
            if row["winner_code"] is None and row["home_score"] is not None:
                h = (row["home_score"], row["home_pen"] or 0)
                a = (row["away_score"], row["away_pen"] or 0)
                if h != a:
                    row["winner_code"] = row["home_code"] if h > a else row["away_code"]
            matches.append(row)
        print(f"  {year}: {sum(1 for x in matches if x['season_id'] == sid)} matches", file=sys.stderr)
    return matches, editions, names


def derive_team_editions(matches: list) -> list:
    """Per (year, team): W/D/L/goals (shootout counts as a draw) + pedigree flags."""
    acc = defaultdict(lambda: {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0,
                               "in_semi": False, "in_final": False, "champion": False})
    finals = {}  # year → final match (for champion derivation)
    for m in matches:
        if m["home_code"] is None or m["away_code"] is None or m["home_score"] is None:
            continue
        for code, gf, ga in ((m["home_code"], m["home_score"], m["away_score"]),
                             (m["away_code"], m["away_score"], m["home_score"])):
            t = acc[(m["year"], code)]
            t["played"] += 1
            t["gf"] += gf
            t["ga"] += ga
            t["won" if gf > ga else "lost" if gf < ga else "drawn"] += 1
            if m["stage"] in ("sf", "third_place"):
                t["in_semi"] = True
            if m["stage"] == "final":
                t["in_semi"] = t["in_final"] = True
        if m["stage"] == "final":
            finals[m["year"]] = m
    # Champion: winner of the 'final' match; editions without one (1950's decisive
    # round-robin) fall back to the chronologically last match of the edition.
    last_by_year = {}
    for m in matches:
        if m["home_score"] is None:
            continue
        if m["year"] not in last_by_year or m["match_date"] > last_by_year[m["year"]]["match_date"]:
            last_by_year[m["year"]] = m
    for year, decider in last_by_year.items():
        decider = finals.get(year, decider)
        champ = decider["winner_code"]
        if champ:
            acc[(year, champ)]["champion"] = True
            if year not in finals:  # round-robin finish: deciding-match sides count as finalists
                for code in (decider["home_code"], decider["away_code"]):
                    acc[(year, code)]["in_semi"] = acc[(year, code)]["in_final"] = True
    return [{"year": y, "team_code": c, **v} for (y, c), v in sorted(acc.items())]


def build_aliases(names: dict) -> list:
    return [{"alias_code": code,
             "display_name": DEFUNCT_CANONICAL[code][0] if code in DEFUNCT_CANONICAL else name,
             "canonical_code": DEFUNCT_CANONICAL[code][1] if code in DEFUNCT_CANONICAL else code}
            for code, name in sorted(names.items())]


def verify(matches, editions, team_editions, aliases) -> None:
    print(f"\n{len(editions)} editions · {len(matches)} matches · "
          f"{len(team_editions)} team-editions · {len(aliases)} alias codes")
    print("\nraw stage names → normalized:")
    seen = defaultdict(set)
    for m in matches:
        seen[(m["stage_name"], m["stage"])].add(m["year"])
    for (raw, norm), years in sorted(seen.items(), key=lambda kv: min(kv[1])):
        flag = "  ⚠ UNMAPPED" if norm == "other" else ""
        print(f"  {raw!r} → {norm} ({min(years)}–{max(years)}){flag}")
    print("\nderived champions (verify against the known list):")
    for te in team_editions:
        if te["champion"]:
            print(f"  {te['year']}: {te['team_code']}")
    defunct = [a for a in aliases if a["alias_code"] in DEFUNCT_CANONICAL]
    print(f"\ndefunct mapped: {[a['alias_code'] for a in defunct]}")


def load(matches, editions, team_editions, aliases) -> None:
    from db import get_conn
    ddl = Path(__file__).resolve().parent / "schema-history.sql"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(ddl.read_text())
        cur.executemany(
            """INSERT INTO wc_history_matches AS h (fifa_match_id, year, season_id, match_num,
                 match_date, stage, stage_name, group_name, home_code, home_name, away_code,
                 away_name, home_score, away_score, home_pen, away_pen, winner_code,
                 result_type, attendance, venue, city)
               VALUES (%(fifa_match_id)s, %(year)s, %(season_id)s, %(match_num)s, %(match_date)s,
                 %(stage)s, %(stage_name)s, %(group_name)s, %(home_code)s, %(home_name)s,
                 %(away_code)s, %(away_name)s, %(home_score)s, %(away_score)s, %(home_pen)s,
                 %(away_pen)s, %(winner_code)s, %(result_type)s, %(attendance)s, %(venue)s, %(city)s)
               ON CONFLICT (fifa_match_id) DO UPDATE SET home_score = EXCLUDED.home_score,
                 away_score = EXCLUDED.away_score, home_pen = EXCLUDED.home_pen,
                 away_pen = EXCLUDED.away_pen, winner_code = EXCLUDED.winner_code,
                 stage = EXCLUDED.stage, stage_name = EXCLUDED.stage_name,
                 attendance = EXCLUDED.attendance""",
            matches)
        cur.executemany(
            """INSERT INTO wc_history_editions (year, season_id, name, host_codes)
               VALUES (%(year)s, %(season_id)s, %(name)s, %(host_codes)s)
               ON CONFLICT (year) DO UPDATE SET name = EXCLUDED.name,
                 host_codes = EXCLUDED.host_codes""",
            editions)
        cur.executemany(
            """INSERT INTO wc_team_aliases (alias_code, display_name, canonical_code)
               VALUES (%(alias_code)s, %(display_name)s, %(canonical_code)s)
               ON CONFLICT (alias_code) DO UPDATE SET display_name = EXCLUDED.display_name,
                 canonical_code = EXCLUDED.canonical_code""",
            aliases)
        cur.execute("TRUNCATE wc_history_team_editions")  # full re-derive, source of truth above
        cur.executemany(
            """INSERT INTO wc_history_team_editions (year, team_code, played, won, drawn,
                 lost, gf, ga, in_semi, in_final, champion)
               VALUES (%(year)s, %(team_code)s, %(played)s, %(won)s, %(drawn)s, %(lost)s,
                 %(gf)s, %(ga)s, %(in_semi)s, %(in_final)s, %(champion)s)""",
            team_editions)
    print("\nloaded to Neon. Remember: grant SELECT on the 4 new tables to the "
          "Holistics reporting role if default privileges don't already cover it.")


def main() -> None:
    dry = "--dry-run" in sys.argv
    print("fetching all editions…", file=sys.stderr)
    matches, editions, names = build()
    team_editions = derive_team_editions(matches)
    aliases = build_aliases(names)
    verify(matches, editions, team_editions, aliases)
    if dry:
        print("\n--dry-run: no DB writes.")
        return
    load(matches, editions, team_editions, aliases)


if __name__ == "__main__":
    main()
