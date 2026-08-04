#!/usr/bin/env bash
set -euo pipefail

# telegram-clean.sh — обёртка-лаунчер Telegram Desktop с автоочисткой.
#
# Запускает Telegram, а после его полного закрытия:
#   1. очищает кэш Telegram (медиа, превью, webview ботов/прочих);
#   2. опустошает пользовательскую корзину (~/.local/share/Trash);
#   3. выполняет fstrim по / — чтобы удалённые данные были физически
#      стёрты на SSD и не подлежали восстановлению forensic-утилитами.
#
# База аккаунта (tdata/<account>) и эмодзи НЕ трогаются —
# сессия и история сообщений сохраняются.
#
# Размещение в репозитории: $USER/.local/bin/telegram-clean.sh
# Развёртывание:            симлинк ~/.local/bin/telegram-clean.sh -> репо
#                           (каталог ~/.local/bin уже в $PATH через .bashrc)
# Ярлык:                    $USER/.local/share/applications/telegram-desktop_telegram-desktop.desktop
# Лог:                      ~/.local/share/telegram-clean.log  (*.log в .gitignore)
#
# Конфигурация — через переменные окружения (см. ниже).

# --- Конфигурация (переопределяется через env) ---
TELEGRAM_BIN="${TELEGRAM_BIN:-/snap/bin/telegram-desktop}"
TELEGRAM_TDATA="${TELEGRAM_TDATA:-$HOME/snap/telegram-desktop/current/.local/share/TelegramDesktop/tdata/user_data}"
TRASH_DIR="${TRASH_DIR:-$HOME/.local/share/Trash}"
LOG_FILE="${LOG_FILE:-$HOME/.local/share/telegram-clean.log}"

CLEAN_TG_CACHE="${CLEAN_TG_CACHE:-1}"   # 1=чистить кэш Telegram
EMPTY_TRASH="${EMPTY_TRASH:-1}"         # 1=опустошать пользовательскую корзину
DO_FSTRIM="${DO_FSTRIM:-1}"             # 1=выполнять sudo -n fstrim /
DRY_RUN="${DRY_RUN:-0}"                 # 1=только отчёт в лог, без изменений

# Подкаталоги кэша Telegram (относительно $TELEGRAM_TDATA), которые чистим.
# Базу аккаунта (D877F783D5D3EF8C) и emoji/ НЕ трогаем.
TG_CACHE_SUBS=(cache media_cache wvother wvbots/cache)

log() { echo "[$(date '+%F %T')] $*" >> "$LOG_FILE"; }

# --- 1. Запуск Telegram -----------------------------------------------
#   Если экземпляр не запущен — блокирует скрипт до выхода Telegram.
#   Если уже запущен — snap single-instance активирует окно и сразу
#   вернёт управление; тогда ждём закрытия в шаге 2.
log "=== обёртка запущена (pid $$) ==="
if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN: запуск telegram-desktop пропущен"
else
  "$TELEGRAM_BIN" "$@" || true
fi

# --- 2. Ожидание полного закрытия Telegram ----------------------------
while pgrep -x telegram-desktop >/dev/null 2>&1; do
  sleep 5
done
log "Telegram закрыт — начинается очистка."

# --- 3. Очистка кэша Telegram -----------------------------------------
if [ "$CLEAN_TG_CACHE" = "1" ]; then
  for sub in "${TG_CACHE_SUBS[@]}"; do
    target="$TELEGRAM_TDATA/$sub"
    if [ "$DRY_RUN" = "1" ]; then
      log "DRY-RUN: rm -rf $target"
    else
      rm -rf "$target" 2>/dev/null || true
      mkdir -p "$target" 2>/dev/null || true
    fi
  done
  log "Кэш Telegram очищён."
fi

# --- 4. Опустошение пользовательской корзины --------------------------
if [ "$EMPTY_TRASH" = "1" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: rm -rf $TRASH_DIR/{files,info}"
  else
    rm -rf "$TRASH_DIR/files" "$TRASH_DIR/info" 2>/dev/null || true
    mkdir -p "$TRASH_DIR/files" "$TRASH_DIR/info" || true
    chmod 700 "$TRASH_DIR/files" "$TRASH_DIR/info" 2>/dev/null || true
  fi
  log "Корзина очищена."
fi

# --- 5. TRIM свободных блоков на / ------------------------------------
#   Физическое стирание на SSD: удалённые данные становятся невосстановимыми.
if [ "$DO_FSTRIM" = "1" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: sudo -n fstrim -v /"
  else
    # Захватываем вывод в переменную, затем пишем в лог от имени пользователя
    # (редирект делает оболочка пользователя, а не sudo — см. shellcheck SC2024).
    if fstrim_out=$(sudo -n fstrim -v / 2>&1); then
      printf '%s\n' "$fstrim_out" >> "$LOG_FILE"
      log "fstrim выполнен."
    else
      printf '%s\n' "$fstrim_out" >> "$LOG_FILE" 2>/dev/null || true
      log "ВНИМАНИЕ: fstrim не выполнен (sudo без пароля недоступен?)."
    fi
  fi
fi

log "=== обёртка завершена ==="
