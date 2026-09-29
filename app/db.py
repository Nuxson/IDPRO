"""SQLite-хранилище выданных ID (внутренняя база)."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .idgen import make_id, normalize_id

def _data_dir() -> Path:
    """Каталог данных. Приоритет: IDGEN_DB > IDGEN_DATA_DIR > рядом с exe/проектом."""
    env_db = os.environ.get("IDGEN_DB")
    if env_db:
        return Path(env_db).resolve().parent
    if getattr(__import__("sys"), "frozen", False):  # PyInstaller
        # Портативный режим: данные рядом с exe (папка переносится целиком,
        # без записи в профиль пользователя и без прав администратора).
        return Path(__import__("sys").executable).resolve().parent
    return Path(__file__).resolve().parent.parent


_env_db = os.environ.get("IDGEN_DB")
DB_PATH = Path(_env_db).resolve() if _env_db else _data_dir() / "ids.db"

# Единственная дата в системе — дата выдачи UID (created_at), часовой пояс системный.
DATE_COL = "created_at"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ids (
    id          TEXT PRIMARY KEY,      -- сформированный код (XXXX-XXXX-...)
    compact     TEXT UNIQUE,           -- код без разделителей
    producer    TEXT NOT NULL,
    date        TEXT NOT NULL,         -- СЛУЖЕБНАЯ: ISO-дата, зашитая в каноническую строку
    location    TEXT NOT NULL,
    company     TEXT NOT NULL,
    serial      TEXT NOT NULL,
    port        TEXT NOT NULL,         -- TN_A / TN_B / TN_C
    site        TEXT NOT NULL,         -- номер площадки (целое число)
    canonical   TEXT NOT NULL,
    created_at  TEXT NOT NULL,         -- дата/время выдачи UID (системный часовой пояс)
    UNIQUE (producer, date, location, company, serial, port, site)
);
"""

_PORT_COLS = ("port", "site")


def now_stamp() -> str:
    """Метка времени выдачи UID в системном часовом поясе (без смещения UTC)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _migrate(conn: sqlite3.Connection) -> None:
    """Добавляет колонки port/site и чинит дубли дат в БД старых форматов."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(ids)")}
    if not cols:
        return
    for col in _PORT_COLS:
        if col not in cols:
            conn.execute(f"ALTER TABLE ids ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    # Дедупликация дат: created_at переводим в локальное время (было UTC from SQLite),
    # а старые записи с created_at='date ... 00:00:00' схлопываем в саму дату выдачи.
    rows = conn.execute("SELECT rowid, date, created_at FROM ids").fetchall()
    for rowid, d, ca in rows:
        if not ca or ca == "":
            conn.execute("UPDATE ids SET created_at=? WHERE rowid=?",
                         (f"{d} 00:00:00", rowid))
        elif d and ca.startswith(d) and ca.endswith("00:00:00"):
            conn.execute("UPDATE ids SET created_at=? WHERE rowid=?", (ca[:10], rowid))


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
    stamp = now_stamp()  # единственная дата — момент выдачи UID (системный TZ)
    with get_conn(db_path) as conn:
        _migrate(conn)  # совместимость со старыми БД (v1 без port/site)
        cur = conn.execute(
            """INSERT INTO ids (id, compact, producer, date, location, company, serial, port, site, canonical, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (producer, date, location, company, serial, port, site) DO NOTHING""",
            (rec["id"], rec["compact"], f["producer"], f["date"], f["location"],
             f["company"], f["serial"], f["port"], f["site"], rec["canonical"], stamp),
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


# ---------- Редактирование и удаление записей базы ----------

#: Поля записи, доступные для изменения из интерфейса.
#: id/compact/canonical/date — служебные, вычисляются из этих полей заново.
EDITABLE_FIELDS = ("producer", "location", "company", "serial", "port", "site")


def update_record(raw_id: str, changes: dict, secret: str | None = None,
                  db_path: Path | str = DB_PATH) -> dict:
    """Изменяет редактируемые поля записи; код пересчитывается автоматически.

    changes — словарь с ключами из EDITABLE_FIELDS (можно частичное обновление).
    Возвращает {"record": ..., "changed": bool}.
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValueError("Неизвестные поля: " + ", ".join(sorted(unknown)))
    compact = normalize_id(raw_id)
    with get_conn(db_path) as conn:
        _migrate(conn)
        row = conn.execute("SELECT * FROM ids WHERE compact=?", (compact,)).fetchone()
        if row is None:
            raise KeyError(f"Запись не найдена: {raw_id}")
        new_vals = {k: (str(v).strip() if v is not None else "")
                    for k, v in changes.items() if k in EDITABLE_FIELDS}
        changed = any(new_vals[k] != row[k] for k in new_vals)
        if changed:
            merged = {k: row[k] for k in EDITABLE_FIELDS}
            merged.update(new_vals)
            # валидация новых значений через make_id (справочники, формат порта/площадки)
            try:
                rec = make_id(merged["producer"], merged["location"], merged["company"],
                              merged["serial"], merged["port"], merged["site"],
                              dt=row["date"], secret=secret)
            except ValueError as e:
                raise ValueError(str(e)) from e
            conn.execute(
                """UPDATE ids SET producer=?, location=?, company=?, serial=?,
                          port=?, site=?, id=?, compact=?, canonical=? WHERE id=?""",
                (rec["fields"]["producer"], rec["fields"]["location"],
                 rec["fields"]["company"], rec["fields"]["serial"],
                 rec["fields"]["port"], rec["fields"]["site"],
                 rec["id"], rec["compact"], rec["canonical"], row["id"]),
            )
            row = conn.execute("SELECT * FROM ids WHERE compact=?",
                               (rec["compact"],)).fetchone()
        return {"record": dict(row), "changed": changed}


def delete_record(raw_id: str, db_path: Path | str = DB_PATH) -> bool:
    """Удаляет запись из базы по ID (с разделителями или без). True — если удалено."""
    compact = normalize_id(raw_id)
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM ids WHERE compact=?", (compact,))
        return cur.rowcount > 0


# ---------- Экспорт данных (CSV / Excel) ----------

EXPORT_HEADERS = {
    "id": "UID",
    "producer": "Производитель",
    "created_at": "Дата выдачи",
    "location": "Место положения",
    "company": "Компания",
    "serial": "Серийный номер",
    "port": "Порт",
    "site": "Номер площадки",
}
EXPORT_ORDER = ("id", "producer", "created_at", "location",
                "company", "serial", "port", "site")


def export_rows(db_path: Path | str = DB_PATH) -> list[dict]:
    """Все записи для экспорта, отсортированные по дате выдачи."""
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM ids ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def export_csv(db_path: Path | str = DB_PATH) -> bytes:
    """CSV с BOM (Excel корректно открывает кириллицу), разделитель — точка с запятой."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(EXPORT_HEADERS[c] for c in EXPORT_ORDER)
    for r in export_rows(db_path):
        w.writerow([r.get(c, "") for c in EXPORT_ORDER])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def export_xlsx(db_path: Path | str = DB_PATH) -> bytes:
    """Файл .xlsx без внешних зависимостей (минимальный OOXML-пакет)."""
    import zipfile

    def esc(v) -> str:
        s = str(v if v is not None else "")
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def cell(ref: str, val) -> str:
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return f'<c r="{ref}"><v>{val}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{esc(val)}</t></is></c>'

    rows_xml = []
    all_rows = [[EXPORT_HEADERS[c] for c in EXPORT_ORDER]]
    for r in export_rows(db_path):
        line = []
        for c in EXPORT_ORDER:
            v = r.get(c, "")
            if c == "site":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    pass
            line.append(v)
        all_rows.append(line)
    for i, line in enumerate(all_rows, start=1):
        cells = "".join(
            cell(f"{chr(ord('A') + j)}{i}", v) for j, v in enumerate(line))
        rows_xml.append(f'<row r="{i}">{cells}</row>')

    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
                ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="UID" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                 'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return bio.getvalue()
