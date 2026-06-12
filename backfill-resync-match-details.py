"""One-off re-sync of finished matches' detail payloads (squads, lineups,
events) through the production sync_detail path — used after changing how
event descriptions are composed, so existing rows pick up the new text.

Run: venv-etl/bin/python backfill-resync-match-details.py
"""
import importlib.util
from pathlib import Path

from db import get_conn

spec = importlib.util.spec_from_file_location(
    "sync_live", Path(__file__).resolve().parent / "sync-live.py"
)
sync_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_live)


def main() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT match_num, fifa_stage_id, fifa_match_id FROM wc_matches
               WHERE status = 'FT' AND fifa_stage_id IS NOT NULL ORDER BY match_num"""
        )
        targets = cur.fetchall()
        id2code = sync_live.team_code_by_fifa_id(cur)
        for match_num, stage_id, match_id, in targets:
            sync_live.sync_detail(cur, match_num, stage_id, match_id, id2code)
            print(f"re-synced match {match_num}")


if __name__ == "__main__":
    main()
