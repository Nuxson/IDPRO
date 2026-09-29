#!/bin/bash
# ============================================================================
# Сборка портативного Windows-бинарника IDPRO.exe из-под Linux через Docker.
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")"

CLEAN_FLAG=""
if [[ "${1:-}" == "--clean" ]]; then
  CLEAN_FLAG="--no-cache"
fi

# 1. Включаем BuildKit, чтобы убрать предупреждение "DEPRECATED: The legacy builder..."
export DOCKER_BUILDKIT=1

detect_engine() {
  if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    echo "podman build"
  elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    # Предпочитаем buildx, если он доступен, иначе обычный build (с BuildKit)
    if docker buildx version >/dev/null 2>&1; then
      echo "docker buildx build"
    else
      echo "docker build"
    fi
  else
    echo "ERROR"
  fi
}

ENGINE=$(detect_engine)
if [[ "$ENGINE" == "ERROR" ]]; then
  echo "ОШИБКА: не найден запущенный контейнерный движок (docker/podman)." >&2
  echo "Проверьте, запущен ли docker: sudo systemctl start docker" >&2
  exit 1
fi

echo "движок сборки: $ENGINE"

# 2. Проверяем наличие zip перед началом долгой сборки
if ! command -v zip >/dev/null 2>&1; then
  echo "ОШИБКА: утилита 'zip' не найдена в системе." >&2
  echo "Установите её перед запуском: sudo pacman -S zip (для Manjaro/Arch) или sudo apt install zip" >&2
  exit 1
fi

BUILD_CMD=($ENGINE)
if [[ "${ENGINE%% *}" == "docker" ]]; then
  CLI=(docker)
else
  CLI=(podman)
fi

# 3. Собираем образ. Передаём Dockerfile через stdin.
"${BUILD_CMD[@]}" $CLEAN_FLAG -f - -t idpro-win-builder . < Dockerfile.windows

# 4. Запускаем сборку внутри контейнера, результат — в ./dist-windows
rm -rf dist-windows
cid=$("${CLI[@]}" create idpro-win-builder)
trap '"${CLI[@]}" rm -f "$cid" >/dev/null 2>&1 || true' EXIT
"${CLI[@]}" cp "$cid:/work/dist-windows/." ./dist-windows/

# 5. Проверяем, что это действительно PE-бинарник Windows, а не ELF
if ! file dist-windows/IDPRO/IDPRO.exe | grep -q 'PE32'; then
  echo "ОШИБКА: IDPRO.exe не является Windows-EXE (PE32)" >&2
  exit 1
fi

# 6. Упаковываем portable-папку в zip
(cd dist-windows && zip -qr IDPRO.zip IDPRO)

echo
echo "✅ ГОТОВО:"
echo "  dist-windows/IDPRO/      — portable-папка (запуск: run-portable.bat или IDPRO.exe)"
echo "  dist-windows/IDPRO.zip   — архив для переноса на другой ПК"
