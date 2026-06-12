"""World Football Elo engine over the martj42 all-internationals dataset.

Methodology = eloratings.net (published on its About page):
  delta = K * G * (W - We)
  K  by competition tier (60 WC finals ... 20 friendlies)
  G  goal-margin multiplier: 1 (margin<=1), 1.5 (=2), (11+N)/8 (>=3)
  We = 1 / (10^(-d/400) + 1), d = rating diff + 100 home advantage (non-neutral)
All teams start at 1500; ~150 years of matches converge long before the eras
we read ratings from. Ratings are keyed by the dataset's team NAMES — map to
FIFA codes at the edge (see TEAM_CODE_OVERRIDES in the loaders).
"""
import csv
from datetime import date
from pathlib import Path

RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
RESULTS_LOCAL = Path(__file__).resolve().parent / "snapshots" / "intl-results.csv"

BASE_RATING = 1500.0
HOME_ADVANTAGE = 100.0

K_BY_TOURNAMENT = {
    "FIFA World Cup": 60,
    "Confederations Cup": 50,
    "Copa América": 50, "UEFA Euro": 50, "African Cup of Nations": 50,
    "AFC Asian Cup": 50, "Gold Cup": 50, "CONCACAF Championship": 50,
    "Oceania Nations Cup": 50,
    "FIFA World Cup qualification": 40,
    "UEFA Euro qualification": 40, "African Cup of Nations qualification": 40,
    "AFC Asian Cup qualification": 40, "Gold Cup qualification": 40,
    "Copa América qualification": 40, "Oceania Nations Cup qualification": 40,
    "UEFA Nations League": 40, "CONCACAF Nations League": 40,
    "Friendly": 20,
}
K_DEFAULT = 30  # minor tournaments (King's Cup, Kirin Cup, regional cups, ...)


def load_results(path: Path = RESULTS_LOCAL) -> list[dict]:
    """Played matches only (scheduled fixtures ship with NA scores), date-sorted."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r["home_score"] or r["home_score"] == "NA":
                continue
            rows.append({
                "date": date.fromisoformat(r["date"]),
                "home": r["home_team"], "away": r["away_team"],
                "hs": int(float(r["home_score"])), "as": int(float(r["away_score"])),
                "tournament": r["tournament"], "neutral": r["neutral"] == "TRUE",
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def expected(d: float) -> float:
    return 1.0 / (10.0 ** (-d / 400.0) + 1.0)


def goal_multiplier(margin: int) -> float:
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11 + margin) / 8.0


def apply_match(ratings: dict, m: dict) -> None:
    rh = ratings.get(m["home"], BASE_RATING)
    ra = ratings.get(m["away"], BASE_RATING)
    d = rh - ra + (0.0 if m["neutral"] else HOME_ADVANTAGE)
    we = expected(d)
    w = 1.0 if m["hs"] > m["as"] else 0.0 if m["hs"] < m["as"] else 0.5
    k = K_BY_TOURNAMENT.get(m["tournament"], K_DEFAULT)
    delta = k * goal_multiplier(abs(m["hs"] - m["as"])) * (w - we)
    ratings[m["home"]] = rh + delta
    ratings[m["away"]] = ra - delta


def ratings_as_of(results: list[dict], cutoff: date) -> dict[str, float]:
    """Ratings using all matches strictly BEFORE cutoff (a pre-tournament prior)."""
    ratings: dict[str, float] = {}
    for m in results:
        if m["date"] >= cutoff:
            break
        apply_match(ratings, m)
    return ratings
