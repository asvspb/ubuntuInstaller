#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$SCRIPT_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

check_docker() {
    if ! command -v docker &>/dev/null; then
        err "Docker не установлен. Установите: https://docs.docker.com/get-docker/"
        exit 1
    fi

    if ! docker info &>/dev/null; then
        err "Docker демон не запущен. Запустите: sudo systemctl start docker"
        exit 1
    fi

    if ! docker compose version &>/dev/null && ! docker-compose version &>/dev/null; then
        err "Docker Compose не найден."
        exit 1
    fi

    ok "Docker и Docker Compose доступны"
}

get_compose_cmd() {
    if docker compose version &>/dev/null 2>&1; then
        echo "docker compose"
    else
        echo "docker-compose"
    fi
}

ask_token() {
    if [[ -f "$ENV_FILE" ]]; then
        source "$ENV_FILE"
        if [[ -n "${NGROK_AUTHTOKEN:-}" ]]; then
            echo -e "${GREEN}Найден сохранённый токен: ${NGROK_AUTHTOKEN:0:8}...${NC}"
            read -rp "Использовать его? [Y/n]: " use_saved
            if [[ "${use_saved,,}" != "n" ]]; then
                return
            fi
        fi
    fi

    echo -e "${CYAN}Получите бесплатный токен на https://dashboard.ngrok.com/get-started/your-authtoken${NC}"
    while true; do
        read -rp "Введите NGROK_AUTHTOKEN: " token
        if [[ -z "$token" ]]; then
            err "Токен не может быть пустым."
            continue
        fi
        if [[ ! "$token" =~ ^[a-zA-Z0-9_]+$ ]]; then
            err "Токен содержит недопустимые символы."
            continue
        fi
        break
    done

    echo "NGROK_AUTHTOKEN=$token" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok "Токен сохранён в $ENV_FILE"
}

inject_token() {
    ok "Токен готов для использования в Docker Compose"
}

is_running() {
    docker ps --format '{{.Names}}' | grep -q "rutube_tinyproxy" 2>/dev/null
}

start_services() {
    local compose_cmd
    compose_cmd=$(get_compose_cmd)

    info "Запуск контейнеров..."
    $compose_cmd -f "$COMPOSE_FILE" up -d

    info "Ожидание запуска tinyproxy..."
    for i in $(seq 1 15); do
        if docker ps --format '{{.Names}}' | grep -q "rutube_tinyproxy"; then
            break
        fi
        sleep 1
    done

    ok "Tinyproxy запущен на порту 8888"
}

wait_for_ngrok_url() {
    info "Ожидание URL туннеля от ngrok..."

    if ! docker ps --format '{{.Names}}' | grep -q "rutube_ngrok" 2>/dev/null; then
        err "Контейнер rutube_ngrok не запущен. Сначала запустите прокси-туннель (пункт 1)."
        return 1
    fi

    local url=""
    for i in $(seq 1 30); do
        local error_msg
        error_msg=$(docker logs rutube_ngrok 2>&1 | grep -oP 'ERR_NGROK_\d+' | tail -1 || true)
        if [[ -n "$error_msg" ]]; then
            err "ngrok вернул ошибку: $error_msg"
            docker logs rutube_ngrok 2>&1 | grep -i "error\|authentication failed" | tail -5 || true
            echo ""
            echo -e "  ${YELLOW}Возможные причины:${NC}"
            echo "  - Неверный authtoken (обновите через пункт 6)"
            echo "  - Токен получен здесь: https://dashboard.ngrok.com/get-started/your-authtoken"
            return 1
        fi

        url=$(docker logs rutube_ngrok 2>&1 | grep -oP 'url=https://[a-zA-Z0-9\-]+\.ngrok[a-zA-Z0-9\-.]+' | head -1 | sed 's/url=//' || true)
        if [[ -n "$url" ]]; then
            break
        fi
        sleep 2
    done

    if [[ -z "$url" ]]; then
        warn "Не удалось автоматически получить URL за 60 сек. Проверьте логи:"
        echo "  docker logs rutube_ngrok"
        echo ""
        echo "Или откройте панель ngrok: https://dashboard.ngrok.com/tunnels/agents"
        return 1
    fi

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Прокси-туннель активен!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "  Публичный URL: ${CYAN}${url}${NC}"
    echo ""
    echo -e "  Используйте в бэкенде kino-club:"
    echo -e "  ${YELLOW}PROXY_URL=${url}${NC}"
    echo ""
    echo -e "  Маршрут трафика:"
    echo -e "  NL Server -> Ngrok -> Ваш ПК -> Rutube"
    echo -e "${GREEN}========================================${NC}"
}

show_logs() {
    echo ""
    read -rp "Показать логи контейнеров? [y/N]: " show
    if [[ "${show,,}" == "y" ]]; then
        local compose_cmd
        compose_cmd=$(get_compose_cmd)
        $compose_cmd -f "$COMPOSE_FILE" logs --tail=30
    fi
}

stop_services() {
    local compose_cmd
    compose_cmd=$(get_compose_cmd)
    info "Остановка контейнеров..."
    $compose_cmd -f "$COMPOSE_FILE" down
    ok "Контейнеры остановлены"
}

show_status() {
    if is_running; then
        ok "Прокси-туннель запущен:"
        docker ps --filter "name=rutube_" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    else
        warn "Прокси-туннель не запущен."
    fi
}

menu() {
    echo ""
    echo -e "${CYAN}=== Rutube Local Proxy Tunnel ===${NC}"
    echo "1) Запустить прокси-туннель"
    echo "2) Остановить прокси-туннель"
    echo "3) Показать статус"
    echo "4) Показать URL туннеля"
    echo "5) Показать логи"
    echo "6) Заменить токен ngrok"
    echo "0) Выход"
    echo ""
    read -rp "Выберите действие [1]: " choice
    choice="${choice:-1}"

    case "$choice" in
        1)
            check_docker
            ask_token
            inject_token
            if is_running; then
                warn "Контейнеры уже запущены. Перезапуск..."
                stop_services
            fi
            start_services
            wait_for_ngrok_url
            ;;
        2)
            stop_services
            ;;
        3)
            show_status
            ;;
        4)
            wait_for_ngrok_url
            ;;
        5)
            local compose_cmd
            compose_cmd=$(get_compose_cmd)
            $compose_cmd -f "$COMPOSE_FILE" logs --tail=50 -f
            ;;
        6)
            rm -f "$ENV_FILE"
            ask_token
            inject_token
            ok "Токен обновлён. Перезапустите контейнеры (пункт 1)."
            ;;
        0)
            exit 0
            ;;
        *)
            err "Неверный выбор."
            ;;
    esac
}

if [[ "${1:-}" == "--start" ]] || [[ "${1:-}" == "-s" ]]; then
    check_docker
    ask_token
    inject_token
    if is_running; then
        warn "Контейнеры уже запущены. Перезапуск..."
        stop_services
    fi
    start_services
    wait_for_ngrok_url
elif [[ "${1:-}" == "--stop" ]]; then
    stop_services
elif [[ "${1:-}" == "--status" ]]; then
    show_status
elif [[ "${1:-}" == "--url" ]]; then
    wait_for_ngrok_url
elif [[ "${1:-}" == "--logs" ]]; then
    compose_cmd=$(get_compose_cmd)
    $compose_cmd -f "$COMPOSE_FILE" logs --tail=50 -f
elif [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    echo "Использование: $(basename "$0") [команда]"
    echo ""
    echo "Команды:"
    echo "  -s, --start    Запустить прокси-туннель"
    echo "  --stop         Остановить прокси-туннель"
    echo "  --status       Показать статус контейнеров"
    echo "  --url          Показать URL туннеля"
    echo "  --logs         Показать логи"
    echo "  -h, --help     Показать справку"
    echo ""
    echo "Без аргументов: интерактивное меню"
else
    while true; do
        menu
    done
fi
