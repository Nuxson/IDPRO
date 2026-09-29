#!/bin/bash
# ============================================================================
# Сборка портативного Windows-бинарника IDPRO.exe из-под Linux через Docker.
# PyInstaller физически не умеет собирать "на кроссе" (Linux -> Windows),
# поэтому внутри контейнера крутится Wine + Windows-версии Python/PyInstaller.
#
# Требования: docker с плагином buildx (есть в любом современном Docker Desktop
#             / Docker Engine 20+). Права админа на хосте для самой сборки НЕ
#             нужны, но группа `docker` выдать их может — собирайте на своей машине.
#
# Использование:
#   ./build_windows_docker.sh            # обычная сборка
#   ./build_windows_docker.sh --clean    # пересобрать образ без кэша
#
# Результат: dist-windows/IDPRO/        (portable-папка: IDPRO.exe + всё нужное)
#            dist-windows/IDPRO.zip     (архив для переноса на рабочий ПК)
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

CLEAN_FLAG=""
if [[ "${1:-}" == "--clean" ]]; then CLEAN_FLAG="--no-cache"; fi

# 1. Собираем образ (кэшируется: повторные сборки идут с шага копирования кода)
docker buildx build $CLEAN_FLAG -f Dockerfile.windows -t idpro-win-builder .

# 2. Запускаем сборку внутри контейнера, результат — в ./dist-windows
rm -rf dist-windows
cid=$(docker create idpro-win-builder)
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT
docker cp "$cid:/work/dist-windows/." ./dist-windows/

# 3. Проверяем, что это действительно PE-бинарник Windows, а не ELF
file dist-windows/IDPRO/IDPRO.exe | grep -q 'PE32' \
  || { echo "ОШИБКА: IDPRO.exe не является Windows-EXE"; exit 1; }

# 4. Упаковываем portable-папку в zip (переносится одним файлом)
(cd dist-windows && zip -qr IDPRO.zip IDPRO)

echo
echo "готово:"
echo "  dist-windows/IDPRO/      — portable-папка (запуск: IDPRO.exe)"
echo "  dist-windows/IDPRO.zip   — архив для переноса"
