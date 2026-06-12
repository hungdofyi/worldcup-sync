"""Monte Carlo simulation of the remaining WC2026 tournament.

Starts from current state: FT matches keep their real result; everything else
is sampled from the Elo match model. Group ranking approximates FIFA
tiebreakers (pts, gd, gf, then random — head-to-head and fair play omitted).
Best 8 of 12 third-placed teams advance; their R32 slots ('3ABCDF' = a third
from one of those groups) are filled by backtracking to a consistent
assignment. Knockout draws advance via the shootout proxy p_h/(p_h+p_a).
Host home advantage: a USA/MEX/CAN side listed at home gets the Elo home bonus.
"""
import random
from math import exp

from elo import BASE_RATING, HOME_ADVANTAGE
from match_model import lambdas, outcome_probs

HOSTS = {"USA", "MEX", "CAN"}
STAGE_ORDER = ["r32", "r16", "qf", "sf", "final"]  # third_place simulated, not tracked


def host_adv(home: str, away: str) -> float:
    if home in HOSTS and away not in HOSTS:
        return HOME_ADVANTAGE
    if away in HOSTS and home not in HOSTS:
        return -HOME_ADVANTAGE
    return 0.0


class Simulator:
    def __init__(self, fixtures: list[dict], elo_by_code: dict[str, float], params: dict):
        """fixtures: rows of wc_matches — match_num, stage, group_code, home_code,
        away_code, home_slot, away_slot, home_score, away_score, status."""
        self.fx = sorted(fixtures, key=lambda m: m["match_num"])
        self.elo = elo_by_code
        self.params = params
        self._pmf_cache: dict[tuple, tuple] = {}

    def _pmfs(self, home: str, away: str) -> tuple[list[float], list[float]]:
        key = (home, away)
        if key not in self._pmf_cache:
            d = (self.elo.get(home, BASE_RATING) - self.elo.get(away, BASE_RATING)
                 + host_adv(home, away))
            lh, la = lambdas(d, self.params)
            self._pmf_cache[key] = (self._pmf(lh), self._pmf(la))
        return self._pmf_cache[key]

    @staticmethod
    def _pmf(lam: float) -> list[float]:
        out, term = [], exp(-lam)
        for k in range(11):
            out.append(term)
            term *= lam / (k + 1)
        return out

    @staticmethod
    def _sample(pmf: list[float]) -> int:
        r, acc = random.random(), 0.0
        for k, p in enumerate(pmf):
            acc += p
            if r < acc:
                return k
        return len(pmf) - 1

    def _group_tables(self) -> dict[str, dict[str, list]]:
        """Simulate remaining group matches; return per-group {team: [pts, gd, gf, rnd]}."""
        tables: dict[str, dict[str, list]] = {}
        for m in self.fx:
            if m["stage"] != "group":
                continue
            g = tables.setdefault(m["group_code"], {})
            for c in (m["home_code"], m["away_code"]):
                g.setdefault(c, [0, 0, 0, random.random()])
            if m["status"] == "FT":
                hs, as_ = m["home_score"], m["away_score"]
            else:
                ph, pa = self._pmfs(m["home_code"], m["away_code"])
                hs, as_ = self._sample(ph), self._sample(pa)
            g[m["home_code"]][0] += 3 if hs > as_ else 1 if hs == as_ else 0
            g[m["away_code"]][0] += 3 if as_ > hs else 1 if hs == as_ else 0
            g[m["home_code"]][1] += hs - as_
            g[m["away_code"]][1] += as_ - hs
            g[m["home_code"]][2] += hs
            g[m["away_code"]][2] += as_
        return tables

    @staticmethod
    def _assign_thirds(slots: list[tuple[str, set]], thirds: dict[str, str]) -> dict[str, str]:
        """Backtracking match of qualified thirds (group→team) onto '3…' slots."""
        groups = list(thirds)

        def solve(i: int, used: set) -> dict | None:
            if i == len(slots):
                return {}
            slot_key, allowed = slots[i]
            for g in groups:
                if g in used or g not in allowed:
                    continue
                rest = solve(i + 1, used | {g})
                if rest is not None:
                    return {slot_key: thirds[g], **rest}
            return None

        out = solve(0, set())
        if out is None:  # combination outside FIFA's table — assign arbitrarily
            free = [thirds[g] for g in groups]
            out = {key: free[i] for i, (key, _) in enumerate(slots)}
        return out

    def run_once(self) -> dict[str, str]:
        """One tournament → {team_code: furthest stage reached ('group'|'r32'|...|'champion')}."""
        reached: dict[str, str] = {}
        tables = self._group_tables()
        slot_team: dict[str, str] = {}
        thirds: dict[str, str] = {}
        third_rank = []
        for g, table in tables.items():
            ranked = sorted(table, key=lambda c: (-table[c][0], -table[c][1], -table[c][2], table[c][3]))
            for c in ranked:
                reached[c] = "group"
            slot_team[f"1{g}"] = ranked[0]
            slot_team[f"2{g}"] = ranked[1]
            third_rank.append((tuple(-v for v in table[ranked[2]][:3]), g, ranked[2]))
        third_rank.sort()
        for _, g, c in third_rank[:8]:
            thirds[g] = c
        third_slots = [(s, set(s[1:]))
                       for m in self.fx if m["stage"] == "r32"
                       for s in (m["home_slot"], m["away_slot"])
                       if (s or "").startswith("3")]
        slot_team.update(self._assign_thirds(third_slots, thirds))

        winners: dict[int, str] = {}
        losers: dict[int, str] = {}
        for m in self.fx:
            if m["stage"] in ("group",):
                continue
            home = m["home_code"] or self._resolve(m["home_slot"], slot_team, winners, losers)
            away = m["away_code"] or self._resolve(m["away_slot"], slot_team, winners, losers)
            stage = m["stage"]
            if stage != "third_place":
                for c in (home, away):
                    reached[c] = stage
            if m["status"] == "FT":
                win = home if (m["home_score"], m.get("home_pen") or 0) > (m["away_score"], m.get("away_pen") or 0) else away
            else:
                ph, pd, pa, _, _ = self._cached_probs(home, away)
                r = random.random()
                if r < ph:
                    win = home
                elif r < ph + pa:
                    win = away
                else:  # draw → shootout proxy
                    win = home if random.random() < ph / (ph + pa) else away
            winners[m["match_num"]] = win
            losers[m["match_num"]] = away if win == home else home
            if stage == "final":
                reached[win] = "champion"
        return reached

    def _cached_probs(self, home: str, away: str):
        key = ("probs", home, away)
        if key not in self._pmf_cache:
            d = (self.elo.get(home, BASE_RATING) - self.elo.get(away, BASE_RATING)
                 + host_adv(home, away))
            self._pmf_cache[key] = outcome_probs(d, self.params)
        return self._pmf_cache[key]

    @staticmethod
    def _resolve(slot: str, slot_team: dict, winners: dict, losers: dict) -> str:
        if slot.startswith("RU"):  # runner-up (loser) of a match, e.g. third-place tie
            return losers[int(slot[2:])]
        if slot.startswith("W"):
            return winners[int(slot[1:])]
        return slot_team[slot]

    def run(self, n: int = 10000) -> dict[str, dict[str, float]]:
        """{team: {p_r32, p_r16, p_qf, p_sf, p_final, p_champion}} over n tournaments."""
        idx = {s: i for i, s in enumerate(STAGE_ORDER)}
        counts: dict[str, list[int]] = {}
        for _ in range(n):
            for team, stage in self.run_once().items():
                row = counts.setdefault(team, [0] * (len(STAGE_ORDER) + 1))
                if stage == "champion":
                    upto = len(STAGE_ORDER)
                elif stage == "group":
                    upto = 0
                else:
                    upto = idx[stage] + 1
                for i in range(upto):
                    row[i] += 1
                if stage == "champion":
                    row[len(STAGE_ORDER)] += 1
        out = {}
        for team, row in counts.items():
            out[team] = {f"p_{s}": row[i] / n for i, s in enumerate(STAGE_ORDER)}
            out[team]["p_champion"] = row[len(STAGE_ORDER)] / n
        return out
