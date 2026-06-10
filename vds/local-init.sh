#!/usr/bin/env bash
# =============================================================================
#  VDS Orchestrator — Локальная инициализация нового сервера
#  Запускается на ВАШЕМ компьютере (не на сервере!)
# =============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
BOLD='\033[1m'

echo -e "${BOLD}${CYAN}=== Инициализация нового сервера ===${NC}"
echo ""

# 1. Запрос данных
read -p "Введите IP-адрес нового сервера: " SERVER_IP </dev/tty
if [[ -z "$SERVER_IP" ]]; then
    echo -e "${RED}Ошибка: IP-адрес не может быть пустым.${NC}"
    exit 1
fi

read -p "Введите имя пользователя (по умолчанию root): " SERVER_USER </dev/tty
SERVER_USER=${SERVER_USER:-root}

echo ""

# 2. Проверка и генерация локального SSH-ключа
if [[ ! -f ~/.ssh/id_rsa && ! -f ~/.ssh/id_ed25519 ]]; then
    echo -e "${YELLOW}SSH-ключ не найден. Генерируем новый (ed25519)...${NC}"
    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
else
    echo -e "${GREEN}[+]${NC} Локальный SSH-ключ найден."
fi

# 3. Копирование ключа (здесь система сама запросит пароль)
echo -e "${CYAN}[->]${NC} Сейчас потребуется ввести ${BOLD}ПАРОЛЬ${NC} от сервера для копирования ключа."
ssh-copy-id -o StrictHostKeyChecking=accept-new "${SERVER_USER}@${SERVER_IP}"

if [[ $? -ne 0 ]]; then
    echo -e "${RED}[x] Ошибка: не удалось скопировать SSH-ключ. Проверьте пароль.${NC}"
    exit 1
fi
echo -e "${GREEN}[+]${NC} SSH-ключ успешно скопирован!"
echo ""

# 4. Удаленный запуск скриптов установки
echo -e "${CYAN}[->]${NC} Подключаемся по SSH (без пароля) и запускаем установку..."
ssh -o StrictHostKeyChecking=accept-new "${SERVER_USER}@${SERVER_IP}" << 'EOF'
    set -e
    if ! command -v curl >/dev/null 2>&1; then
        echo -e "\n\033[0;36m--- Установка базовых утилит (curl) ---\033[0m"
        apt-get update -qq && apt-get install -y -qq curl
    fi

    echo -e "\n\033[0;36m--- Запуск preinstall.sh ---\033[0m"
    curl -fsSL "https://raw.githubusercontent.com/asvspb/my-first-vds/main/preinstall.sh?v=$(date +%s)" | bash
    
    echo -e "\n\033[0;36m--- Запуск install.sh ---\033[0m"
    curl -fsSL "https://raw.githubusercontent.com/asvspb/my-first-vds/main/install.sh?v=$(date +%s)" | bash
EOF

echo ""
echo -e "${BOLD}${GREEN}✅ Сервер успешно инициализирован!${NC}"
echo -e "${CYAN}Подключаемся к серверу...${NC}"
sleep 1

# 5. Вход на сервер и запуск меню
ssh -t "${SERVER_USER}@${SERVER_IP}" "vds"
