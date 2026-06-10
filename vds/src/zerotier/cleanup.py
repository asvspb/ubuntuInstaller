import logging
import os
from pathlib import Path

import typer

from src.core.shell import run, docker_exec, systemctl, command_exists
from src.core.lock import FileLock
from src.core.logger import console
from src.zerotier.nat import load_env, cleanup_nat, get_main_iface

logger = logging.getLogger(__name__)

INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/ztnet")
LOCK_FILE = "/var/run/ztnet-cleanup.lock"


def _validate_install_dir(dir_path: str) -> bool:
    """Проверка что путь безопасен для удаления."""
    if not dir_path:
        return False
    if not dir_path.startswith("/"):
        return False
    system_dirs = {"/", "/usr", "/etc", "/home", "/var", "/bin", "/sbin", "/lib", "/boot", "/root"}
    if dir_path in system_dirs:
        return False
    return True


def run_cleanup() -> int:
    lock = FileLock(LOCK_FILE)
    if not lock.acquire():
        console.print("[error]Другой экземпляр очистки уже запущен[/error]")
        return 1

    try:
        console.print("\n[info]═══ ZeroTier + ZTNET — Полная очистка ═══[/info]\n")

        console.print("[warning]Будут удалены:[/warning]")
        console.print("  - Все Docker контейнеры ZTNET")
        console.print("  - Все Docker volumes (identity, БД, конфиги)")
        console.print("  - ZeroTier systemd сервис")
        console.print("  - iptables правила NAT для ZT")
        console.print(f"  - Директория {INSTALL_DIR}")
        console.print("  - systemd zt-nat-setup.service")
        console.print("  - /etc/sysctl.d/99-zt-forward.conf")
        console.print()

        if not typer.confirm("Продолжить очистку?", default=False):
            console.print("[info]Очистка отменена[/info]")
            return 0

        env = load_env()
        all_subnets = env.get("ZT_SUBNETS") or env.get("ZT_SUBNET", "10.121.15.0/24")
        subnets = [s.strip() for s in all_subnets.split(",") if s.strip()]

        # 1. Docker Compose down
        compose_file = os.path.join(INSTALL_DIR, "docker-compose.yml")
        if os.path.isfile(compose_file):
            console.print("[info]Останавливаем контейнеры...[/info]")
            run(f"docker compose -f {compose_file} down --volumes --remove-orphans")
            console.print("[success]Контейнеры и volumes удалены[/success]")
        else:
            for c in ["ztnet", "ztnet_postgres", "ztnet_zerotier"]:
                run(f"docker rm -f {c} 2>/dev/null")
            for v in ["ztnet_postgres-data", "ztnet_zerotier"]:
                run(f"docker volume rm -f {v} 2>/dev/null")

        # 2. Удаление образов
        console.print("[info]Удаляем Docker образы...[/info]")
        for img in ["sinamics/ztnet:latest", "zyclonite/zerotier:1.14.2", "postgres:15.2-alpine"]:
            run(f"docker rmi -f {img} 2>/dev/null")

        # 3. ZeroTier systemd
        console.print("[info]Останавливаем хостовой zerotier-one...[/info]")
        systemctl("stop", "zerotier-one")
        systemctl("disable", "zerotier-one")
        systemctl("unmask", "zerotier-one")
        run("pkill -9 -x zerotier-one 2>/dev/null")

        # 4. iptables
        console.print("[info]Удаляем iptables правила...[/info]")
        cleanup_nat(subnets)

        # UFW
        if command_exists("ufw"):
            run("ufw delete allow 9993/udp 2>/dev/null")
            run("ufw delete allow 9993/tcp 2>/dev/null")
            run("ufw delete allow 3000/tcp 2>/dev/null")

        # 5. Файлы
        console.print("[info]Удаляем файлы конфигурации...[/info]")
        run("rm -f /etc/systemd/system/zt-nat-setup.service 2>/dev/null")
        run("rm -f /etc/systemd/system/zt-watchdog.service 2>/dev/null")
        run("rm -f /etc/systemd/system/zt-watchdog.timer 2>/dev/null")
        run("rm -f /etc/systemd/system/zt-reconcile.service 2>/dev/null")
        run("rm -f /etc/systemd/system/zt-reconcile.timer 2>/dev/null")
        run("rm -f /etc/sysctl.d/99-zt-forward.conf 2>/dev/null")
        systemctl("daemon-reload")

        if os.path.isdir(INSTALL_DIR):
            if not _validate_install_dir(INSTALL_DIR):
                console.print(f"[error]INSTALL_DIR '{INSTALL_DIR}' не прошёл валидацию — пропуск удаления директории[/error]")
            else:
                run(f"rm -rf {INSTALL_DIR}")
                console.print(f"[success]{INSTALL_DIR} удалён[/success]")

        console.print("\n[success]═══ Очистка завершена ═══[/success]")
        console.print("\n[warning]ВАЖНО: Очистите куки браузера для домена сервера перед повторной установкой[/warning]")

        return 0
    finally:
        lock.release()
