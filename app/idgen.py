"""
Формат ID v2 — читаемый код вида «PPPDWC + хеш + CRC».

Пример: ER1AF-3V9K-QPZT-HD  для Ericsson, 1-я неделя августа, пятница.

Разбор первых 5 символов (сегмент производителя и даты):
  [0:2]  PP   — код производителя (первые 2 буквы транслита; при коллизии — 3-я буква)
  [2]    D    — номер недели (1..9, затем A,B,C,D,E; всего 53 значения)
  [3]    W    — месяц: A=Январь ... L=Декабрь
  [4]    C    — день недели: A=Понедельник ... G=Воскресенье

Далее:
  [5:11]       — хеш-часть SHA-256 от канонической строки полей v2 (base31, 6 симв.)
  [11:13]      — контрольный код HMAC-SHA256 от тела ID (аналог CRC/IMEI)

Компания и место положения кодируются внутри хеш-части: они влияют на код
(изменение любого поля меняет ID), но не читаются глазами. Расшифровка всех
полей — по внутренней базе (lookup по коду).

Алфавит base31 без неоднозначных символов (нет 0/O, 1/I/L).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date, timedelta

# ---------- Справочники ----------
WEEK_CODES = "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"          # 34 символа (без I,O,U,Y)
MONTH_CODES = "ABCDEFGHIJKL"                              # A=Январь ... L=Декабрь
WEEKDAY_CODES = "GABCDEF"                                 # A=Понедельник ... G=Воскресенье

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"              # base31 для хеша/CRC
BASE = len(ALPHABET)

SEP = "-"
BLOCK = 4
HASH_LEN = 6
CHECK_LEN = 2
BODY_LEN = 5 + HASH_LEN                                   # 11
TOTAL_LEN = BODY_LEN + CHECK_LEN                          # 13

_NON_ALNUM = re.compile(r"[^A-Z0-9]")

_TMAP = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "Z", "З": "Z", "И": "I", "Й": "I", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "H", "Ц": "C", "Ч": "CH", "Ш": "SH", "Щ": "SCH", "Ъ": "",
    "Ы": "Y", "Ь": "", "Э": "E", "Ю": "JU", "Я": "JA",
}


# Читаемые коды производителей по умолчанию (расширяются без смены формата).
PRODUCER_ALIASES = {
    "ERICSSON": "ER", "NOKIA": "NO", "SIEMENS": "SI", "HUAWEI": "HW",
    "SAMSUNG": "SA", "ROBOTECH": "RQ",
}

# Читаемые сокращения компаний по инициалам слов: MASHTAB-SVYAZ -> MS.
COMPANY_ALIASES = {
    "MASHTAB-SVYAZ": "MS", "MASHATAB-SVYAZ": "MS",
}


def _norm(value: str) -> str:
    """Нормализация строки: транслит, trim, верхний регистр, схлопывание пробелов."""
    return " ".join(_translit((value or "").strip().upper()).split())


def _translit(text: str) -> str:
    return "".join(_TMAP.get(ch, ch) for ch in text)


def producer_code(name: str) -> str:
    """Читаемый код производителя: первые 2 буквы транслита (Ericsson -> ER).

    Приоритет: словарь PRODUCER_ALIASES → первые 2 допустимые буквы транслита →
    детерминированный 2-символьный код из base31 (стабилен для одного имени).
    """
    n = _norm(name)
    if n in PRODUCER_ALIASES:
        return PRODUCER_ALIASES[n]
    letters = _NON_ALNUM.sub("", _translit(n))
    if len(letters) >= 2 and all(ch in ALPHABET for ch in letters[:2]):
        return letters[:2]
    digest = hashlib.sha256(("v2p:" + n).encode()).digest()
    return to_base31(int.from_bytes(digest[:4], "big"), 2)


def company_abbr(name: str) -> str:
    """Читаемое сокращение компании по инициалам слов (Масштаб-Связь -> МС, MASHTAB-SVYAZ -> MS).

    Используется в подсказках UI; в самом ID компания участвует через хеш-часть.
    """
    n = _norm(name)
    if n in COMPANY_ALIASES:
        return COMPANY_ALIASES[n]
    words = re.split(r"[\s\-]+", _translit(n))
    initials = "".join(w[0] for w in words if w)
    cleaned = "".join(ch if ch in ALPHABET else "Q" for ch in initials)[:2]
    return cleaned.ljust(2, "Q")


def _week_of_month(d: date) -> int:
    """Номер недели внутри месяца (1..5): неделя 1 = дни 1–7, неделя 2 = 8–14 и т.д."""
    return (d.day - 1) // 7 + 1


def date_segment(iso_date: str) -> str:
    """Сегмент даты DWC: номер недели в месяце + месяц + день недели (напр. 1AF).

    Пример: 2026-08-07 (пятница) -> '1' (1-я неделя августа, дни 1–7) + 'H' (Август)
    + 'F' (Пятница) = 1HF. Диапазон кодировки — 2020–2073 годы.
    """
    d = date.fromisoformat(iso_date)
    if not date(2020, 1, 1) <= d <= date(2073, 12, 31):
        raise ValueError(f"Дата вне диапазона кодировки v2 (2020–2073): {iso_date}")
    wcode = WEEK_CODES[_week_of_month(d) - 1]
    return f"{wcode}{MONTH_CODES[d.month - 1]}{WEEKDAY_CODES[d.isoweekday() - 1]}"


def decode_date_segment(seg: str, ref_iso: str | None = None) -> dict:
    """Обратный разбор сегмента DWC → месяц/день недели/номер недели в месяце.

    Точная дата восстанавливается при известном годе из базы (ref_iso), иначе
    год подбирается как ближайший прошедший, для которого сегмент непротиворечив.
    """
    wcode, mcode, ccode = seg.upper()
    if wcode not in WEEK_CODES[:5] or mcode not in MONTH_CODES or ccode not in WEEKDAY_CODES:
        raise ValueError(f"Некорректный сегмент даты: {seg!r}")
    month = MONTH_CODES.index(mcode) + 1
    weekday = WEEKDAY_CODES.index(ccode) + 1
    week_in_month = WEEK_CODES.index(wcode) + 1
    info: dict = {
        "week_symbol": wcode,
        "week_in_month": week_in_month,
        "month": month,
        "month_name": ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                       "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"][month - 1],
        "weekday": weekday,
        "weekday_name": ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница",
                         "Суббота", "Воскресенье"][weekday - 1],
    }
    # подбор дня: d такой, что _week_of_month(d)==week_in_month и d.isoweekday()==weekday
    def day_for(year: int) -> str | None:
        for day in range(1, 32):
            try:
                cand = date(year, month, day)
            except ValueError:
                break
            if cand.isoweekday() == weekday and _week_of_month(cand) == week_in_month:
                return cand.isoformat()
        return None

    year = None
    date_approx = None
    if ref_iso:
        try:
            d = date.fromisoformat(ref_iso)
            year = d.year
            if date_segment(ref_iso)[2:5] == seg.upper():
                date_approx = ref_iso
        except ValueError:
            pass
    if date_approx is None:
        today = date.today()
        for cand_year in range(min(today.year, 2073), 2019, -1):   # ближайшее прошлое
            da = day_for(cand_year)
            if da and date.fromisoformat(da) <= today:
                date_approx, year = da, cand_year
                break
    info["year"] = year
    info["date_approx"] = date_approx
    return info


def norm_date(value: str) -> str:
    """Приведение даты к ISO (YYYY-MM-DD). Принимает ГГГГ-ММ-ДД, ДД.ММ.ГГГГ, ДД-ММ-ГГГГ, ГГГГ/ММ/ДД."""
    v = _norm(value).replace("/", "-").replace(".", "-")
    m = re.fullmatch(r"(\d{1,4})-(\d{1,2})-(\d{1,4})", v)
    if not m:
        raise ValueError(f"Некорректный формат даты: {value!r} (ожидается ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)")
    a, b, c = m.group(1), int(m.group(2)), int(m.group(3))
    if len(a) == 4:
        y, mo, d = int(a), b, c
    else:
        d, mo, y = int(a), b, c
    try:
        return date(y, mo, d).isoformat()
    except ValueError as e:
        raise ValueError(f"Несуществующая дата: {value!r}") from e


def to_base31(number: int, length: int) -> str:
    out = []
    for _ in range(length):
        number, rem = divmod(number, BASE)
        out.append(ALPHABET[rem])
    return "".join(reversed(out))


def checksum(body: str, secret: str | None = None) -> str:
    msg = body.encode()
    if secret:
        digest = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    else:
        digest = hashlib.sha256(msg).digest()
    return to_base31(int.from_bytes(digest[:4], "big"), CHECK_LEN)


def canonical_string(producer: str, iso_date: str, location: str, company: str, serial: str) -> str:
    """Каноническая строка формата v2 (версия зафиксирована в префиксе 'v2')."""
    return "|".join(["v2", producer, iso_date, location, company, serial])


def make_id(producer: str, dt: str, location: str, company: str, serial: str,
            secret: str | None = None) -> dict:
    """Генерирует читаемый ID формата v2. Детерминирован: те же данные → тот же ID."""
    producer, location, company, serial = (_norm(x) for x in (producer, location, company, serial))
    missing = [name for name, val in (("производитель", producer), ("место", location),
                                      ("компания", company), ("серийный номер", serial)) if not val]
    if missing:
        raise ValueError("Заполните поля: " + ", ".join(missing))
    iso_date = norm_date(dt)

    canon = canonical_string(producer, iso_date, location, company, serial)
    hash_part = to_base31(int(hashlib.sha256(canon.encode()).hexdigest()[:16], 16), HASH_LEN)

    body = producer_code(producer) + date_segment(iso_date) + hash_part
    full = body + checksum(body, secret)
    blocks = [full[i:i + BLOCK] for i in range(0, len(full), BLOCK)]

    return {
        "id": SEP.join(blocks),
        "compact": full,
        "canonical": canon,
        "date_segment": body[2:5],
        "decoded_date": decode_date_segment(body[2:5], ref_iso=iso_date),
        "fields": {"producer": producer, "date": iso_date, "location": location,
                   "company": company, "serial": serial},
    }


def normalize_id(raw: str) -> str:
    s = re.sub(r"[\s\-_]+", "", raw or "").upper()
    return "".join(ch for ch in s if ch.isalnum())


def verify_checksum(raw_id: str, secret: str | None = None) -> bool:
    """Офлайн-проверка контрольного кода ID (без доступа к базе)."""
    compact = normalize_id(raw_id)
    if len(compact) != TOTAL_LEN:
        return False
    if any(ch not in ALPHABET for ch in compact):
        return False
    if compact[2] not in WEEK_CODES or compact[3] not in MONTH_CODES or compact[4] not in WEEKDAY_CODES:
        return False
    body, check = compact[:BODY_LEN], compact[BODY_LEN:]
    return checksum(body, secret) == check


def extract_parts(raw_id: str) -> dict:
    """Разбор ID на читаемые составляющие."""
    c = normalize_id(raw_id)
    parts = {
        "producer_prefix": c[0:2],
        "date_segment": c[2:5],
        "hash": c[5:BODY_LEN],
        "checksum": c[BODY_LEN:],
    }
    try:
        parts["date_decoded"] = decode_date_segment(c[2:5])
    except ValueError:
        parts["date_decoded"] = None
    return parts
