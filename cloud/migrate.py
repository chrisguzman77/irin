"""Apply cloud/sql/*.sql in order against TIGER_URI, once, idempotently
(C2). A `schema_migrations` table records applied filenames; each file is
applied in one transaction. Run: python migrate.py  (or at container boot)."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

TIGER_URI = os.environ.get("TIGER_URI", "postgresql://postgres:postgres@localhost:5432/irin")
SQL_DIR = Path(__file__).resolve().parent / "sql"


def migrate(uri: str = TIGER_URI) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(uri) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(SQL_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    print("applied:", migrate() or "nothing new")
