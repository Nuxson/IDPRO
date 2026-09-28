"""
Формат ID v6 — компактный читаемый код «PP + DWC + CC + порт + площадка + хеш + CRC».

Длина: 19 символов, группировка XXXX-XXXX-XXXX-XXXX-X.
Пример: RM4KF-VT-A2238HYEQ (условно: производитель с кодом RM из config/producers.json;
        компания VT из config/companies.json; порт TN_A = A; площадка 42 -> '223A';
        хеш 3 символа; контрольный код 2 символа)

Разбор компактного кода (до контрольного кода):
  [0:2]  PP    — код производителя ИЗ СПРАВОЧНИКА config/producers.json
                (программа не содержит названий брендов — только готовые коды)
  [2:5]  DWC   — дата: D = номер недели в месяце (дни 1–7 = 1, 8–14 = 2 ...),
                W = месяц (B=Январь ... Q=Декабрь),
                C = день недели (A=Понедельник ... F=Пятница, G=Воскресенье).
                Поле «дата» больше НЕ вводится пользователем — оно фиксируется
                автоматически в момент генерации и хранится во внутренней базе.
  [5:7]  CC    — сокращение компании ИЗ СПРАВОЧНИКА config/companies.json
  [7]    S     — порт: TN_A -> A, TN_B -> B, TN_C -> C
  [8:12] SSSS  — номер площадки, 4 символа compact-алфавита (0..1 336 335)
                (6 -> '2228', 42 -> '223A'); точно декодируется обратно в число.

Далее:
  [12:17] HHHHH — хеш-часть: SHA-256 от канонической строки полей v6
                (5 символов compact-алфавита) — кодирует место положения и серийный номер;
  [17:19] CC   — контрольный код HMAC-SHA256 от тела ID (аналог CRC/IMEI),
                офлайн-проверка без доступа к базе.

Алфавиты: читаемые глазом сегменты (неделя/месяц/день/порт/производитель/компания)
используют base31 (без 0/O, 1/I/L, U, Y); сжатые числовые сегменты (площадка, хеш,
CRC) — compact-алфавит base34 (цифры+A-Z без 0/O, 1/I/L). Точная дата, точные поля и
остальные данные восстанавливаются по внутренней базе (lookup по коду).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import date

from .codes import PORTS_FILE, CodesError, registry as default_registry

# ---------- Справочники ----------
MONTH_CODES = "BCDEFGHJKLMNPQ"        # месяц: B=Январь ... Q=Декабрь (алфавит без 0/O,1/I,L,U,Y)
WEEKDAY_CODES = "ABCDEFG"             # день недели: A=Понедельник ... F=Пятница, G=Воскресенье
PORT_CODES = {"TN_A": "A", "TN_B": "B", "TN_C": "C"}   # порт -> буква

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"           # base31 (нет 0/O, 1/I/L, U, Y)
BASE = len(ALPHABET)
# Дополнительный алфавит для сжатых числовых сегментов (площадка/хеш/CRC):
# полный base36 без неоднозначных 0/O и 1/I/L. Применяется ТОЛЬКО к сегментам,
# которые никогда не декодируются в обратную сторону глазами (число -> код),
# читаемые глазом сегменты (неделя/месяц/день/порт) остаются на base31.
ALNUM36 = "23456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE36 = len(ALNUM36)                                   # 34

SEP = "-"
BLOCK = 4
HASH_LEN = 5                                            # хеш: 5 символов compact-алфавита (~45 млн вариантов)
CHECK_LEN = 2                                           # CRC: 2 символа base36
SITE_LEN = 4                                            # площадка: 4 символа compact-алфавита
PREFIX_LEN = 2 + 3 + 2 + 1 + SITE_LEN                   # PP+DWC+CC+порт+площадка = 12
BODY_LEN = PREFIX_LEN + HASH_LEN                        # 14
TOTAL_LEN = BODY_LEN + CHECK_LEN                        # 19 символов
GROUPS = (4, 4, 4, 4, 3)                                  # группировка XXXX-XXXX-XXXX-XXXX-XXX
# Примечание: компактный формат сознательно короче прежних 20 символов; последний
# блок короткий — осознанный компромисс между длиной и коллизиями.
# Верхняя граница ограничена ёмкостью кода (4 символа compact-алфавита), а не
# круглым числом вроде 99/99999. Единственное исключение — старшие три символа
# кода площадки: если они попали в "сигнатуру" сегмента даты (неделя 1..5 +
# месяц + день недели), 19-символьный код мог бы читаться двусмысленно при
# сдвиге группировки, поэтому такие значения просто не используются.

def _to_base(number: int, length: int, alphabet: str, base: int) -> str:
    out = []
    for _ in range(length):
        number, rem = divmod(number, base)
        out.append(alphabet[rem])
    return "".join(reversed(out))


def to_base31(number: int, length: int) -> str:
    return _to_base(number, length, ALPHABET, BASE)


def to_base36(number: int, length: int) -> str:
    """Кодирование для сжатых сегментов (площадка/хеш/CRC): алфавит 34 символа."""
    return _to_base(number, length, ALNUM36, BASE36)


#: Ёмкость кода площадки и формальная верхняя граница (без перебора!).
SITE_CAPACITY = BASE36 ** SITE_LEN           # 1 336 336
SITE_MAX = SITE_CAPACITY - 1                 # 1 336 335


def is_site_ambiguous(site_num: int) -> bool:
    """True, если код площадки двусмысленен (совпадает с шаблоном сегмента даты DWC).

    Проверка O(1), без перебора всего диапазона: старшие три символа кода
    должны одновременно попадать в алфавиты недели/месяца/дня недели.
    Такие номера зарезервированы и не выдаются, чтобы 19-символьный код
    читался однозначно при любом сдвиге группировки.
    """
    if not 0 <= site_num < SITE_CAPACITY:
        raise ValueError(f"Номер площадки вне ёмкости кода 0..{SITE_MAX}: {site_num!r}")
    a, b, c = (site_num // BASE36 ** 3,
               (site_num // BASE36 ** 2) % BASE36,
               (site_num // BASE36) % BASE36)
    return (a < 5                                   # ALPHABET[:5] = '23456' (недели 1..5)
            and ALNUM36[b] in MONTH_CODES           # месяц B..Q
            and ALNUM36[c] in WEEKDAY_CODES)        # день недели A..G


def site_excluded() -> frozenset[int]:
    """Зарезервированные номера площадок (вычисляются лениво, ~17 тыс. значений)."""
    return frozenset(n for n in range(SITE_CAPACITY) if is_site_ambiguous(n))

FORMAT_VERSION = "v6"

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")

#: Префиксы канонизации для автоподбора кодов. Равны текущей версии формата,
#: поэтому смена FORMAT_VERSION автоматически меняет все ранее предложенные
#: коды (старые справочники не «наследуются» новым форматом по ошибке).
_SUGGEST_PREFIX = "v6"
_PORT_SUGGEST_PREFIX = "v6port"


def _norm(value: str) -> str:
    """Нормализация строки: trim, верхний регистр, схлопывание пробелов."""
    return " ".join((value or "").strip().upper().split())


def producer_code(name: str, registry_obj=None) -> str:
    """Код производителя из справочника config/producers.json.

    Программа не вычисляет коды из названий брендов — соответствия задаёт
    пользователь в JSON-конфиге. Если имени нет в справочнике — CodesError
    с подсказкой, какую запись добавить.
    """
    return (registry_obj or default_registry).producer_code(name)


def company_abbr(name: str, registry_obj=None) -> str:
    """Сокращение компании из справочника config/companies.json."""
    return (registry_obj or default_registry).company_code(name)


_TMAP = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "Z", "З": "Z", "И": "I", "Й": "I", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "H", "Ц": "C", "Ч": "CH", "Ш": "SH", "Щ": "SCH", "Ъ": "",
    "Ы": "Y", "Ь": "", "Э": "E", "Ю": "JU", "Я": "JA",
}


def _translit(text: str) -> str:
    return "".join(_TMAP.get(ch, ch) for ch in text)


def suggest_code(name: str) -> str:
    """Генерирует рекомендуемый 2-символьный код из названия (для заполнения справочника).

    Инициалы слов транслитированного названия («Ромашка-Завод» -> RZ); если
    инициалов меньше двух или они содержат неоднозначные символы (0/O, 1/I/L),
    добираются/заменяются первыми допустимыми буквами, крайний случай —
    детерминированный код из SHA-256 имени. Используется только как подсказка:
    итоговый код всегда фиксируется пользователем в JSON-справочнике.
    """
    n = _norm(name)
    tr = _translit(n.upper())
    words = [w for w in re.split(r"[\s\-]+", tr) if w]
    initials = "".join(w[0] for w in words)
    letters = "".join(ch for ch in initials if ch in ALPHABET)
    if len(letters) < 2:
        extra = [ch for ch in "".join(c for c in tr if c in ALPHABET) if ch not in letters]
        letters += "".join(extra)
    if len(letters) >= 2:
        return letters[:2]
    digest = hashlib.sha256((_SUGGEST_PREFIX + ":" + n).encode()).digest()
    return to_base31(int.from_bytes(digest[:4], "big"), 2)


def port_code(port: str, registry_obj=None) -> str:
    """Код порта из справочника config/ports.json: TN_A -> A и т.д.

    Программа не хранит бренды/порты жёстко — соответствия задаёт пользователь
    в JSON-конфиге (стартовый шаблон: TN_A/TN_B/TN_C). Нормализация: регистр,
    пробелы и любые разделители (_, -, :) не значимы.
    """
    key = re.sub(r"[^A-Z0-9]+", "_", _norm(port)).strip("_")
    try:
        return (registry_obj or default_registry).port_code(key)
    except CodesError as e:
        raise ValueError(str(e)) from e


def suggest_port(name: str) -> str:
    """Рекомендует букву кода порта из названия (для справочника ports.json).

    Берёт первую допустимую букву из транслитерированного имени; если все
    символы имени неоднозначные — детерминированный код из SHA-256.
    """
    tr = _translit(_norm(name))
    for ch in tr:
        if ch in ALPHABET and ch not in PORT_CODES.values():
            return ch
    digest = hashlib.sha256((_PORT_SUGGEST_PREFIX + ":" + _norm(name)).encode()).digest()
    return to_base31(int.from_bytes(digest[:4], "big"), 1)


def site_code(site: str) -> str:
    """Код номера площадки: 4 символа compact-алфавита.

    Площадка не ограничена 99 и не привязана жёстко к цифрам: любое целое
    число 0..BASE36**4-1 (= 1 336 335) кодируется четырьмя символами без
    неоднозначных знаков и точно декодируется обратно
    (6 -> '2228', 42 -> '223A', 0 -> '2222', 99999 -> 'BBHF').
    """
    s = _norm(site).replace(" ", "")
    s = re.sub(r"^(НОМЕР|NOMER|NO|#)", "", s) or s
    if not re.fullmatch(r"\d+", s):
        raise ValueError(f"Некорректный номер площадки: {site!r} (ожидается целое число)")
    num = int(s)
    if not 0 <= num <= SITE_MAX:
        raise ValueError(f"Номер площадки вне допустимого диапазона 0..{SITE_MAX}: {site!r}")
    if is_site_ambiguous(num):
        raise ValueError(
            f"Номер площадки {num} зарезервирован (его код совпадает с шаблоном "
            f"сегмента даты — выберите соседнее значение): {site!r}")
    return to_base36(num, SITE_LEN)


def decode_site(code: str) -> int:
    """Обратный разбор кода площадки ('2228' -> 6, '223A' -> 42)."""
    n = 0
    for ch in code.upper():
        n = n * BASE36 + ALNUM36.index(ch)
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


def checksum(body: str, secret: str | None = None) -> str:
    msg = body.encode()
    if secret:
        digest = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    else:
        digest = hashlib.sha256(msg).digest()
    return to_base36(int.from_bytes(digest[:4], "big"), CHECK_LEN)


def canonical_string(producer: str, iso_date: str, location: str, company: str,
                     serial: str, port: str, site: str) -> str:
    """Каноническая строка формата v6 (версия зафиксирована в префиксе 'v6')."""
    return "|".join([FORMAT_VERSION, producer, iso_date, location, company, serial, port, site])


def short_code(producer: str, iso_date: str, company: str, port: str, site: str,
               registry_obj=None) -> str:
    """Короткий читаемый код вида RM4KF-VT-A-6 (без хеша и CRC) — для площадок/журналов."""
    return SEP.join([
        producer_code(producer, registry_obj) + date_segment(iso_date),
        company_abbr(company, registry_obj),
        port_code(port, registry_obj),
        str(decode_site(site_code(site))),
    ])


def make_id(producer: str, location: str, company: str, serial: str,
            port: str, site: str, secret: str | None = None,
            dt: str | None = None, registry_obj=None) -> dict:
    """Генерирует читаемый ID формата v6. Детерминирован: те же данные → тот же ID.

    Дата НЕ запрашивается у пользователя: фиксируется автоматически (сегодня)
    в момент генерации и сохраняется во внутренней базе; при проверке кода
    точная дата восстанавливается из базы. Параметр `dt` — служебный (тесты).
    Коды производителя и компании берутся из JSON-справочников (app/codes.py);
    при отсутствии имени в справочнике — CodesError с подсказкой.
    """
    reg = registry_obj or default_registry
    producer, iso_location, company, serial, site = (
        _norm(x) for x in (producer, location, company, serial, site))
    port_n = _norm(port)
    missing = [name for name, val in (("производитель", producer), ("место", iso_location),
                                      ("компания", company), ("серийный номер", serial),
                                      ("порт", port_n), ("номер площадки", site)) if not val]
    if missing:
        raise ValueError("Заполните поля: " + ", ".join(missing))
    iso_date = norm_date(dt) if dt else date.today().isoformat()

    # Коды из справочников — до вычисления хеша, чтобы ошибка была понятной.
    pcode = reg.producer_code(producer)
    ccode = reg.company_code(company)

    canon = canonical_string(producer, iso_date, iso_location, company, serial, port_n, site)
    # ВАЖНО: берём 8 байт дайджеста напрямую (int.from_bytes), а НЕ hex[:12] —
    # hex-срезы не совпадают с побайтовым представлением (расхождение Python/JS).
    hash_part = to_base36(int.from_bytes(
        hashlib.sha256(canon.encode()).digest()[:8], "big"), HASH_LEN)

    prefix = (pcode + date_segment(iso_date) + ccode
              + port_code(port_n, reg) + site_code(site))
    body = prefix + hash_part
    full = body + checksum(body, secret)
    blocks, pos = [], 0
    for size in GROUPS:
        blocks.append(full[pos:pos + size])
        pos += size

    return {
        "id": SEP.join(blocks),
        "compact": full,
        "short": short_code(producer, iso_date, company, port_n, site, reg),
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
    if any(ch not in ALNUM36 for ch in compact[:2] + compact[5:PREFIX_LEN]):
        return False                                   # PP, CC+порт+площадка
    if any(ch not in ALNUM36 for ch in compact[PREFIX_LEN:]):
        return False                                   # хеш и контрольный код
    # Сегменты, читаемые глазами, — на base31 (без неоднозначных U/Y):
    if compact[0] not in ALPHABET or compact[1] not in ALPHABET:
        return False
    if compact[5] not in ALPHABET or compact[6] not in ALPHABET:
        return False
    # Дата: D = неделя месяца (1..5), W = месяц, C = день недели.
    if compact[2] not in ALPHABET[:5]:
        return False
    if compact[3] not in MONTH_CODES or compact[4] not in WEEKDAY_CODES:
        return False
    if compact[7] not in _port_names():                # буква порта из ports.json
        return False
    # Площадка обязана декодироваться в допустимый диапазон (ловит подмену
    # старших символов кода площадки на шаблон даты — позиции 2–4).
    try:
        n = decode_site(compact[8:PREFIX_LEN])
    except ValueError:
        return False
    if not 0 <= n <= SITE_MAX:
        return False
    body, check = compact[:BODY_LEN], compact[BODY_LEN:]
    return checksum(body, secret) == check


def _port_names() -> dict[str, str]:
    """Обратная таблица буква ID -> название порта из справочника (без ошибок)."""
    try:
        ports = default_registry.ports
    except Exception:
        ports = dict(PORT_CODES)
    return {v: k for k, v in ports.items()}


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
        parts["site_number"] = decode_site(c[8:PREFIX_LEN]) if len(c) >= PREFIX_LEN else None
    except ValueError:
        parts["site_number"] = None
    parts["port_name"] = _port_names().get(c[7])
    return parts
