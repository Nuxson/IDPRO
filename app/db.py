"""SQLite-хранилище выданных ID (внутренняя база)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .idgen import make_id, normalize_id

DB_PATH = Path(__file__).resolve().parent.parent / "ids.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ids (
    id          TEXT PRIMARY KEY,      -- сформированный код формата v4 (XXXX-XXXX-...)
    compact     TEXT UNIQUE,           -- код без разделителей
    producer    TEXT NOT NULL,
    date        TEXT NOT NULL,         -- ISO YYYY-MM-DD
    location    TEXT NOT NULL,
    company     TEXT NOT NULL,
    serial      TEXT NOT NULL,
    port        TEXT NOT NULL,         -- TN_A / TN_B / TN_C
    site        TEXT NOT NULL,         -- номер площадки (целое число)
    canonical   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (producer, date, location, company, serial, port, site)
);
"""

_PORT_COLS = ("port", "site")


def _migrate(conn: sqlite3.Connection) -> None:
    """Добавляет колонки port/site в БД старого (v1) формата."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ids)")}
    if not cols:
        return
    for col in _PORT_COLS:
        if col not in cols:
            conn.execute(f"ALTER TABLE ids ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")


_INIT_DONE: dict[str, str] = {}


def _ensure_schema(conn: sqlite3.Connection, path: str) -> None:
    """Создаёт таблицу при первом обращении и при восстановлении удалённого ids.db.

    Файл БД проверяется по inode (а не только по имени): sqlite-соединение
    продолжает писать в старый удалённый дескриптор, поэтому простого
    exists() недостаточно.
    """
    import os
    try:
        stat = os.stat(path)
        key = f"{stat.st_dev}:{stat.st_ino}"
    except FileNotFoundError:
        key = "missing"
    if _INIT_DONE.get(path) == key:
        return
    conn.executescript(SCHEMA)   # CREATE TABLE IF NOT EXISTS — безопасно
    _migrate(conn)
    try:
        stat = os.stat(path)
        _INIT_DONE[path] = f"{stat.st_dev}:{stat.st_ino}"
    except FileNotFoundError:
        _INIT_DONE.pop(path, None)


@contextmanager
def get_conn(db_path: Path | str = DB_PATH):
    """Открытое соединение; схема создаётся автоматически (ленивая инициализация),
    поэтому приложение работает даже если кто-то удалил файл ids.db на ходу."""
    path = str(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn, path)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def register(producer: str, location: str, company: str, serial: str,
             port: str, site: str, secret: str | None = None,
             db_path: Path | str = DB_PATH) -> dict:
    """Генерирует ID (дата фиксируется автоматически — сегодня) и сохраняет в базу.

    Повторная регистрация тех же данных вернёт существующий ID (с его исходной датой).
    """
    rec = make_id(producer, location, company, serial, port, site, secret)
    f = rec["fields"]
    uniq = (f["producer"], f["date"], f["location"], f["company"], f["serial"], f["port"], f["site"])
    with get_conn(db_path) as conn:
        _migrate(conn)  # совместимость со старыми БД (v1 без port/site)
        cur = conn.execute(
            """INSERT INTO ids (id, compact, producer, date, location, company, serial, port, site, canonical)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (producer, date, location, company, serial, port, site) DO NOTHING""",
            (rec["id"], rec["compact"], f["producer"], f["date"], f["location"],
             f["company"], f["serial"], f["port"], f["site"], rec["canonical"]),
        )
        if cur.rowcount == 0:  # такая запись уже есть — возвращаем её
            row = conn.execute(
                """SELECT * FROM ids WHERE producer=? AND date=? AND location=?
                   AND company=? AND serial=? AND port=? AND site=?""",
                uniq,
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
