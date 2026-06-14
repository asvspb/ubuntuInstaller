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

# Пересобираем и запускаем контейнеры
echo ">>> Запускаем docker-compose..."
docker compose up -d --build

echo ">>> Local Proxy Tunnel успешно запущен!"
echo "SOCKS5-прокси доступен на: 127.0.0.1:1080"
echo "Reverse-proxy доступен на: 127.0.0.1:8888"
echo "Обязательно прочитайте документацию: doc/local-proxy-tunnel.md"
