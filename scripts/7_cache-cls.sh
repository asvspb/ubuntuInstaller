#!/usr/bin/env bash
set -euo pipefail

# Конфигурация (можно переопределить через переменные окружения)
CACHE_DIR="${CACHE_DIR:-$HOME/.cache}"
LOG_PREFIX="[cache-clean]"
HF_MAX_AGE_DAYS="${HF_MAX_AGE_DAYS:-0}"   # 0 = удалить ВСЁ содержимое ~/.cache/huggingface
CHROME_CLEAN="${CHROME_CLEAN:-1}"          # 1=чистить ~/.cache/google-chrome (если не запущен Chrome)
PUPPETEER_CLEAN="${PUPPETEER_CLEAN:-1}"
PLAYWRIGHT_CLEAN="${PLAYWRIGHT_CLEAN:-1}"
PIP_PURGE="${PIP_PURGE:-1}"
THUMBNAILS_CLEAN="${THUMBNAILS_CLEAN:-1}"
MESA_CLEAN="${MESA_CLEAN:-1}"
UV_CLEAN="${UV_CLEAN:-1}"
BRAVE_CLEAN="${BRAVE_CLEAN:-0}"            # по желанию
DRY_RUN="${DRY_RUN:-0}"

log() { printf '%s %s %s\n' "$(date '+%F %T')" "$LOG_PREFIX" "$*"; }
size_of() { du -sh "$1" 2>/dev/null | awk '{print $1}'; }

# Лок-файл, чтобы не было параллельных запусков
LOCKFILE="$CACHE_DIR/.cache-clean-weekly.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  log "Уже запущено — выхожу"
  exit 0
fi

purge_dir_contents() {
  local dir="$1"
  [ -d "$dir" ] || { log "skip (нет каталога): $dir"; return 0; }
  case "$dir" in
    "$HOME/.cache"/*) ;;  # защита от случайного rm вне ~/.cache
    *) log "ОТКАЗ: $dir не под $HOME/.cache"; return 1;;
  esac

  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: удалил бы содержимое $dir"
    return 0
  fi

  # Удаляем всё верхнего уровня (и файлы, и папки)
  find "$dir" -mindepth 1 -maxdepth 1 -print0 | xargs -0 -r rm -rf -- || true
}

proc_running() { pgrep -x "$1" >/dev/null 2>&1; }

clean_dir_if_idle() { # name dir proc_hint
  local name="$1" dir="$2" proc="$3"
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }
  if [ -n "$proc" ] && proc_running "$proc"; then
    log "skip ($name): процесс '$proc' активен"
    return 0
  fi
  log "Очистка $name: $dir"
  purge_dir_contents "$dir" || true
}

clean_huggingface() {
  local dir="$CACHE_DIR/huggingface"
  [ -d "$dir" ] || { log "skip: нет $dir"; return 0; }

  if [ "$HF_MAX_AGE_DAYS" = "0" ]; then
    log "Очистка huggingface ЦЕЛИКОМ"
    purge_dir_contents "$dir" || true
  else
    log "Очистка huggingface: файлы старше $HF_MAX_AGE_DAYS дн."
    if [ "$DRY_RUN" = "1" ]; then
      find "$dir" -type f -mtime +"$HF_MAX_AGE_DAYS" -print | head -n 20 \
        | sed 's/^/DRY-RUN: удалил бы: /' || true
    else
      find "$dir" -type f -mtime +"$HF_MAX_AGE_DAYS" -delete || true
      find "$dir" -type d -empty -delete || true
    fi
  fi
}

clean_pip() {
  [ "$PIP_PURGE" = "1" ] || return 0
  if command -v pip3 >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: pip3 cache purge"
    else
      log "pip3 cache purge"
      pip3 cache purge || true
    fi
  elif command -v pip >/dev/null 2>&1; then
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: pip cache purge"
    else
      log "pip cache purge"
      pip cache purge || true
    fi
  else
    log "skip: pip не найден"
  fi
}

log "Начало. Размер $CACHE_DIR = $(size_of "$CACHE_DIR")"
# Основные чистки
clean_pip
clean_huggingface
[ "$CHROME_CLEAN" = "1" ]     && clean_dir_if_idle "google-chrome" "$CACHE_DIR/google-chrome" "chrome"
[ "$PUPPETEER_CLEAN" = "1" ]  && clean_dir_if_idle "puppeteer"     "$CACHE_DIR/puppeteer"     ""
[ "$PLAYWRIGHT_CLEAN" = "1" ] && clean_dir_if_idle "ms-playwright-go" "$CACHE_DIR/ms-playwright-go" ""
[ "$THUMBNAILS_CLEAN" = "1" ] && purge_dir_contents "$CACHE_DIR/thumbnails"
[ "$MESA_CLEAN" = "1" ]       && purge_dir_contents "$CACHE_DIR/mesa_shader_cache"
[ "$UV_CLEAN" = "1" ]         && purge_dir_contents "$CACHE_DIR/uv"
[ "$BRAVE_CLEAN" = "1" ]      && clean_dir_if_idle "BraveSoftware" "$CACHE_DIR/BraveSoftware" "brave"

# Дополнительно мелкие кэши (если есть)
purge_dir_contents "$CACHE_DIR/node-gyp" || true
purge_dir_contents "$CACHE_DIR/yarn"     || true
purge_dir_contents "$CACHE_DIR/vscode-ripgrep" || true

log "Готово. Размер $CACHE_DIR = $(size_of "$CACHE_DIR")"