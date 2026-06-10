import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from src.core.shell import run, docker_exec, command_exists, systemctl
from src.core.lock import FileLock
from src.core.logger import console

logger = logging.getLogger(__name__)

INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/ztnet")
LOCK_FILE = "/var/run/ztnet-install.lock"
CONTAINER = "ztnet_zerotier"


def detect_network_architecture() -> dict:
    arch = {
        "is_openvz": False,
        "main_iface": "",
        "main_ip": "",
        "gateway": "",
        "public_ip": "",
        "dns_servers": "",
    }

    virt = run("systemd-detect-virt 2>/dev/null").output.lower()
    if "openvz" in virt or "lxc" in virt:
        arch["is_openvz"] = True
        console.print(f"[warning]Обнаружена виртуализация: {virt}[/warning]")

    iface_result = run("ip -4 route show default | grep -oP 'dev \\K\\S+' | head -1")
    arch["main_iface"] = iface_result.output if iface_result.ok else ""

    ip_result = run(f"ip -4 addr show {arch['main_iface']} | grep -oP 'inet \\K[\\d.]+' | head -1")
    arch["main_ip"] = ip_result.output if ip_result.ok else ""

    gw_result = run("ip -4 route show default | grep -oP 'via \\K\\S+' | head -1")
    arch["gateway"] = gw_result.output if gw_result.ok else ""

    pub_result = run("curl -s4 --max-time 5 https://ifconfig.me 2>/dev/null || curl -s --max-time 5 https://api.ipify.org 2>/dev/null")
    arch["public_ip"] = pub_result.output if pub_result.ok else arch["main_ip"]

    dns_result = run("grep -oP 'nameserver \\K[\\d.]+' /etc/resolv.conf 2>/dev/null | sort -u | tr '\\n' ' '")
    arch["dns_servers"] = dns_result.output if dns_result.ok else ""

    return arch


def generate_docker_compose(
    install_dir: str,
    postgres_password: str,
    nextauth_secret: str,
    server_ip: str,
    ztnet_port: int = 3000,
    docker_bridge_subnet: str = "172.31.255.0/29",
    docker_bridge_gw: str = "172.31.255.1"
) -> str:

    if server_ip and ":" in server_ip:
        server_ip_url = f"[{server_ip}]"
    else:
        server_ip_url = server_ip
    nextauth_url = f"http://{server_ip_url}:{ztnet_port}"

    compose = f"""services:
  postgres:
    image: postgres:15.2-alpine
    container_name: ztnet_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: {postgres_password}
      POSTGRES_DB: ztnet
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - app-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d ztnet"]
      interval: 5s
      timeout: 5s
      retries: 12

  zerotier:
    image: zyclonite/zerotier:1.14.2
    hostname: zerotier
    container_name: ztnet_zerotier
    restart: unless-stopped
    volumes:
      - zerotier:/var/lib/zerotier-one
      - ./local.conf:/var/lib/zerotier-one/local.conf
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
      - NET_RAW
    devices:
      - /dev/net/tun:/dev/net/tun
    network_mode: host
    environment:
      - ZT_OVERRIDE_LOCAL_CONF=false
    healthcheck:
      test: ["CMD", "zerotier-cli", "info"]
      interval: 10s
      timeout: 5s
      retries: 12

  ztnet:
    image: sinamics/ztnet:latest
    container_name: ztnet
    working_dir: /app
    volumes:
      - zerotier:/var/lib/zerotier-one
    restart: unless-stopped
    ports:
      - "{ztnet_port}:3000"
    environment:
      POSTGRES_HOST: postgres
      POSTGRES_PORT: 5432
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: {postgres_password}
      POSTGRES_DB: ztnet
      NEXTAUTH_URL: "{nextauth_url}"
      NEXTAUTH_SECRET: "{nextauth_secret}"
      NEXTAUTH_URL_INTERNAL: "http://ztnet:3000"
    extra_hosts:
      - "zerotier:{docker_bridge_gw}"
    networks:
      - app-network
    links:
      - postgres
    depends_on:
      postgres:
        condition: service_healthy
      zerotier:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -s -o /dev/null -w \\"%{{http_code}}\\" http://ztnet:3000 | grep -qE \\"^(200|30[128])$\\" || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 12

volumes:
  zerotier:
  postgres-data:

networks:
  app-network:
    driver: bridge
    ipam:
      driver: default
      config:
        - subnet: {docker_bridge_subnet}
"""
    return compose


def save_env_info(
    install_dir: str,
    arch: dict,
    postgres_password: str,
    nextauth_secret: str,
    ztnet_port: int = 3000,
    zt_subnet: str = "10.121.15.0/24",
    network_id: str = "",
    docker_bridge_subnet: str = "172.31.255.0/29",
    docker_bridge_gw: str = "172.31.255.1"
) -> None:
    env_file = os.path.join(install_dir, ".env.info")
    server_ip = arch["public_ip"]

    if server_ip and ":" in server_ip:
        server_ip_url = f"[{server_ip}]"
    else:
        server_ip_url = server_ip
    nextauth_url = f"http://{server_ip_url}:{ztnet_port}"

    content = f"""# ZTNET Installation info
SERVER_IP={server_ip}
MAIN_IFACE={arch['main_iface']}
MAIN_IP={arch['main_ip']}
PUBLIC_IP={arch['public_ip']}
IS_OPENVZ={str(arch['is_openvz']).lower()}
ZTNET_URL={nextauth_url}
POSTGRES_PASSWORD={postgres_password}
NEXTAUTH_SECRET={nextauth_secret}
INSTALL_DIR={install_dir}
DOCKER_BRIDGE_SUBNET={docker_bridge_subnet}
DOCKER_BRIDGE_GW={docker_bridge_gw}
ZT_SUBNET={zt_subnet}
"""
    if network_id:
        content += f"NETWORK_ID={network_id}\n"

    Path(env_file).write_text(content)
    os.chmod(env_file, 0o600)


def run_install(
    ztnet_port: int = 3000,
    docker_bridge_subnet: str = "172.31.255.0/29",
    docker_bridge_gw: str = "172.31.255.1",
    zt_subnet: str = "10.121.15.0/24"
) -> int:
    lock = FileLock(LOCK_FILE)
    if not lock.acquire():
        console.print("[error]Другой экземпляр установки уже запущен[/error]")
        return 1

    try:
        console.print("\n[info]═══ ZeroTier + ZTNET Panel Installer ═══[/info]\n")

        # Анализ сети
        console.print("[info]Анализ сетевой архитектуры...[/info]")
        arch = detect_network_architecture()
        console.print(f"  Основной интерфейс: {arch['main_iface']}")
        console.print(f"  Локальный IP: {arch['main_ip']}")
        console.print(f"  Публичный IP: {arch['public_ip']}")
        console.print(f"  OpenVZ/LXC: {arch['is_openvz']}")

        # Пароли
        env_file = os.path.join(INSTALL_DIR, ".env.info")
        if os.path.isfile(env_file):
            from src.zerotier.nat import load_env
            existing = load_env()
            postgres_password = existing.get("POSTGRES_PASSWORD", secrets.token_hex(16))
            nextauth_secret = existing.get("NEXTAUTH_SECRET", secrets.token_hex(32))
            console.print("[warning]Обнаружен существующий .env.info — используем сохранённые пароли[/warning]")
        else:
            postgres_password = secrets.token_hex(16)
            nextauth_secret = secrets.token_hex(32)

        # TUN check
        if not os.path.exists("/dev/net/tun"):
            run("mkdir -p /dev/net && mknod /dev/net/tun c 10 200 2>/dev/null")
            if not os.path.exists("/dev/net/tun"):
                console.print("[error]/dev/net/tun недоступен — ZeroTier не запустится[/error]")
                return 1
        console.print("[success]/dev/net/tun доступен[/success]")

        # Системные зависимости
        console.print("\n[info]Шаг 1/6: Обновление системы...[/info]")
        res_update = run("apt-get update -qq", timeout=300)
        if not res_update.ok:
            console.print("[error]Ошибка при обновлении списка пакетов (apt-get update)[/error]")
            return 1

        res_install = run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl wget ca-certificates gnupg lsb-release openssl iptables-persistent", timeout=600)
        if not res_install.ok:
            console.print("[error]Ошибка при установке системных зависимостей[/error]")
            return 1

        # ZeroTier на хосте
        console.print("[info]Шаг 2/6: Очистка системы от старого ZeroTier...[/info]")
        if command_exists("zerotier-one") or run("dpkg -l | grep -q zerotier-one").ok:
            console.print("[warning]Обнаружен системный zerotier-one, удаляем для избежания конфликта портов с Docker...[/warning]")
            systemctl("stop", "zerotier-one")
            systemctl("disable", "zerotier-one")
            run("apt-get purge -y -qq zerotier-one 2>/dev/null")
            run("pkill -9 -x zerotier-one 2>/dev/null")
            console.print("[success]Системный ZeroTier удален.[/success]")

        # Освобождаем порт 9993 если все еще занят
        if run("ss -tuln | grep -q ':9993 '").ok:
            console.print("[warning]Порт 9993 занят — освобождаем...[/warning]")
            run("fuser -k 9993/tcp 9993/udp 2>/dev/null")
            time.sleep(2)
        console.print("[success]Порт 9993 свободен для Docker-контейнера[/success]")

        # Docker
        console.print("[info]Шаг 3/6: Установка Docker...[/info]")
        if not command_exists("docker"):
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as tmp:
                docker_tmp_path = tmp.name
            try:
                result = run(f"curl -fsSL https://get.docker.com -o {docker_tmp_path}", timeout=60)
                if not result.ok:
                    console.print("[error]Не удалось загрузить скрипт установки Docker[/error]")
                    return 1
                with open(docker_tmp_path, 'r') as f:
                    first_line = f.readline().strip()
                    if not first_line or not first_line.startswith('#!/'):
                        console.print("[error]Загруженный скрипт Docker невалиден[/error]")
                        return 1
                os.chmod(docker_tmp_path, 0o755)
                result = run(f"bash {docker_tmp_path}", timeout=300)
                if not result.ok:
                    console.print("[error]Ошибка установки Docker[/error]")
                    return 1
            finally:
                if os.path.exists(docker_tmp_path):
                    os.remove(docker_tmp_path)
            systemctl("enable", "docker")
            systemctl("start", "docker")

        # IP Forwarding
        console.print("[info]Шаг 4/6: Настройка IP Forwarding...[/info]")
        Path("/etc/sysctl.d/99-zt-forward.conf").write_text(
            "net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n"
        )
        run("sysctl --system > /dev/null 2>&1")

        # Docker Compose
        console.print("[info]Шаг 5/6: Настройка ZTNET + NAT...[/info]")
        Path(INSTALL_DIR).mkdir(parents=True, exist_ok=True)

        compose_content = generate_docker_compose(
            INSTALL_DIR, postgres_password, nextauth_secret, arch["public_ip"], ztnet_port,
            docker_bridge_subnet, docker_bridge_gw
        )
        compose_file = os.path.join(INSTALL_DIR, "docker-compose.yml")
        Path(compose_file).write_text(compose_content)
        os.chmod(compose_file, 0o600)

        # local.conf для ZeroTier
        local_conf_content = f"""{{
    "settings": {{
        "primaryPort": 9993,
        "portMappingEnabled": true,
        "softwareUpdate": "disable",
        "allowManagementFrom": ["127.0.0.1", "{docker_bridge_subnet}"],
        "allowTcpFallbackRelay": true
    }},
    "physical": {{
        "::/0": {{ "blacklist": true }}
    }}
}}"""
        local_conf_file = os.path.join(INSTALL_DIR, "local.conf")
        Path(local_conf_file).write_text(local_conf_content)
        os.chmod(local_conf_file, 0o600)

        # UFW
        if command_exists("ufw"):
            run("ufw allow 9993/udp")
            run("ufw allow 9993/tcp")
            run(f"ufw allow {ztnet_port}/tcp")
            run("ufw default allow routed")

        # iptables NAT
        main_iface = arch["main_iface"]
        server_ip = arch["public_ip"]
        out_flag = f"-o {main_iface}" if main_iface else ""

        if arch["is_openvz"]:
            run(f"iptables -t nat -C POSTROUTING -s {zt_subnet} {out_flag} -j SNAT --to-source {server_ip} 2>/dev/null || iptables -t nat -A POSTROUTING -s {zt_subnet} {out_flag} -j SNAT --to-source {server_ip}")
        else:
            run(f"iptables -t nat -C POSTROUTING -s {zt_subnet} {out_flag} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s {zt_subnet} {out_flag} -j MASQUERADE")

        run(f"iptables -C FORWARD -s {zt_subnet} -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -s {zt_subnet} -j ACCEPT")
        run(f"iptables -C FORWARD -d {zt_subnet} -j ACCEPT 2>/dev/null || iptables -I FORWARD 2 -d {zt_subnet} -j ACCEPT")

        run("netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4 2>/dev/null")

        save_env_info(INSTALL_DIR, arch, postgres_password, nextauth_secret, ztnet_port, zt_subnet, "", docker_bridge_subnet, docker_bridge_gw)

        # Запуск контейнеров
        console.print("[info]Шаг 6/6: Запуск ZTNET...[/info]")
        run(f"docker compose -f {compose_file} pull -q", timeout=600)
        run(f"docker compose -f {compose_file} up -d --wait", timeout=300)

        # Ожидание ONLINE
        console.print("[info]Ожидание ONLINE статуса...[/info]")
        for i in range(12):
            info = docker_exec(CONTAINER, "zerotier-cli info").output
            if "ONLINE" in info or "TUNNELED" in info:
                console.print(f"[success]Контроллер {info}[/success]")
                break
            time.sleep(5)

        # Установка systemd таймеров
        console.print("[info]Настройка фоновых задач (watchdog, reconcile)...[/info]")
        project_dir = os.environ.get("PYTHONPATH", "/opt/my-vds")
        systemd_dir = os.path.join(project_dir, "systemd")
        if os.path.exists(systemd_dir):
            run(f"cp {systemd_dir}/* /etc/systemd/system/")
            run("systemctl daemon-reload")
            systemctl("enable", "vds-watchdog.timer")
            systemctl("start", "vds-watchdog.timer")
            systemctl("enable", "vds-reconcile.timer")
            systemctl("start", "vds-reconcile.timer")
            console.print("[success]Фоновые задачи запущены[/success]")
        else:
            console.print("[warning]Папка systemd не найдена — таймеры не установлены[/warning]")

        console.print(f"\n[success]═══ Установка завершена ═══[/success]")
        console.print(f"  ZTNET Panel: http://{arch['public_ip']}:{ztnet_port}")
        console.print(f"  Директория: {INSTALL_DIR}")

        return 0
    finally:
        lock.release()
