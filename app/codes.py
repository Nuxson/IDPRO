"""Справочники кодов производителей и компаний (JSON-конфиги).

Программа НЕ содержит жёстко зашитых названий брендов/компаний. Все читаемые
коды берутся из внешних конфигурационных файлов:

    config/producers.json   — производитель -> код (2 символа), напр. "РОМАШКА": "RM"
    config/companies.json   — компания -> сокращение (2 символа), напр. "Вектор-Телеком": "VT"

Файлы генерируются самой программой в момент первого запуска (или командой
`python cli.py codes-init`) и дальше редактируются пользователем. При создании
ID готовые коды подставляются в код; если имени в справочнике нет — программа
не угадывает бренд, а возвращает ошибку с подсказкой по формату записи.

Формат JSON: {"версия": 1, "коды": {"Название": "КД"}} — ключи регистронезависимы,
пробелы по краям игнорируются. Код должен состоять только из допустимых символов
алфавита base31 (без неоднозначных 0/O, 1/I/L, U, Y).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Алфавит читаемых сегментов ID (base31): без 0/O, 1/I/L, U, Y.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PRODUCERS_FILE = CONFIG_DIR / "producers.json"
COMPANIES_FILE = CONFIG_DIR / "companies.json"

_CODE_RE = re.compile(rf"[{re.escape(ALPHABET)}]{{2}}")


class CodesError(ValueError):
    """Ошибка справочника кодов (отсутствует запись, некорректный код/файл)."""


def _norm_key(value: str) -> str:
    return " ".join((value or "").strip().upper().split())


def load_codes(path: Path | str) -> dict[str, str]:
    """Читает JSON-справочник и возвращает словарь {НОРМАЛИЗОВАННОЕ_ИМЯ: КОД}."""
    p = Path(path)
    if not p.exists():
        raise CodesError(
            f"Файл справочника не найден: {p}\n"
            f"Создайте его командой: python cli.py codes-init"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CodesError(f"Некорректный JSON в файле {p}: {e}") from e
    raw = data.get("коды") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise CodesError(f"В файле {p} нет секции \"коды\": {{...}}")
    out: dict[str, str] = {}
    for name, code in raw.items():
        key = _norm_key(str(name))
        if not key:
            continue
        code = str(code).strip().upper()
        if not _CODE_RE.fullmatch(code):
            raise CodesError(
                f"{p.name}: код {code!r} для «{name}» некорректен — "
                f"нужно ровно 2 символа из алфавита {ALPHABET} (без 0/O, 1/I/L)"
            )
        out[key] = code
    return out


def save_codes(path: Path | str, mapping: dict[str, str]) -> Path:
    """Сохраняет справочник в JSON-файл (ключи — как переданы)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"версия": 1, "коды": dict(mapping)}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def default_producer_codes() -> dict[str, str]:
    """Пустой стартовый шаблон: никаких реальных брендов не содержит."""
    return {
        "Пример-Производитель": "PP",
    }


def default_company_codes() -> dict[str, str]:
    """Пустой стартовый шаблон: никаких реальных компаний не содержит."""
    return {
        "Пример-Компания": "PK",
    }


def ensure_configs(force: bool = False) -> dict[str, Path]:
    """Создаёт config/producers.json и config/companies.json, если их нет.

    Возвращает путь к каждому созданному/существующему файлу.
    force=True — перезаписать существующие файлы шаблонами.
    """
    result: dict[str, Path] = {}
    for path, defaults in ((PRODUCERS_FILE, default_producer_codes),
                           (COMPANIES_FILE, default_company_codes)):
        if force or not path.exists():
            save_codes(path, defaults())
        result[path.name] = path
    return result


class CodeRegistry:
    """Объединённый доступ к справочникам производителей и компаний.

    Кэширует содержимое JSON-файлов; вызов reload() перечитывает их с диска
    (например, после правки пользователем).
    """

    def __init__(self, producers_path: Path | str = PRODUCERS_FILE,
                 companies_path: Path | str = COMPANIES_FILE):
        self.producers_path = Path(producers_path)
        self.companies_path = Path(companies_path)
        self._producers: dict[str, str] | None = None
        self._companies: dict[str, str] | None = None

    def reload(self) -> None:
        self._producers = None
        self._companies = None

    @property
    def producers(self) -> dict[str, str]:
        if self._producers is None:
            self._producers = load_codes(self.producers_path)
        return self._producers

    @property
    def companies(self) -> dict[str, str]:
        if self._companies is None:
            self._companies = load_codes(self.companies_path)
        return self._companies

    def producer_code(self, name: str) -> str:
        key = _norm_key(name)
        if not key:
            raise CodesError("Не указан производитель")
        code = self.producers.get(key)
        if code is None:
            raise CodesError(
                f"Производитель «{name.strip()}» не найден в справочнике "
                f"{self.producers_path.name}. Добавьте запись вида "
                f"\"{name.strip()}\": \"КД\" (2 символа из алфавита {ALPHABET})."
            )
        return code

    def company_code(self, name: str) -> str:
        key = _norm_key(name)
        if not key:
            raise CodesError("Не указана компания")
        code = self.companies.get(key)
        if code is None:
            raise CodesError(
                f"Компания «{name.strip()}» не найденa в справочнике "
                f"{self.companies_path.name}. Добавьте запись вида "
                f"\"{name.strip()}\": \"КД\" (2 символа из алфавита {ALPHABET})."
            )
        return code


#: Общий экземпляр реестра (используется idgen, API и CLI).
registry = CodeRegistry()
