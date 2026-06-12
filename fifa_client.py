"""Thin client for the public FIFA API (api.fifa.com/api/v3).

The API is public but UNDOCUMENTED — it powers fifa.com itself. Shape was
verified live on 2026-06-11 against season 285023 (WC2026) and a finished
Qatar 2022 match (lineups, formations, event timeline). Every fetch is
snapshotted to etl/snapshots/ (gitignored) so shape drift can be diagnosed.
"""
import json
import re
import time
from pathlib import Path

import requests

BASE = "https://api.fifa.com/api/v3"
COMPETITION_ID = "17"   # FIFA World Cup
SEASON_ID = "285023"    # FIFA World Cup 2026
FLAG_URL_TEMPLATE = BASE + "/picture/flags-sq-4/{code}"  # verified: 200 image/png

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) worldcup-dashboard-etl"}
THROTTLE_SECONDS = 0.6  # polite pacing; no published rate limit

STAGE_BY_NAME = {
    "First Stage": "group",
    "Round of 32": "r32",
    "Round of 16": "r16",
    "Quarter-final": "qf",
    "Semi-final": "sf",
    "Play-off for third place": "third_place",
    "Final": "final",
}
# Raw FIFA MatchStatus → our status. 0/1 verified; 3=LIVE is the commonly
# observed value — re-verify on the first live matchday and extend if needed.
STATUS_BY_FIFA = {0: "FT", 1: "NS", 3: "LIVE"}

TEAM_CODE_RE = re.compile(r"^[A-Z]{3}$")
SLOT_RE = re.compile(r"^[0-9A-Z/]{1,12}$")
# Letters (incl. accented), digits, spaces and common name punctuation.
NAME_RE = re.compile(r"^[\w À-ɏ&().,'’/-]+$", re.UNICODE)


def loc(value, default=None):
    """FIFA localized fields are [{Locale, Description}] arrays."""
    if isinstance(value, list) and value and value[0].get("Description"):
        return value[0]["Description"]
    return default


def sane_name(value, what):
    """Charset-whitelist guard for strings that end up in dashboard markdown."""
    if value is None:
        return None
    if not NAME_RE.match(value):
        raise ValueError(f"suspicious {what}: {value!r}")
    return value


def _get(path: str, params: dict, snapshot: str) -> dict:
    resp = requests.get(f"{BASE}/{path}", params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    (SNAPSHOT_DIR / snapshot).write_text(json.dumps(data, ensure_ascii=False))
    time.sleep(THROTTLE_SECONDS)
    return data


def fetch_calendar() -> list[dict]:
    """All matches of the season (104 rows; fixtures + scores once played)."""
    data = _get(
        "calendar/matches",
        {"idSeason": SEASON_ID, "idCompetition": COMPETITION_ID,
         "language": "en", "count": 500},
        snapshot="calendar.json",
    )
    results = data.get("Results") or []
    if len(results) < 100:
        raise RuntimeError(f"calendar returned {len(results)} matches — expected 104; API shape may have drifted")
    return results


def fetch_seasons() -> list[dict]:
    """All editions of competition 17 (1930 → current). Verified 2026-06-12: 23 seasons."""
    data = _get(
        "seasons",
        {"idCompetition": COMPETITION_ID, "count": 100, "language": "en"},
        snapshot="seasons.json",
    )
    results = data.get("Results") or []
    if len(results) < 20:
        raise RuntimeError(f"seasons returned {len(results)} — expected 23+; API shape may have drifted")
    return results


def fetch_season_calendar(season_id: str) -> list[dict]:
    """Calendar of ANY season. Historical editions are small (1930 = 18 matches)."""
    data = _get(
        "calendar/matches",
        {"idSeason": season_id, "idCompetition": COMPETITION_ID,
         "language": "en", "count": 500},
        snapshot=f"calendar-{season_id}.json",
    )
    results = data.get("Results") or []
    if len(results) < 16:
        raise RuntimeError(f"season {season_id} returned {len(results)} matches — below the smallest edition (16); API shape may have drifted")
    return results


def fetch_match_detail(fifa_stage_id: str, fifa_match_id: str) -> dict:
    """Squads with lineup status + pitch coords, formations, possession."""
    return _get(
        f"live/football/{COMPETITION_ID}/{SEASON_ID}/{fifa_stage_id}/{fifa_match_id}",
        {"language": "en"},
        snapshot=f"detail-{fifa_match_id}.json",
    )


def fetch_timeline(fifa_stage_id: str, fifa_match_id: str) -> dict:
    """Minute-by-minute event stream (goals, cards, subs, VAR...)."""
    return _get(
        f"timelines/{COMPETITION_ID}/{SEASON_ID}/{fifa_stage_id}/{fifa_match_id}",
        {"language": "en"},
        snapshot=f"timeline-{fifa_match_id}.json",
    )
