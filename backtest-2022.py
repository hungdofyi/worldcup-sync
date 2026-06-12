"""Calibration backtest: predict every Qatar 2022 match with the shipped model.

Honest protocol — parameters fitted strictly pre-tournament (match_model.py's
fixed window ends 2022-11-19), ratings roll sequentially through the tournament
exactly as the nightly job would have updated them. Outcomes are the recorded
90'+ET scores (a shootout is a draw, consistent with the fit convention).

Acceptance (the analyst bar for shipping probabilities):
  - multi-class Brier beats the uniform baseline clearly
  - calibration buckets: favorites priced p win about p of the time

Usage: python backtest-2022.py   (no DB; reads snapshots/intl-results.csv)
"""
from datetime import date

from elo import BASE_RATING, HOME_ADVANTAGE, apply_match, load_results, ratings_as_of
from match_model import fit_parameters, outcome_probs

WC_START, WC_END = date(2022, 11, 20), date(2022, 12, 19)


def main() -> None:
    results = load_results()
    params = fit_parameters(results)
    print(f"fitted on {params['n_fit']} internationals 2010→2022-11-19: "
          f"gd_slope={params['gd_slope']:.6f}  total={params['t0']:.3f}+{params['t1']:.5f}|d|")

    ratings = ratings_as_of(results, WC_START)
    wc = [m for m in results
          if WC_START <= m["date"] <= WC_END and m["tournament"] == "FIFA World Cup"]
    print(f"backtesting {len(wc)} Qatar 2022 matches\n")

    brier = 0.0
    buckets = {}  # favorite prob decile → [n, hits]
    draws_pred = draws_real = 0.0
    for m in wc:
        d = (ratings.get(m["home"], BASE_RATING) - ratings.get(m["away"], BASE_RATING)
             + (0.0 if m["neutral"] else HOME_ADVANTAGE))
        ph, pd, pa, _, _ = outcome_probs(d, params)
        o = (1, 0, 0) if m["hs"] > m["as"] else (0, 0, 1) if m["hs"] < m["as"] else (0, 1, 0)
        brier += (ph - o[0]) ** 2 + (pd - o[1]) ** 2 + (pa - o[2]) ** 2
        fav_p, fav_won = (ph, o[0]) if ph >= pa else (pa, o[2])
        b = buckets.setdefault(round(fav_p, 1), [0, 0])
        b[0] += 1
        b[1] += fav_won
        draws_pred += pd
        draws_real += o[1]
        apply_match(ratings, m)  # roll forward, as the nightly job would

    n = len(wc)
    uniform = ((1 / 3 - 1) ** 2 + 2 * (1 / 3) ** 2)  # uniform prior per match
    print(f"multi-class Brier: {brier / n:.4f}  (uniform baseline {uniform:.4f})")
    print(f"draws: predicted {draws_pred / n:.1%} vs realized {draws_real / n:.1%}\n")
    print("calibration (favorite win prob → realized):")
    for p in sorted(buckets):
        cnt, hits = buckets[p]
        print(f"  ~{p:.0%}: {hits}/{cnt} = {hits / cnt:.0%}")


if __name__ == "__main__":
    main()
