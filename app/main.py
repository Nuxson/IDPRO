"""REST API + веб-интерфейс генерации и проверки уникальных ID."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db
from .idgen import extract_parts, make_id, verify_checksum

app = FastAPI(title="Unique ID Generator", version="1.0")

STATIC_DIR = Path(__file__).resolve().parent / "static"
SECRET = os.environ.get("IDGEN_SECRET") or None  # необязательный секрет для контрольного кода


class IdInput(BaseModel):
    producer: str = Field(..., min_length=1, description="Производитель")
    date: str = Field(..., min_length=5, description="Дата (ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)")
    location: str = Field(..., min_length=1, description="Место положения")
    company: str = Field(..., min_length=1, description="Компания")
    serial: str = Field(..., min_length=1, description="Серийный номер")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.post("/api/generate")
def generate(data: IdInput):
    """Генерирует ID и сохраняет его во внутреннюю базу."""
    try:
        res = db.register(data.producer, data.date, data.location,
                          data.company, data.serial, secret=SECRET)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"created": res["created"], "record": res["record"]}


@app.post("/api/preview")
def preview(data: IdInput):
    """Показывает ID без сохранения в базу."""
    try:
        rec = make_id(data.producer, data.date, data.location,
                      data.company, data.serial, secret=SECRET)
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
            expected = make_id(record["producer"], record["date"], record["location"],
                               record["company"], record["serial"], secret=SECRET)["compact"]
            full_match = expected == record["compact"]
        except ValueError:
            pass
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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
