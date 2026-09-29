"""Точка входа для автономной сборки (PyInstaller) и простого запуска.

Запуск без Python на борту:
    python -m PyInstaller build.spec        # → dist/IDPRO.exe (или ./IDPRO в Linux)
    IDPRO.exe                               # открывает браузер на http://127.0.0.1:8000

Обычный запуск из исходников (эквивалент uvicorn):
    python run_app.py [--host 0.0.0.0] [--port 9000]
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

# PyInstaller не видит динамические импорты uvicorn — регистрируем их явно.
os.environ.setdefault("UVICORN_LOGGING_CONFIG", "")
try:  # работает и в исходниках, и в собранном exe
    import uvicorn.importer  # noqa: F401
    import uvicorn.logging  # noqa: F401
    import uvicorn.loops.asyncio  # noqa: F401
    import uvicorn.loops.auto  # noqa: F401
    import uvicorn.protocols.http.auto  # noqa: F401
    import uvicorn.protocols.websockets.auto  # noqa: F401
    import uvicorn.lifespan.on  # noqa: F401
except ImportError:  # pragma: no cover
    pass


def _free_port(preferred: int) -> int:
    """Возвращает preferred, если порт свободен, иначе первый свободный рядом."""
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


def _data_home() -> None:
    """Все пользовательские данные (ids.db, config/) — в одном месте:
    Windows:  %LOCALAPPDATA%\\IDPRO
    Linux:    ~/.local/share/IDPRO
    macOS:    ~/Library/Application Support/IDPRO
    Так exe-шник и папка сборки остаются чистыми, база не теряется при обновлении.
    Переменные окружения IDGEN_DB / IDGEN_CONFIG_DIR имеют приоритет."""
    import sys
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "IDPRO"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "IDPRO"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME")
                    or Path.home() / ".local" / "share") / "IDPRO"
    base.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("IDGEN_DB", str(base / "ids.db"))
    os.environ.setdefault("IDGEN_CONFIG_DIR", str(base / "config"))


def main() -> None:
    parser = argparse.ArgumentParser(description="IDPRO — генератор уникальных ID")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true",
                        help="не открывать браузер автоматически")
    args = parser.parse_args()

    _data_home()  # до импорта app.* — пути подхватятся один раз при импорте

    host = args.host if args.host != "0.0.0.0" else "127.0.0.1"
    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}/"

    # Справочники создаются при первом старте (если config/*.json отсутствуют).
    from app.codes import ensure_configs
    ensure_configs()

    print(f"IDPRO запущен: {url}")
    print("Справка по API: " + url + "docs")
    print("Данные (база и справочники): " + str(Path(os.environ["IDGEN_DB"]).parent))
    print("Остановка: Ctrl+C")

    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(1.2), webbrowser.open(url)),
            daemon=True,
        ).start()

    import uvicorn
    from app.main import app

    uvicorn.run(app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    # В собранном exe multiprocessing-пулы uvicorn требуют явной защиты.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
