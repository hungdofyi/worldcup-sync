"""One-off backfill: title-case the ALL-CAPS surnames FIFA ships in
wc_players.name and wc_events.description (wc_goals / wc_scorers are views
over these tables, so they inherit the fix). sync-live.py applies the same
normalize_caps_name() at ingest, so re-synced rows stay normalized.

Run: venv-etl/bin/python backfill-normalize-names.py
"""
from db import get_conn
from fifa_client import normalize_caps_name


def backfill(cur, table: str, key: str, col: str) -> int:
    cur.execute(f"SELECT {key}, {col} FROM {table} WHERE {col} IS NOT NULL")
    changed = 0
    for pk, value in cur.fetchall():
        normalized = normalize_caps_name(value)
        if normalized != value:
            cur.execute(f"UPDATE {table} SET {col} = %s WHERE {key} = %s", (normalized, pk))
            changed += 1
    print(f"{table}.{col}: {changed} rows normalized")
    return changed


def main() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        backfill(cur, "wc_players", "fifa_player_id", "name")
        backfill(cur, "wc_events", "event_id", "description")


if __name__ == "__main__":
    main()
