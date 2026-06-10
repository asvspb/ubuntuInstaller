import logging
import os
import secrets
import string
from pathlib import Path

from src.core.shell import run, command_exists, systemctl
from src.core.logger import console

logger = logging.getLogger(__name__)

TURN_CONF = "/etc/turnserver.conf"
TURN_DEFAULT = "/etc/default/coturn"


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_external_ip() -> str:
    for url in [
        "curl -s4 --max-time 5 https://ifconfig.me",
        "curl -s4 --max-time 5 https://api.ipify.org",
        "curl -s4 --max-time 5 https://ipecho.net/plain",
    ]:
        result = run(url)
        if result.ok and result.output:
            return result.output
    return ""


def is_installed() -> bool:
    return os.path.exists(TURN_CONF) and command_exists("turnserver")


def get_credentials() -> tuple[str, str]:
    if not os.path.exists(TURN_CONF):
        return "", ""
    for line in Path(TURN_CONF).read_text().splitlines():
        line = line.strip()
        if line.startswith("user="):
            parts = line.split("=", 1)[1].split(":", 1)
            if len(parts) == 2:
                return parts[0], parts[1]
    return "", ""


def get_external_ip_from_conf() -> str:
    if not os.path.exists(TURN_CONF):
        return ""
    for line in Path(TURN_CONF).read_text().splitlines():
        line = line.strip()
        if line.startswith("external-ip="):
            return line.split("=", 1)[1].strip()
    return ""


def run_install(
    username: str = "admin",
    password: str = "",
    realm: str = "private-cinema.local",
    port: int = 3478,
    min_port: int = 49152,
    max_port: int = 65535,
) -> int:
    if is_installed():
        console.print("[warning]Coturn уже установлен[/warning]")
        console.print("Используйте 'vds coturn status' для просмотра конфигурации")
        console.print("Используйте 'vds coturn remove' для удаления")
        return 0

    console.print("\n[info]═══ Coturn TURN Server Installer ═══[/info]\n")

    if not password:
        password = generate_password()
        console.print(f"[info]Сгенерирован пароль: {password}[/info]")

    external_ip = get_external_ip()
    if not external_ip:
        console.print("[error]Не удалось определить внешний IP-адрес[/error]")
        return 1

    console.print(f"[info]Внешний IP: {external_ip}[/info]")

    console.print("[info]Настройка фаервола...[/info]")
    if command_exists("ufw"):
        run(f"ufw allow {port}/tcp")
        run(f"ufw allow {port}/udp")
        run(f"ufw allow {min_port}:{max_port}/udp")

    run(f"iptables -I INPUT -p tcp --dport {port} -j ACCEPT")
    run(f"iptables -I INPUT -p udp --dport {port} -j ACCEPT")
    run(f"iptables -I INPUT -p udp --dport {min_port}:{max_port} -j ACCEPT")

    console.print("[info]Установка пакета coturn...[/info]")
    run("apt-get update -qq")
    run("apt-get install -y -qq coturn")

    if os.path.exists(TURN_DEFAULT):
        conf_text = Path(TURN_DEFAULT).read_text()
        conf_text = conf_text.replace("#TURNSERVER_ENABLED=1", "TURNSERVER_ENABLED=1")
        if "TURNSERVER_ENABLED" not in conf_text:
            conf_text += "\nTURNSERVER_ENABLED=1\n"
        Path(TURN_DEFAULT).write_text(conf_text)

    if os.path.exists(TURN_CONF):
        Path(TURN_CONF).rename(TURN_CONF + ".backup")

    console.print("[info]Создание конфигурации...[/info]")
    turn_conf = f"""listening-ip=0.0.0.0
listening-port={port}
external-ip={external_ip}
user={username}:{password}
realm={external_ip}
min-port={min_port}
max-port={max_port}
fingerprint
lt-cred-mech
no-tls
no-dtls
no-cli
no-loopback-peers
no-multicast-peers
"""
    Path(TURN_CONF).write_text(turn_conf)

    console.print("[info]Запуск службы coturn...[/info]")
    systemctl("enable", "coturn")
    systemctl("restart", "coturn")

    if systemctl("is-active", "coturn").ok:
        console.print("\n[success]═══ Coturn запущен ═══[/success]")
        console.print("Скопируйте эти строки в ваш файл .env:\n")
        console.print(f"  VITE_TURN_SERVER_URL_1=turn:{external_ip}:{port}")
        console.print(f"  VITE_TURN_SERVER_URL_2=turn:{external_ip}:{port}?transport=tcp")
        console.print(f"  VITE_TURN_USERNAME={username}")
        console.print(f"  VITE_TURN_PASSWORD={password}")
    else:
        console.print("[error]Coturn не запустился. Проверьте: journalctl -u coturn[/error]")
        return 1

    return 0


def run_status() -> int:
    if not is_installed():
        console.print("[warning]Coturn не установлен[/warning]")
        return 0

    active = systemctl("is-active", "coturn").ok
    external_ip = get_external_ip_from_conf()
    user, _ = get_credentials()

    console.print("\n[info]═══ Coturn Status ═══[/info]\n")
    console.print(f"  Служба: {'[success]active[/success]' if active else '[error]inactive[/error]'}")
    console.print(f"  Внешний IP: {external_ip}")
    console.print(f"  Пользователь: {user}")
    console.print(f"  Конфиг: {TURN_CONF}")

    if active and external_ip:
        console.print(f"\n  TURN URL: turn:{external_ip}:3478")
        console.print(f"  TURN TCP: turn:{external_ip}:3478?transport=tcp")

    return 0


def run_remove() -> int:
    if not is_installed():
        console.print("[warning]Coturn не установлен[/warning]")
        return 0

    console.print("[info]Остановка и удаление coturn...[/info]")
    systemctl("stop", "coturn")
    systemctl("disable", "coturn")

    if os.path.exists(TURN_CONF):
        Path(TURN_CONF).unlink()
    if os.path.exists(TURN_CONF + ".backup"):
        Path(TURN_CONF + ".backup").unlink()

    run("apt-get remove -y coturn 2>/dev/null")

    console.print("[success]Coturn удалён[/success]")
    return 0
