"""Справочники кодов производителей и компаний (JSON-конфиги).

Программа НЕ содержит жёстко зашитых названий брендов/компаний. Все читаемые
коды берутся из внешних конфигурационных файлов:

    config/producers.json   — производитель -> код (2 символа), напр. "РОМАШКА": "RM"
    config/companies.json   — компания -> сокращение (2 символа), напр. "Вектор-Телеком": "VT"
    config/ports.json       — порт -> буква ID (1 символ), напр. "TN_A": "A"

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

def _config_dir() -> Path:
    """Каталог справочников. Приоритет: IDGEN_CONFIG_DIR > рядом с exe/проектом,
    чтобы пользователь мог редактировать JSON-файлы после сборки."""
    env_dir = __import__("os").environ.get("IDGEN_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    if getattr(__import__("sys"), "frozen", False):  # PyInstaller
        return Path(__import__("sys").executable).resolve().parent / "config"
    return Path(__file__).resolve().parent.parent / "config"


CONFIG_DIR = _config_dir()
PRODUCERS_FILE = CONFIG_DIR / "producers.json"
COMPANIES_FILE = CONFIG_DIR / "companies.json"
PORTS_FILE = CONFIG_DIR / "ports.json"

_CODE_RE = re.compile(rf"[{re.escape(ALPHABET)}]{{2}}")
_PORT_CODE_RE = re.compile(rf"[{re.escape(ALPHABET)}]")


class CodesError(ValueError):
    """Ошибка справочника кодов (отсутствует запись, некорректный код/файл)."""


def _norm_key(value: str) -> str:
    return " ".join((value or "").strip().upper().split())


def load_codes(path: Path | str, code_len: int = 2) -> dict[str, str]:
    """Читает JSON-справочник и возвращает словарь {НОРМАЛИЗОВАННОЕ_ИМЯ: КОД}.

    code_len=2 — производители/компании; code_len=1 — справочник портов.
    Для портов ключ нормализуется тем же правилом, что и ввод пользователя в
    idgen.port_code (разделители _, -, : и пробелы не значимы), поэтому запись
    "TN-A"/"tn a" читается как "TN_A".
    """
    port_mode = code_len == 1

    def key_of(value: str) -> str:
        if not port_mode:
            return _norm_key(value)
        return re.sub(r"[^A-Z0-9]+", "_", _norm_key(value)).strip("_")

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
    problems: list[str] = []
    for name, code in raw.items():
        key = key_of(str(name))
        if not key:
            continue
        code = str(code).strip().upper()
        ok = (_PORT_CODE_RE.fullmatch(code) if code_len == 1
              else _CODE_RE.fullmatch(code))
        if not ok:
            problems.append(f"код {code!r} для «{name}»")
            continue
        out[key] = code
    if problems:
        raise CodesError(
            f"{p.name}: некорректные записи — {', '.join(problems)}. "
            f"Код должен состоять ровно из {code_len} симв. алфавита {ALPHABET} "
            f"(без 0/O, 1/I/L). "
            f"Исправьте файл или удалите его и выполните: python cli.py codes-init --force"
        )
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


def default_port_codes() -> dict[str, str]:
    """Стартовый шаблон портов: нейтральные технические обозначения TN_A/B/C."""
    return {"TN_A": "A", "TN_B": "B", "TN_C": "C"}


def ensure_configs(force: bool = False) -> dict[str, Path]:
    """Создаёт config/producers.json, companies.json и ports.json, если их нет.

    Возвращает путь к каждому созданному/существующему файлу.
    force=True — перезаписать существующие файлы шаблонами.
    """
    result: dict[str, Path] = {}
    for path, defaults in ((PRODUCERS_FILE, default_producer_codes),
                           (COMPANIES_FILE, default_company_codes),
                           (PORTS_FILE, default_port_codes)):
        if force or not path.exists():
            save_codes(path, defaults())
        result[path.name] = path
    return result


class CodeRegistry:
    """Объединённый доступ к справочникам производителей и компаний.

    Кэширует содержимое JSON-файлов; кэш автоматически сбрасывается, когда
    файл справочника меняется на диске (правка вручную или через API), —
    перезапуск приложения не требуется. Вызов reload() форсирует перечитывание.
    """

    def __init__(self, producers_path: Path | str = PRODUCERS_FILE,
                 companies_path: Path | str = COMPANIES_FILE,
                 ports_path: Path | str = PORTS_FILE):
        self.producers_path = Path(producers_path)
        self.companies_path = Path(companies_path)
        self.ports_path = Path(ports_path)
        self._producers: dict[str, str] | None = None
        self._companies: dict[str, str] | None = None
        self._ports: dict[str, str] | None = None
        self._stamps: dict[Path, tuple] = {}

    @staticmethod
    def _stamp(path: Path) -> tuple:
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return ("missing",)

    def _fresh(self, path: Path) -> bool:
        """True, если файл с момента последнего чтения не менялся."""
        return self._stamps.get(path) == self._stamp(path)

    def reload(self) -> None:
        self._producers = None
        self._companies = None
        self._ports = None
        self._stamps.clear()

    @property
    def producers(self) -> dict[str, str]:
        if self._producers is None or not self._fresh(self.producers_path):
            self._producers = load_codes(self.producers_path)
            self._stamps[self.producers_path] = self._stamp(self.producers_path)
        return self._producers

    @property
    def companies(self) -> dict[str, str]:
        if self._companies is None or not self._fresh(self.companies_path):
            self._companies = load_codes(self.companies_path)
            self._stamps[self.companies_path] = self._stamp(self.companies_path)
        return self._companies

    @property
    def ports(self) -> dict[str, str]:
        if self._ports is None or not self._fresh(self.ports_path):
            self._ports = load_codes(self.ports_path, code_len=1)
            self._stamps[self.ports_path] = self._stamp(self.ports_path)
        return self._ports

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

    def port_code(self, name: str) -> str:
        key = _norm_key(name)
        if not key:
            raise CodesError("Не указан порт")
        code = self.ports.get(key)
        if code is None:
            raise CodesError(
                f"Порт «{name.strip()}» не найден в справочнике "
                f"{self.ports_path.name}. Добавьте запись вида "
                f"\"{name.strip()}\": \"Д\" (1 символ из алфавита {ALPHABET})."
            )
        return code

    def add_code(self, name: str, code: str, *, ports: bool = False) -> None:
        """Добавляет/обновляет запись в справочнике компаний (ports=True — в ports.json)."""
        path = self.ports_path if ports else self.companies_path
        codes = load_codes(path, 1 if ports else 2)
        codes[_norm_key(name)] = code
        save_codes(path, codes)
        self.reload()

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
