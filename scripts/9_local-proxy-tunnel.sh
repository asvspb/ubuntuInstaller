#!/bin/bash
set -e

# Описание: Скрипт для развертывания Local Proxy Tunnel (обход VPN для ру-сайтов)
# Использование: Запускать на локальной машине с правами пользователя (должен быть установлен Docker)

echo ">>> Развертывание Local Proxy Tunnel..."

TUNNEL_DIR="$(dirname "$0")/local-proxy-tunnel"

if [ ! -d "$TUNNEL_DIR" ]; then
    echo "Ошибка: Директория $TUNNEL_DIR не найдена."
    exit 1
fi

cd "$TUNNEL_DIR"

# Автоматическое определение физической сети
echo ">>> Определение сетевых настроек (поиск физического шлюза)..."
DEFAULT_ROUTE=$(ip -4 route | grep '^default' | grep -vE 'tun|wg|zt|ppp' | head -n 1)
GATEWAY=$(echo "$DEFAULT_ROUTE" | awk '{print $3}')
INTERFACE=$(echo "$DEFAULT_ROUTE" | awk '{print $5}')

if [ -z "$GATEWAY" ] || [ -z "$INTERFACE" ]; then
    echo "Ошибка: Не удалось определить физический шлюз или интерфейс."
    exit 1
fi

SUBNET=$(ip -4 route | grep "dev $INTERFACE proto kernel" | awk '{print $1}' | head -n 1)
PREFIX=$(echo "$GATEWAY" | cut -d. -f1-3)
PROXY_IP="${PREFIX}.199"
SOCKS5_IP="${PREFIX}.198"

echo "Определен интерфейс: $INTERFACE"
echo "Определен шлюз: $GATEWAY"
echo "Определена подсеть: $SUBNET"
echo "IP прокси (Nginx): $PROXY_IP"
echo "IP прокси (SOCKS5): $SOCKS5_IP"

# Сохраняем настройки в .env
cat > .env <<EOF
SUBNET=$SUBNET
GATEWAY=$GATEWAY
PARENT_INTERFACE=$INTERFACE
PROXY_IP=$PROXY_IP
SOCKS5_IP=$SOCKS5_IP
EOF

# Пересобираем и запускаем контейнеры
echo ">>> Запускаем docker-compose..."
docker compose up -d --build

echo ">>> Local Proxy Tunnel успешно запущен!"
echo "SOCKS5-прокси доступен на: 127.0.0.1:1080"
echo "Reverse-proxy доступен на: 127.0.0.1:8888"
echo "Обязательно прочитайте документацию: doc/local-proxy-tunnel.md"
