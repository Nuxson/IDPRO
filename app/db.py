"""SQLite-хранилище выданных ID (внутренняя база)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .idgen import make_id, normalize_id

DB_PATH = Path(__file__).resolve().parent.parent / "ids.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ids (
    id          TEXT PRIMARY KEY,      -- сформированный код XXXX-XXXX-XXXX-CCKK
    compact     TEXT UNIQUE,           -- код без разделителей
    producer    TEXT NOT NULL,
    date        TEXT NOT NULL,         -- ISO YYYY-MM-DD
    location    TEXT NOT NULL,
    company     TEXT NOT NULL,
    serial      TEXT NOT NULL,
    canonical   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (producer, date, location, company, serial)
);
"""


@contextmanager
def get_conn(db_path: Path | str = DB_PATH):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def register(producer: str, dt: str, location: str, company: str, serial: str,
             secret: str | None = None, db_path: Path | str = DB_PATH) -> dict:
    """Генерирует ID и сохраняет в базу. Повторная регистрация тех же данных вернёт существующий ID."""
    rec = make_id(producer, dt, location, company, serial, secret)
    f = rec["fields"]
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO ids (id, compact, producer, date, location, company, serial, canonical)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (producer, date, location, company, serial) DO NOTHING""",
            (rec["id"], rec["compact"], f["producer"], f["date"], f["location"],
             f["company"], f["serial"], rec["canonical"]),
        )
        if cur.rowcount == 0:  # такая запись уже есть — возвращаем её
            row = conn.execute(
                "SELECT * FROM ids WHERE producer=? AND date=? AND location=? AND company=? AND serial=?",
                (f["producer"], f["date"], f["location"], f["company"], f["serial"]),
            ).fetchone()
            return {"record": dict(row), "created": False}
        row = conn.execute("SELECT * FROM ids WHERE compact=?", (rec["compact"],)).fetchone()
        return {"record": dict(row), "created": True}


def lookup(raw_id: str, db_path: Path | str = DB_PATH) -> dict | None:
    """Поиск записи в базе по ID (с разделителями или без)."""
    compact = normalize_id(raw_id)
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM ids WHERE compact=?", (compact,)).fetchone()
        return dict(row) if row else None


def list_ids(limit: int = 100, db_path: Path | str = DB_PATH) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ids ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
