#!/bin/bash
# ============================================================================
# Сборка портативного Windows-бинарника IDPRO.exe из-под Linux через Docker.
# PyInstaller физически не умеет собирать "на кроссе" (Linux -> Windows),
# поэтому внутри контейнера крутится Wine + Windows-версии Python/PyInstaller.
#
# Требования: запущенный демон docker (или podman). На Manjaro после установки:
#   sudo systemctl enable --now docker.socket docker.service
#   sudo usermod -aG docker $USER   # затем перезайти в систему
# Если вместо docker используете podman — скрипт определит его сам.
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

# 0. Определяем движок сборки: podman -> docker c buildx -> plain docker build.
#    На Manjaro часто стоит podman (alias docker=podman), у которого нет плагина
#    buildx и который по-своему трактует некоторые флаги — поэтому подбираем
#    рабочую команду автоматически.
detect_engine() {
  if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    echo "podman build"
  elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
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
  echo "Docker не отвечает на unix:///var/run/docker.sock — скорее всего," >&2
  echo "демон docker не запущен. На Manjaro/Arch:" >&2
  echo "  sudo systemctl enable --now docker.socket docker.service" >&2
  echo "  sudo usermod -aG docker \$USER   # затем выйти и зайти снова" >&2
  echo "Проверка: docker info" >&2
  echo "" >&2
  echo "Альтернатива без демона — rootless podman:" >&2
  echo "  sudo pacman -S podman && podman system migrate" >&2
  exit 1
fi
echo "движок сборки: $ENGINE"
BUILD_CMD=($ENGINE)
if [[ "${ENGINE%% *}" == "docker" ]]; then CLI=(docker); else CLI=(podman); fi

# 1. Собираем образ (кэшируется: повторные сборки идут с шага копирования кода).
#    Dockerfile передаём через stdin (-f -): так команду принимают и docker,
#    и podman, и старые версии без buildx. Контекст — текущая директория.
"${BUILD_CMD[@]}" $CLEAN_FLAG -f - -t idpro-win-builder . < Dockerfile.windows

# 2. Запускаем сборку внутри контейнера, результат — в ./dist-windows
rm -rf dist-windows
cid=$("${CLI[@]}" create idpro-win-builder)
trap '"${CLI[@]}" rm -f "$cid" >/dev/null 2>&1 || true' EXIT
"${CLI[@]}" cp "$cid:/work/dist-windows/." ./dist-windows/

# 3. Проверяем, что это действительно PE-бинарник Windows, а не ELF
file dist-windows/IDPRO/IDPRO.exe | grep -q 'PE32' \
  || { echo "ОШИБКА: IDPRO.exe не является Windows-EXE"; exit 1; }

# 4. Упаковываем portable-папку в zip (переносится одним файлом)
(cd dist-windows && zip -qr IDPRO.zip IDPRO)

echo
echo "готово:"
echo "  dist-windows/IDPRO/      — portable-папка (запуск: IDPRO.exe)"
echo "  dist-windows/IDPRO.zip   — архив для переноса"
