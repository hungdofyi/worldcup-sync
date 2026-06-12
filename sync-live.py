"""Live sync: FIFA API → Neon. Idempotent; safe to run any time, any cadence.

Pass 1 (1 request): calendar → scores/status/kickoff/knockout-pairing updates
for all 104 matches (UPDATE by match_num, never insert-new).
Pass 2 (2 requests per target match): detail + timeline → players, lineups
(starters with pitch coords), formations, possession, attendance, events.
Targets: LIVE matches, finished matches with no events yet, and matches
kicking off within 75 minutes (FIFA publishes lineups ~1h before kickoff).
"""
import sys
from datetime import datetime, timedelta, timezone

from db import get_conn
from fifa_client import (
    STATUS_BY_FIFA, TEAM_CODE_RE,
    fetch_calendar, fetch_match_detail, fetch_timeline, loc,
    normalize_caps_name, sane_name,
)

PREMATCH_WINDOW = timedelta(minutes=75)


def upsert_calendar(cur) -> int:
    rows = []
    for m in fetch_calendar():
        codes, id2code = {}, {}
        for side in ("Home", "Away"):
            team = m.get(side)
            code = team.get("Abbreviation") if team and team.get("IdTeam") else None
            if code and not TEAM_CODE_RE.match(code):
                raise ValueError(f"suspicious team code: {code!r}")
            codes[side] = code
            if code:
                id2code[str(team.get("IdTeam"))] = code
        # Winner holds the FIFA IdTeam, not the team code — resolve via this row's teams.
        winner = id2code.get(str(m.get("Winner")))
        rows.append({
            "match_num": int(m["MatchNumber"]),
            "kickoff_utc": m["Date"],
            "home_code": codes["Home"], "away_code": codes["Away"],
            "home_score": m.get("HomeTeamScore"), "away_score": m.get("AwayTeamScore"),
            "home_pen": m.get("HomeTeamPenaltyScore"), "away_pen": m.get("AwayTeamPenaltyScore"),
            "status": STATUS_BY_FIFA.get(m.get("MatchStatus"), "NS"),
            "fifa_status": m.get("MatchStatus"),
            "winner_code": winner if winner and TEAM_CODE_RE.match(str(winner)) else None,
            "attendance": m.get("Attendance"),
        })
    cur.executemany(
        """UPDATE wc_matches SET kickoff_utc = %(kickoff_utc)s,
             home_code = COALESCE(%(home_code)s, home_code),
             away_code = COALESCE(%(away_code)s, away_code),
             home_score = %(home_score)s, away_score = %(away_score)s,
             home_pen = %(home_pen)s, away_pen = %(away_pen)s,
             status = %(status)s, fifa_status = %(fifa_status)s,
             winner_code = COALESCE(%(winner_code)s, winner_code),
             attendance = COALESCE(%(attendance)s, attendance),
             updated_at = now()
           WHERE match_num = %(match_num)s""",
        rows,
    )
    return len(rows)


def pick_detail_targets(cur) -> list[tuple]:
    # FT matches stay targets while their event stream disagrees with the scoreboard
    # (fewer goal events than goals scored, or no events at all). Event upserts are
    # idempotent, so partial ingests — e.g. a sync outage mid-match — self-heal on the
    # next run. The 48h bound stops a match with an unmapped goal type code from being
    # re-fetched for the rest of the tournament.
    now = datetime.now(timezone.utc)
    cur.execute(
        """SELECT m.match_num, m.fifa_stage_id, m.fifa_match_id, m.status, m.kickoff_utc
           FROM wc_matches m
           WHERE m.status = 'LIVE'
              OR (m.status = 'FT'
                  AND m.kickoff_utc > now() - interval '48 hours'
                  AND ((SELECT count(*) FROM wc_events e
                         WHERE e.match_num = m.match_num AND e.type IN (0, 41))
                       < COALESCE(m.home_score, 0) + COALESCE(m.away_score, 0)
                       OR NOT EXISTS
                         (SELECT 1 FROM wc_events e WHERE e.match_num = m.match_num)))
              OR (m.status = 'NS' AND m.kickoff_utc BETWEEN %s AND %s)
           ORDER BY m.kickoff_utc""",
        (now - timedelta(minutes=15), now + PREMATCH_WINDOW),
    )
    return cur.fetchall()


def team_code_by_fifa_id(cur) -> dict[str, str]:
    cur.execute("SELECT fifa_team_id::text, code FROM wc_teams")
    return dict(cur.fetchall())


def roster_names(cur) -> dict[str, str]:
    cur.execute("SELECT fifa_player_id, name FROM wc_players")
    return dict(cur.fetchall())


def event_text(e: dict, names: dict[str, str]):
    """Compose ticker text from roster full names. FIFA's EventDescription
    abbreviates players ("H G OH (in) comes off the bench to replace H M SON
    (out) (Korea Republic)"); IdPlayer/IdSubPlayer resolve to full squad names
    (IdPlayer = scorer/carded/incoming, IdSubPlayer = outgoing — verified on
    match 2). Falls back to the normalized FIFA sentence when ids don't
    resolve (own goals, unknown types, pre-squad events)."""
    kind = e["Type"]
    player = names.get(str(e.get("IdPlayer")))
    sub_out = names.get(str(e.get("IdSubPlayer")))
    if kind == 0 and player:
        return f"{player} scores!!"
    if kind == 41 and player:
        return f"{player} scores from the spot!!"
    if kind == 2 and player:
        return f"{player} is booked."
    if kind == 3 and player:
        return f"{player} is sent off!"
    if kind == 5 and player and sub_out:
        return f"{player} on for {sub_out}"
    return normalize_caps_name(loc(e.get("EventDescription")))


def sync_detail(cur, match_num: int, stage_id: str, match_id: str, id2code: dict) -> None:
    detail = fetch_match_detail(stage_id, match_id)
    formations, possession = {}, detail.get("BallPossession") or {}
    for side in ("HomeTeam", "AwayTeam"):
        team = detail.get(side) or {}
        code = id2code.get(str(team.get("IdTeam")))
        if not code:
            continue  # pairing not resolved yet (pre-knockout)
        formations[side] = team.get("Tactics")
        players, lineups = [], []
        for p in team.get("Players") or []:
            players.append({
                "fifa_player_id": p["IdPlayer"], "team_code": code,
                "name": normalize_caps_name(sane_name(loc(p.get("PlayerName")), "player name")) or "Unknown",
                "shirt_number": p.get("ShirtNumber"), "position": p.get("Position"),
                "picture_url": (p.get("PlayerPicture") or {}).get("PictureUrl"),
            })
            lineups.append({
                "match_num": match_num, "fifa_player_id": p["IdPlayer"], "team_code": code,
                "is_starter": p.get("Status") == 1,  # verified: 11 starters, coords set
                "is_captain": bool(p.get("Captain")),
                "pitch_x": p.get("LineupX"), "pitch_y": p.get("LineupY"),
            })
        cur.executemany(
            """INSERT INTO wc_players (fifa_player_id, team_code, name, shirt_number, position, picture_url)
               VALUES (%(fifa_player_id)s, %(team_code)s, %(name)s, %(shirt_number)s, %(position)s, %(picture_url)s)
               ON CONFLICT (fifa_player_id) DO UPDATE SET team_code = EXCLUDED.team_code,
                 name = EXCLUDED.name, shirt_number = EXCLUDED.shirt_number,
                 position = EXCLUDED.position, picture_url = EXCLUDED.picture_url""",
            players,
        )
        cur.executemany(
            """INSERT INTO wc_lineups (match_num, fifa_player_id, team_code, is_starter, is_captain, pitch_x, pitch_y)
               VALUES (%(match_num)s, %(fifa_player_id)s, %(team_code)s, %(is_starter)s, %(is_captain)s, %(pitch_x)s, %(pitch_y)s)
               ON CONFLICT (match_num, fifa_player_id) DO UPDATE SET is_starter = EXCLUDED.is_starter,
                 is_captain = EXCLUDED.is_captain, pitch_x = EXCLUDED.pitch_x, pitch_y = EXCLUDED.pitch_y""",
            lineups,
        )
    cur.execute(
        """UPDATE wc_matches SET home_formation = %s, away_formation = %s,
             possession_home = %s, possession_away = %s,
             attendance = COALESCE(%s, attendance), updated_at = now()
           WHERE match_num = %s""",
        (formations.get("HomeTeam"), formations.get("AwayTeam"),
         possession.get("OverallHome"), possession.get("OverallAway"),
         detail.get("Attendance"), match_num),
    )

    events = (fetch_timeline(stage_id, match_id).get("Event") or [])
    names = roster_names(cur)  # full squad names just upserted above (same transaction)
    cur.executemany(
        """INSERT INTO wc_events (event_id, match_num, team_code, fifa_player_id, type,
             type_name, match_minute, period, ts, home_goals, away_goals, description)
           VALUES (%(event_id)s, %(match_num)s, %(team_code)s, %(fifa_player_id)s, %(type)s,
             %(type_name)s, %(match_minute)s, %(period)s, %(ts)s, %(home_goals)s, %(away_goals)s, %(description)s)
           ON CONFLICT (event_id) DO UPDATE SET team_code = EXCLUDED.team_code,
             fifa_player_id = EXCLUDED.fifa_player_id, type_name = EXCLUDED.type_name,
             match_minute = EXCLUDED.match_minute, period = EXCLUDED.period, ts = EXCLUDED.ts,
             home_goals = EXCLUDED.home_goals, away_goals = EXCLUDED.away_goals,
             description = EXCLUDED.description""",
        [{
            "event_id": e["EventId"], "match_num": match_num,
            "team_code": id2code.get(str(e.get("IdTeam"))),
            "fifa_player_id": e.get("IdPlayer"),
            "type": e["Type"], "type_name": loc(e.get("TypeLocalized")),
            "match_minute": e.get("MatchMinute"), "period": e.get("Period"),
            "ts": e.get("Timestamp"),
            "home_goals": e.get("HomeGoals"), "away_goals": e.get("AwayGoals"),
            "description": event_text(e, names),
        } for e in events if e.get("EventId")],
    )


def main() -> None:
    with get_conn() as conn, conn.cursor() as cur:  # single transaction (all-or-nothing)
        n = upsert_calendar(cur)
        targets = pick_detail_targets(cur)
        id2code = team_code_by_fifa_id(cur) if targets else {}
        for match_num, stage_id, match_id, status, kickoff in targets:
            print(f"detail sync: match {match_num} ({status}, kickoff {kickoff})")
            sync_detail(cur, match_num, stage_id, match_id, id2code)
        cur.execute("SELECT count(*) FROM wc_matches WHERE status = 'FT'")
        ft = cur.fetchone()[0]
        print(f"calendar: {n} matches updated | detail targets: {len(targets)} | finished: {ft}")


if __name__ == "__main__":
    main()
