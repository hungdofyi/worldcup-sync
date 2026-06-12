"""Neon Postgres connection helper. Reads NEON_DATABASE_URL from this repo's .env (gitignored)."""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def get_conn() -> psycopg.Connection:
    url = os.environ.get("NEON_DATABASE_URL")
    if not url:
        raise SystemExit(
            "NEON_DATABASE_URL is not set. "
            "Copy etl/.env.example to 'Heo Sao Mai/worldcup/.env' and fill it in."
        )
    return psycopg.connect(url)
