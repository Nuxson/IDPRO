"""
Формат ID v4 — читаемый код вида «PP + DWC + CC + порт + площадка + хеш + CRC».

Пример короткого кода: ER2HF-MS-A-6
  Ericsson, 2-я неделя августа, пятница (дата подставляется автоматически при
  генерации — сегодня; при проверке восстанавливается из внутренней базы),
  компания Масштаб-Связь (MS), порт TN_A (A), номер площадки 6 (код '7').

Разбор компактного кода (до контрольного кода):
  [0:2]  PP   — код производителя (транслит; Ericsson -> ER)
  [2:5]  DWC  — дата: D = номер недели в месяце (дни 1–7 = 1, 8–14 = 2 ...),
               W = месяц (B=Январь ... Q=Декабрь),
               C = день недели (A=Понедельник ... F=Пятница, G=Воскресенье).
               Поле «дата» больше НЕ вводится пользователем — оно фиксируется
               автоматически в момент генерации и хранится во внутренней базе.
  [5:7]  CC   — читаемое сокращение компании по инициалам слов
               (Масштаб-Связь -> MS; нечитаемые символы заменяются на Q)
  [7]    S    — порт: TN_A -> A, TN_B -> B, TN_C -> C
  [8:13] S    — номер площадки, base31, ровно 5 символов (0 .. 99999):
               6 -> '22228', 42 -> '2223D'; читается как младшие цифры номера.

Все символы ID принадлежат алфавиту base31 (без неоднозначных 0/O, 1/I/L, U, Y),
поэтому офлайн-проверка отлавливает любую опечатку. Символы недель/месяцев/дней
читаются по справочникам выше; точная дата, точный номер площадки и остальные
поля восстанавливаются по внутренней базе (lookup по коду).

Далее:
  хеш-часть   — SHA-256 от канонической строки полей v4 (base31, 6 симв.) —
                кодирует место положения и серийный номер (уникальность);
  контрольный — HMAC-SHA256 от тела ID (аналог CRC/IMEI), офлайн-проверка.

Полный вид с группировкой по 4 символа: ER2H-FMSA-22228-HASH-XXYZ
(префикс 13 + хеш 6 + CRC 2 = 21 символ).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date

# ---------- Справочники ----------
MONTH_CODES = "BCDEFGHJKLMNPQ"        # месяц: B=Январь ... Q=Декабрь (алфавит без 0/O,1/I,L,U,Y)
WEEKDAY_CODES = "ABCDEFG"             # день недели: A=Понедельник ... F=Пятница, G=Воскресенье
PORT_CODES = {"TN_A": "A", "TN_B": "B", "TN_C": "C"}   # порт -> буква

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"           # base31 (нет 0/O, 1/I/L, U, Y)
BASE = len(ALPHABET)

SEP = "-"
BLOCK = 4
HASH_LEN = 6
CHECK_LEN = 2
SITE_LEN = 5                                          # номер площадки: ровно 5 символов base31
PREFIX_LEN = 2 + 3 + 2 + 1 + SITE_LEN                 # PP+DWC+CC+порт+площадка = 13
BODY_LEN = PREFIX_LEN + HASH_LEN                      # 19
TOTAL_LEN = BODY_LEN + CHECK_LEN                      # 21

# Читаемые коды производителей по умолчанию (расширяются без смены формата).
PRODUCER_ALIASES = {
    "ERICSSON": "ER", "NOKIA": "NO", "SIEMENS": "SI", "HUAWEI": "HW",
    "SAMSUNG": "SA", "ROBOTECH": "RQ",
}

# Читаемые сокращения компаний по инициалам слов: MASHTAB-SVYAZ -> MS.
COMPANY_ALIASES = {
    "МАСШТАБ-СВЯЗЬ": "MS",
    "MASHTAB-SVYAZ": "MS", "MASHATAB-SVYAZ": "MS",
}


_TMAP = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "Z", "З": "Z", "И": "I", "Й": "I", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "H", "Ц": "C", "Ч": "CH", "Ш": "SH", "Щ": "SCH", "Ъ": "",
    "Ы": "Y", "Ь": "", "Э": "E", "Ю": "JU", "Я": "JA",
}


def _translit(text: str) -> str:
    return "".join(_TMAP.get(ch, ch) for ch in text)


def _readable(text: str) -> str:
    """Транслитерирует кириллицу в латиницу и оставляет только символы алфавита base31.

    Эрикссон -> ERICCCOH -> 'ER'; Nokia -> NOKIA -> 'NK' (O вырезается как неоднозначная).
    Так код производителя всегда читается и при этом проходит офлайн-валидацию ID.
    """
    t = _translit(text.upper())
    return "".join(ch for ch in t if ch in ALPHABET)


_CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _norm(value: str) -> str:
    """Нормализация строки: trim, верхний регистр, схлопывание пробелов.

    Транслит кириллицы в латиницу НЕ применяется к канонической строке —
    иначе «Москва» и «Moscow» дали бы один ID при разных исходных данных.
    Для читаемых кодов производителя/компании используется _readable().
    """
    return " ".join((value or "").strip().upper().split())


def producer_code(name: str) -> str:
    """Читаемый код производителя: первые 2 буквы транслита (Ericsson -> ER, Эрикссон -> ER).

    Приоритет: словарь PRODUCER_ALIASES → первые 2 допустимые буквы транслита →
    детерминированный 2-символьный код из base31 (стабилен для одного имени).
    """
    n = _norm(name)
    if n in PRODUCER_ALIASES:
        return PRODUCER_ALIASES[n]
    letters = _readable(n)
    if len(letters) >= 2:
        return letters[:2]
    digest = hashlib.sha256(("v3p:" + n).encode()).digest()
    return to_base31(int.from_bytes(digest[:4], "big"), 2)


def company_abbr(name: str) -> str:
    """Читаемое сокращение компании по инициалам слов (Масштаб-Связь -> MS).

    Используется в сегменте CC внутри ID; нечитаемые в алфавите буквы
    (I, O, U, Y) заменяются на Q, одиночное слово дополняется до 2 символов.
    """
    n = _norm(name)
    if n in COMPANY_ALIASES:
        return COMPANY_ALIASES[n]
    # Транслит всей строки, инициалы слов (первые буквы), затем только символы base31.
    # Масштаб-Связь -> MASHTAB-SVYAZ -> MS; ИнвестГрупп -> InvestGroup -> 'V' + добор 'Q'.
    # Инициалы слов транслитированного названия: МАСШТАБ-СВЯЗЬ -> MASHTAB-SVYAZ -> MS.
    tr = _translit(n)
    words = [w for w in re.split(r"[\s\-]+", tr) if w]
    initials = "".join(w[0] for w in words)
    readable = "".join(ch for ch in initials if ch in ALPHABET)
    if len(readable) < 2:
        extra = [ch for ch in _readable(n) if ch not in readable]
        readable += "".join(extra)
    return readable[:2].ljust(2, "Q")


def port_code(port: str) -> str:
    """Код порта: TN_A -> A, TN_B -> B, TN_C -> C (регистр/разделители не важны)."""
    key = re.sub(r"[^A-Z0-9]", "_", _norm(port))
    if key in PORT_CODES:
        return PORT_CODES[key]
    compact = key.replace("_", "")
    if compact in PORT_CODES:
        return PORT_CODES[compact]
    raise ValueError(f"Неизвестный порт: {port!r} (допустимо: TN_A, TN_B, TN_C)")


def site_code(site: str) -> str:
    """Код номера площадки: ровно 5 символов base31, диапазон 0..99999.

    6 -> '22228', 42 -> '2223D', 0 -> '22222'. Младшие символы читаются как
    номер; точное значение всегда восстанавливается из внутренней базы.
    """
    s = _norm(site).replace(" ", "")
    s = re.sub(r"^(НОМЕР|NOMER|NO|#)", "", s) or s
    if not re.fullmatch(r"\d+", s):
        raise ValueError(f"Некорректный номер площадки: {site!r} (ожидается число 0..99999)")
    num = int(s)
    if not 0 <= num <= 99999:
        raise ValueError(f"Номер площадки вне диапазона 0..99999: {site!r}")
    return to_base31(num, SITE_LEN)


def decode_site(code: str) -> int:
    """Обратный разбор 5-символьного кода площадки ('22228' -> 6, '2223D' -> 42)."""
    n = 0
    for ch in code.upper():
        n = n * BASE + ALPHABET.index(ch)
    return n


def week_symbol(week_in_month: int) -> str:
    """Символ номера недели в месяце из алфавита base31: 1->'2', 5->'6'."""
    if not 1 <= week_in_month <= 5:
        raise ValueError(f"Недель в месяце не бывает {week_in_month}")
    return ALPHABET[week_in_month - 1]


def week_from_symbol(sym: str) -> int:
    return ALPHABET.index(sym.upper()) + 1


def _week_of_month(d: date) -> int:
    """Номер недели внутри месяца (1..5): неделя 1 = дни 1–7, неделя 2 = 8–14 и т.д."""
    return (d.day - 1) // 7 + 1


def date_segment(iso_date: str) -> str:
    """Сегмент даты DWC: номер недели в месяце + месяц + день недели (напр. 2HF).

    Все символы — из алфавита base31 (без 0/O, 1/I/L): неделя 1->'2', ... 5->'6';
    месяц B=Январь ... Q=Декабрь; день недели A=Пн ... F=Пт, G=Вс.
    Пример: 07.08.2026 (пятница) -> '2' (2-я неделя августа, дни 8–14) + 'H' (Август)
    + 'F' (Пятница) = 2HF. Диапазон кодировки — 2020–2073 годы.
    """
    d = date.fromisoformat(iso_date)
    if not date(2020, 1, 1) <= d <= date(2073, 12, 31):
        raise ValueError(f"Дата вне диапазона кодировки (2020–2073): {iso_date}")
    return f"{week_symbol(_week_of_month(d))}{MONTH_CODES[d.month - 1]}{WEEKDAY_CODES[d.isoweekday() - 1]}"


def decode_date_segment(seg: str, ref_iso: str | None = None) -> dict:
    """Обратный разбор сегмента DWC → месяц/день недели/номер недели в месяце.

    Точная дата восстанавливается при известном годе из базы (ref_iso), иначе
    год подбирается как ближайший прошедший, для которого сегмент непротиворечив.
    """
    wcode, mcode, ccode = seg.upper()
    if wcode not in ALPHABET[:5] or mcode not in MONTH_CODES or ccode not in WEEKDAY_CODES:
        raise ValueError(f"Некорректный сегмент даты: {seg!r}")
    month = MONTH_CODES.index(mcode) + 1
    weekday = WEEKDAY_CODES.index(ccode) + 1
    week_in_month = week_from_symbol(wcode)
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
            if date_segment(ref_iso) == seg.upper():
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
    """Приведение даты к ISO (YYYY-MM-DD). Основной формат ввода — ДД.ММ.ГГГГ (DD:MM:YYYY).

    Дополнительно принимаются ГГГГ-ММ-ДД, ДД-ММ-ГГГГ, ГГГГ:ММ:ДД, ДД/ММ/ГГГГ.
    """
    v = _norm(value).replace("/", "-").replace(".", "-").replace(":", "-")
    m = re.fullmatch(r"(\d{1,4})-(\d{1,2})-(\d{1,4})", v)
    if not m:
        raise ValueError(f"Некорректный формат даты: {value!r} (ожидается ДД.ММ.ГГГГ или DD:MM:YYYY)")
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


def canonical_string(producer: str, iso_date: str, location: str, company: str,
                     serial: str, port: str, site: str) -> str:
    """Каноническая строка формата v4 (версия зафиксирована в префиксе 'v4')."""
    return "|".join(["v4", producer, iso_date, location, company, serial, port, site])


def short_code(producer: str, iso_date: str, company: str, port: str, site: str) -> str:
    """Короткий читаемый код вида ER2HF-MS-A-6 (без хеша и CRC) — для площадок/журналов."""
    return SEP.join([
        producer_code(producer) + date_segment(iso_date),
        company_abbr(company),
        port_code(port),
        str(decode_site(site_code(site))),
    ])


def make_id(producer: str, location: str, company: str, serial: str,
            port: str, site: str, secret: str | None = None,
            dt: str | None = None) -> dict:
    """Генерирует читаемый ID формата v4. Детерминирован: те же данные → тот же ID.

    Дата НЕ запрашивается у пользователя: фиксируется автоматически (сегодня)
    в момент генерации и сохраняется во внутренней базе; при проверке кода
    точная дата восстанавливается из базы. Параметр `dt` — служебный (тесты).
    """
    producer, iso_location, company, serial, site = (
        _norm(x) for x in (producer, location, company, serial, site))
    port_n = _norm(port)
    missing = [name for name, val in (("производитель", producer), ("место", iso_location),
                                      ("компания", company), ("серийный номер", serial),
                                      ("порт", port_n), ("номер площадки", site)) if not val]
    if missing:
        raise ValueError("Заполните поля: " + ", ".join(missing))
    iso_date = norm_date(dt) if dt else date.today().isoformat()

    canon = canonical_string(producer, iso_date, iso_location, company, serial, port_n, site)
    hash_part = to_base31(int(hashlib.sha256(canon.encode()).hexdigest()[:16], 16), HASH_LEN)

    prefix = (producer_code(producer) + date_segment(iso_date) + company_abbr(company)
              + port_code(port_n) + site_code(site))
    body = prefix + hash_part
    full = body + checksum(body, secret)
    blocks = [full[i:i + BLOCK] for i in range(0, len(full), BLOCK)]

    return {
        "id": SEP.join(blocks),
        "compact": full,
        "short": short_code(producer, iso_date, company, port_n, site),
        "canonical": canon,
        "date_segment": body[2:5],
        "decoded_date": decode_date_segment(body[2:5], ref_iso=iso_date),
        "fields": {"producer": producer, "date": iso_date, "location": iso_location,
                   "company": company, "serial": serial, "port": port_n, "site": site},
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
    if compact[2] not in ALPHABET[:5]:                 # неделя в месяце 1..5
        return False
    if compact[3] not in MONTH_CODES or compact[4] not in WEEKDAY_CODES:
        return False
    if compact[7] not in "ABC":                        # порт TN_A/TN_B/TN_C
        return False
    body, check = compact[:BODY_LEN], compact[BODY_LEN:]
    return checksum(body, secret) == check


def extract_parts(raw_id: str) -> dict:
    """Разбор ID на читаемые составляющие."""
    c = normalize_id(raw_id)
    parts = {
        "producer_prefix": c[0:2],
        "date_segment": c[2:5],
        "company_abbr": c[5:7],
        "port": c[7],
        "site_code": c[8:PREFIX_LEN],
        "hash": c[PREFIX_LEN:BODY_LEN],
        "checksum": c[BODY_LEN:],
    }
    try:
        parts["date_decoded"] = decode_date_segment(c[2:5])
    except ValueError:
        parts["date_decoded"] = None
    try:
        parts["site_number"] = decode_site(c[8:PREFIX_LEN])
    except ValueError:
        parts["site_number"] = None
    parts["port_name"] = {v: k for k, v in PORT_CODES.items()}.get(c[7])
    return parts
