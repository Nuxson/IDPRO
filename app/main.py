"""REST API + веб-интерфейс генерации и проверки уникальных ID."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .codes import CodesError, ensure_configs, load_codes as load_codes_raw, registry, save_codes
from .idgen import (extract_parts, make_id, suggest_code, suggest_port,
                    verify_checksum)

app = FastAPI(title="Unique ID Generator", version="1.0")

def _static_dir() -> Path:
    """Каталог веб-интерфейса: в исходниках — app/static; в собранном exe —
    извлекаемый бандл PyInstaller (_MEIPASS)."""
    if getattr(__import__("sys"), "frozen", False):  # PyInstaller
        return Path(__import__("sys")._MEIPASS) / "app" / "static"
    return Path(__file__).resolve().parent / "static"


STATIC_DIR = _static_dir()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")  # js/idgen.js, js/app.js
SECRET = os.environ.get("IDGEN_SECRET") or None  # необязательный секрет для контрольного кода


class IdInput(BaseModel):
    producer: str = Field(..., min_length=1, description="Производитель (из config/producers.json)")
    location: str = Field(..., min_length=1, description="Место положения")
    company: str = Field(..., min_length=1, description="Компания (из config/companies.json)")
    serial: str = Field(..., min_length=1, description="Серийный номер")
    port: str = Field(..., min_length=1, description="Порт: TN_A / TN_B / TN_C")
    site: str = Field(..., min_length=1, description="Номер площадки (целое число)")


class CodesInput(BaseModel):
    codes: dict[str, str] = Field(..., description='{"Название": "КД"} — 2 символа из алфавита base31')


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    ensure_configs()          # генерируем JSON-справочники, если их ещё нет
    registry.reload()         # перечитываем справочники с диска


@app.post("/api/generate")
def generate(data: IdInput):
    """Генерирует ID формата v6 (дата фиксируется автоматически) и сохраняет во внутреннюю базу."""
    try:
        res = db.register(data.producer, data.location,
                          data.company, data.serial, data.port, data.site,
                          secret=SECRET)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"created": res["created"], "record": res["record"]}


@app.post("/api/preview")
def preview(data: IdInput):
    """Показывает ID без сохранения в базу."""
    try:
        rec = make_id(data.producer, data.location,
                      data.company, data.serial, data.port, data.site,
                      secret=SECRET)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return rec


@app.get("/api/verify")
def verify(id: str = Query(..., description="Код для проверки")):
    """Проверяет код: сначала контрольный символ, затем поиск во внутренней базе."""
    checksum_ok = verify_checksum(id, secret=SECRET)
    record = db.lookup(id) if checksum_ok else None
    full_match = False
    if record:
        try:
            expected = make_id(record["producer"], record["location"],
                               record["company"], record["serial"],
                               record.get("port", ""), record.get("site", ""),
                               dt=record["date"], secret=SECRET)["compact"]
            full_match = expected == record["compact"]
        except ValueError as e:
            # Данные записи несовместимы с текущим форматом (например, площадка вне
            # диапазона после смены формата) — не падаем 500, а честно сообщаем.
            return {
                "input": id, "checksum_valid": checksum_ok,
                "found_in_db": True, "authentic": False,
                "parts": extract_parts(id), "record": record,
                "warning": f"Не удалось пересчитать ID по записям базы: {e}",
            }
    return {
        "input": id,
        "checksum_valid": checksum_ok,
        "found_in_db": bool(record),
        "authentic": bool(record and full_match),
        "parts": extract_parts(id),
        "record": record,
    }


@app.get("/api/list")
def list_records(limit: int = Query(100, ge=1, le=1000)):
    return db.list_ids(limit)


class IdUpdate(BaseModel):
    """Частичное обновление записи: можно передать только изменяемые поля."""
    producer: str | None = None
    location: str | None = None
    company: str | None = None
    serial: str | None = None
    port: str | None = None
    site: str | None = None


@app.put("/api/record/{record_id}")
def update_record(record_id: str, data: IdUpdate):
    """Редактирует запись базы; UID пересчитывается автоматически по новым данным."""
    changes = {k: v for k, v in data.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(400, "Не передано ни одного поля для изменения")
    try:
        res = db.update_record(record_id, changes, secret=SECRET)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "Запись не найдена")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"changed": res["changed"], "record": res["record"]}


@app.delete("/api/record/{record_id}")
def delete_record(record_id: str):
    """Удаляет запись из базы по UID (с разделителями или без)."""
    if not db.delete_record(record_id):
        raise HTTPException(404, f"Запись не найдена: {record_id}")
    return {"deleted": record_id}


@app.get("/api/export")
def export(fmt: str = Query("csv", pattern="^(csv|xlsx)$")):
    """Экспорт всей базы выданных UID в CSV или Excel (.xlsx)."""
    from fastapi.responses import Response
    if fmt == "xlsx":
        data, media, fname = (db.export_xlsx(),
                              "application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet", "ids.xlsx")
    else:
        data, media, fname = db.export_csv(), "text/csv; charset=utf-8", "ids.csv"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------- Справочники кодов (JSON-конфиги) ----------

def _validate_codes(codes: dict[str, str], kind: str = "producers") -> dict[str, str]:
    """Проверяет и нормализует пары {название: код}; возвращает готовые к записи.

    Для kind="ports" код — ровно 1 символ алфавита, иначе — ровно 2.
    """
    from .codes import ALPHABET as A
    need = 1 if kind == "ports" else 2
    out: dict[str, str] = {}
    for name, code in codes.items():
        name = " ".join(str(name).split())
        code = str(code).strip().upper()
        if not name:
            raise HTTPException(400, "Пустое название в справочнике")
        if len(code) != need or any(ch not in A for ch in code):
            raise HTTPException(
                400, f"Код {code!r} для «{name}» некорректен: нужно ровно {need} симв. "
                     f"из алфавита {A} (без неоднозначных 0/O, 1/I/L)")
        out[name] = code
    return out


@app.get("/api/codes")
def get_codes():
    """Текущее содержимое всех трёх справочников (поровый — без ошибок файла)."""
    registry.reload()
    try:
        ports = registry.ports
    except CodesError:
        ports = {"TN_A": "A", "TN_B": "B", "TN_C": "C"}
    return {"producers": registry.producers, "companies": registry.companies,
            "ports": ports}


@app.post("/api/codes/{kind}/add")
def add_codes(kind: str, data: CodesInput):
    """Добавляет/обновляет записи в справочнике. kind: producers | companies | ports."""
    path = {"producers": registry.producers_path,
            "companies": registry.companies_path,
            "ports": registry.ports_path}.get(kind)
    if path is None:
        raise HTTPException(404, "kind должен быть producers, companies или ports")
    added = _validate_codes(data.codes, kind)
    current = dict(load_codes_raw(path, 1 if kind == "ports" else 2))
    current.update(added)
    save_codes(path, current)
    registry.reload()
    return {"file": str(path), "added": added, "total": len(current)}


@app.post("/api/codes/generate")
def generate_codes(data: CodesInput):
    """Генерирует коды сам: по первым буквам названия (транслит кириллицы).

    Например «Ромашка-Завод» -> RZ, «Вектор-Телеком» -> VT. Если букв не
    хватает или они неоднозначны — берёт детерминированный код из хеша имени.
    Возвращает готовые пары {название: код} (в файл НЕ пишет — их можно
    отправить через /api/codes/{kind}/add).
    """
    result = {}
    for name in data.codes:
        name = name.strip()
        if not name:
            continue
        result[name] = suggest_port(name) if kind_hint_ports(data) else suggest_code(name)
    return {"generated": result}


def kind_hint_ports(data: "CodesInput") -> bool:
    """Если все ключи похожи на порты (TN_*) — генерируем односимвольные коды."""
    keys = [k.strip().upper() for k in data.codes if k.strip()]
    return bool(keys) and all(k.startswith("TN_") or k.startswith("PORT_") for k in keys)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
