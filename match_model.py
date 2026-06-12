"""Elo → match-outcome model: fitted goal expectancy + independent Poissons.

Mapping fitted on internationals in a FIXED window (2010-01-01 → 2022-11-19,
i.e. everything before the Qatar World Cup) so the parameters that ship are
exactly the ones validated by backtest-2022.py:
  exp_goal_diff = GD_SLOPE * d          (least squares through the origin)
  exp_total     = T0 + T1 * |d|         (mismatches produce more goals)
where d = elo_home - elo_away (+100 if not neutral). Outcome probabilities
come from a 0..10 Poisson score grid. Draws are what the grid says — no
ad-hoc inflation unless the backtest shows it's needed.
"""
from datetime import date
from math import exp

from elo import BASE_RATING, HOME_ADVANTAGE, apply_match

FIT_START, FIT_END = date(2010, 1, 1), date(2022, 11, 19)
LAMBDA_FLOOR = 0.15
GRID = 11  # goals 0..10 per side


def fit_parameters(results: list[dict]) -> dict:
    """Roll Elo through history; regress goals on pre-match rating diff in the window."""
    ratings: dict[str, float] = {}
    sum_dd = sum_dgd = n = sum_t = sum_ad = sum_adt = sum_adad = 0.0
    for m in results:
        if m["date"] >= FIT_END:
            break
        if m["date"] >= FIT_START:
            d = (ratings.get(m["home"], BASE_RATING) - ratings.get(m["away"], BASE_RATING)
                 + (0.0 if m["neutral"] else HOME_ADVANTAGE))
            gd, total, ad = m["hs"] - m["as"], m["hs"] + m["as"], abs(d)
            sum_dd += d * d
            sum_dgd += d * gd
            n += 1
            sum_t += total
            sum_ad += ad
            sum_adt += ad * total
            sum_adad += ad * ad
        apply_match(ratings, m)
    gd_slope = sum_dgd / sum_dd
    t1 = (n * sum_adt - sum_ad * sum_t) / (n * sum_adad - sum_ad * sum_ad)
    t0 = (sum_t - t1 * sum_ad) / n
    return {"gd_slope": gd_slope, "t0": t0, "t1": t1, "n_fit": int(n)}


def lambdas(d: float, p: dict) -> tuple[float, float]:
    gd = p["gd_slope"] * d
    total = p["t0"] + p["t1"] * abs(d)
    return (max((total + gd) / 2.0, LAMBDA_FLOOR), max((total - gd) / 2.0, LAMBDA_FLOOR))


def poisson_pmf(lam: float) -> list[float]:
    pmf, term = [], exp(-lam)
    for k in range(GRID):
        pmf.append(term)
        term *= lam / (k + 1)
    return pmf


def outcome_probs(d: float, p: dict) -> tuple[float, float, float, float, float]:
    """(p_home, p_draw, p_away, exp_home_goals, exp_away_goals) for rating diff d."""
    lh, la = lambdas(d, p)
    ph, pa = poisson_pmf(lh), poisson_pmf(la)
    home = draw = away = 0.0
    for i in range(GRID):
        for j in range(GRID):
            pij = ph[i] * pa[j]
            if i > j:
                home += pij
            elif i == j:
                draw += pij
            else:
                away += pij
    s = home + draw + away  # grid truncation → renormalize
    return home / s, draw / s, away / s, lh, la
