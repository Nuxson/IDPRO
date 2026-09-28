"""
Генерация уникальных идентификационных кодов из вводных данных.

Формат ID: XXXX-XXXX-XXXX-CCKK  (14 символов, блоки по 4)

Состав кода:
  [0:2]   префикс производителя (транслитерация, 2 символа)
  [2:4]   префикс компании
  [4:6]   префикс места положения
  [6:12]  хеш-часть SHA-256 от канонической строки всех полей (base31)
  [12:14] контрольный код (аналог CRC/контрольной суммы IMEI) — HMAC-SHA256 от тела ID

Алфавит исключает неоднозначные символы (нет 0/O, 1/I/L), как в реальных серийных номерах.

Проверка подлинности бывает двух видов:
  1) офлайн-проверка контрольного кода (без базы, защита от опечаток);
  2) полная верификация — восстановить ID по исходным полям и сверить с базой.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date

# Алфавит без неоднозначных символов (нет 0, O, 1, I, L)
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 31 символ
BASE = len(ALPHABET)

SEP = "-"
BLOCK = 4
CHECK_LEN = 2        # длина контрольного кода
HASH_LEN = 6         # длина хеш-части
PREFIX_LEN = 2       # длина каждого префикса
BODY_LEN = PREFIX_LEN * 3 + HASH_LEN          # 12
TOTAL_LEN = BODY_LEN + CHECK_LEN              # 14

_NON_ALNUM = re.compile(r"[^A-Z0-9]")

_TMAP = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "Z", "З": "Z", "И": "K", "Й": "K", "К": "K", "Л": "Q", "М": "M",
    "Н": "N", "О": "Q", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "H", "Ц": "C", "Ч": "C", "Ш": "W", "Щ": "W", "Ъ": "Q",
    "Ы": "Y", "Ь": "Q", "Э": "E", "Ю": "U", "Я": "U",
}


def _norm(value: str) -> str:
    """Нормализация строки: trim, верхний регистр, схлопывание пробелов."""
    return " ".join((value or "").strip().upper().split())


def _translit(text: str) -> str:
    return "".join(_TMAP.get(ch, ch) for ch in text)


def _prefix(text: str, n: int = PREFIX_LEN) -> str:
    """Читаемый префикс: транслит + только символы из ALPHABET, добивка 'Q'."""
    cleaned = _NON_ALNUM.sub("", _translit(_norm(text)))
    p = "".join(ch if ch in ALPHABET else "Q" for ch in cleaned[:n])
    return p.ljust(n, "Q")


def _norm_date(value: str) -> str:
    """Приведение даты к ISO (YYYY-MM-DD). Принимает ГГГГ-ММ-ДД, ДД.ММ.ГГГГ, ДД-ММ-ГГГГ, ГГГГ/ММ/ДД."""
    v = _norm(value).replace("/", "-").replace(".", "-")
    m = re.fullmatch(r"(\d{1,4})-(\d{1,2})-(\d{1,4})", v)
    if not m:
        raise ValueError(f"Некорректный формат даты: {value!r} (ожидается ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)")
    a, b, c = m.group(1), int(m.group(2)), int(m.group(3))
    if len(a) == 4:            # ISO: ГГГГ-ММ-ДД
        y, mo, d = int(a), b, c
    else:                      # ДД-ММ-ГГГГ
        d, mo, y = int(a), b, c
    try:
        return date(y, mo, d).isoformat()
    except ValueError as e:
        raise ValueError(f"Несуществующая дата: {value!r}") from e


def to_base31(number: int, length: int) -> str:
    """Представление числа в алфавите ALPHABET фиксированной длины."""
    out = []
    for _ in range(length):
        number, rem = divmod(number, BASE)
        out.append(ALPHABET[rem])
    return "".join(reversed(out))


def checksum(body: str, secret: str | None = None) -> str:
    """Контрольный код: HMAC-SHA256 (или SHA256 без секрета) от тела ID, 2 символа base31."""
    msg = body.encode()
    if secret:
        digest = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    else:
        digest = hashlib.sha256(msg).digest()
    return to_base31(int.from_bytes(digest[:4], "big"), CHECK_LEN)


def canonical_string(producer: str, iso_date: str, location: str, company: str, serial: str) -> str:
    """Каноническая строка для хеширования (порядок полей зафиксирован версией v1)."""
    return "|".join(["v1", producer, iso_date, location, company, serial])


def make_id(producer: str, dt: str, location: str, company: str, serial: str,
            secret: str | None = None) -> dict:
    """Генерирует уникальный ID из пяти полей. Детерминирован: те же данные → тот же ID."""
    producer, location, company, serial = (_norm(x) for x in (producer, location, company, serial))
    missing = [name for name, val in (("производитель", producer), ("место", location),
                                      ("компания", company), ("серийный номер", serial)) if not val]
    if missing:
        raise ValueError("Заполните поля: " + ", ".join(missing))
    iso_date = _norm_date(dt)

    hash_part = to_base31(int(hashlib.sha256(
        canonical_string(producer, iso_date, location, company, serial).encode()
    ).hexdigest()[:16], 16), HASH_LEN)

    body = _prefix(producer) + _prefix(company) + _prefix(location) + hash_part
    full = body + checksum(body, secret)

    blocks = [full[i:i + BLOCK] for i in range(0, len(full), BLOCK)]
    code = SEP.join(blocks)

    return {
        "id": code,
        "compact": full,
        "canonical": canonical_string(producer, iso_date, location, company, serial),
        "fields": {"producer": producer, "date": iso_date, "location": location,
                   "company": company, "serial": serial},
    }


def normalize_id(raw: str) -> str:
    """Убирает разделители/пробелы, приводит к верхнему регистру."""
    s = re.sub(r"[\s\-_]+", "", raw or "").upper()
    return "".join(ch for ch in s if ch.isalnum())


def verify_checksum(raw_id: str, secret: str | None = None) -> bool:
    """Офлайн-проверка контрольного кода ID (без доступа к базе). Аналог проверки IMEI."""
    compact = normalize_id(raw_id)
    if len(compact) != TOTAL_LEN:
        return False
    if any(ch not in ALPHABET for ch in compact):
        return False
    body, check = compact[:BODY_LEN], compact[BODY_LEN:]
    return checksum(body, secret) == check


def extract_parts(raw_id: str) -> dict:
    """Разбор ID на читаемые составляющие (префиксы, хеш, контрольный код)."""
    compact = normalize_id(raw_id)
    return {
        "producer_prefix": compact[0:2],
        "company_prefix": compact[2:4],
        "location_prefix": compact[4:6],
        "hash": compact[6:BODY_LEN],
        "checksum": compact[BODY_LEN:],
    }
