import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.core.lock import FileLock
from src.core.shell import run, docker_exec, docker_inspect, systemctl
from src.core.logger import console, rotate_log
from src.zerotier.nat import setup_nat_for_subnet, setup_forward_for_subnet, save_iptables, load_env, get_main_iface, get_public_ip

logger = logging.getLogger(__name__)

INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/ztnet")
LOG_FILE = os.path.join(INSTALL_DIR, "zt-watchdog.log")
LOCK_FILE = "/var/run/ztnet-watchdog.lock"
CONTAINER = "ztnet_zerotier"
CHECK_WINDOW = "5m"
MAX_RESTARTS_PER_HOUR = 3
STATE_DIR = "/var/lib/vds"
STATE_FILE = os.path.join(STATE_DIR, "zt-watchdog-restarts")


def count_recent_restarts() -> int:
    if not os.path.isfile(STATE_FILE):
        return 0
    cutoff = datetime.now() - timedelta(hours=1)
    count = 0
    try:
        with open(STATE_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = datetime.strptime(line, "%Y-%m-%d %H:%M:%S")
                    if ts >= cutoff:
                        count += 1
                except ValueError:
                    continue
    except OSError:
        pass
    return count


def record_restart() -> None:
    cutoff = datetime.now() - timedelta(hours=1)
    lines = []
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ts = datetime.strptime(line, "%Y-%m-%d %H:%M:%S")
                        if ts >= cutoff:
                            lines.append(line)
                    except ValueError:
                        continue
        except OSError:
            pass
    lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")


def kill_port_thief() -> None:
    result = run("ss -tlnup 2>/dev/null | grep ':9993 ' | grep -oP 'pid=\\K\\d+'")
    if not result.ok or not result.output:
        return

    container_pid = docker_inspect(CONTAINER, "{{.State.Pid}}")

    for pid_str in result.output.splitlines():
        pid = pid_str.strip()
        if not pid:
            continue
        if pid == container_pid:
            continue
        comm_result = run(f"cat /proc/{pid}/comm 2>/dev/null")
        comm = comm_result.output if comm_result.ok else "?"
        logger.info(f"Порт 9993 занят процессом {comm} (PID {pid}) — убиваем")
        run(f"kill -9 {pid} 2>/dev/null")
        time.sleep(2)


def kill_system_zt() -> None:
    if systemctl("is-active", "zerotier-one").ok:
        logger.info("Системный zerotier-one активен — останавливаем")
        systemctl("stop", "zerotier-one")
        systemctl("mask", "zerotier-one")

    if run("pgrep -x zerotier-one").ok:
        logger.info("Процесс zerotier-one найден — убиваем")
        run("pkill -9 -x zerotier-one 2>/dev/null")
        time.sleep(2)


def restart_container() -> bool:
    recent = count_recent_restarts()
    if recent >= MAX_RESTARTS_PER_HOUR:
        logger.warning(f"ОСТАНОВЛЕН: уже {recent} рестартов за час (лимит {MAX_RESTARTS_PER_HOUR})")
        return False

    logger.info(f"Рестарт #{recent + 1}/{MAX_RESTARTS_PER_HOUR} за последний час")
    record_restart()

    run(f"docker stop {CONTAINER} >/dev/null 2>&1")
    time.sleep(3)
    run(f"docker start {CONTAINER} >/dev/null 2>&1")
    logger.info("Контейнер перезапущен, ожидаем ONLINE...")

    for i in range(12):
        time.sleep(5)
        info = docker_exec(CONTAINER, "zerotier-cli info").output
        if "ONLINE" in info or "TUNNELED" in info:
            logger.info(f"Восстановлен: {info}")
            return True
        logger.info(f"  Ожидание... ({(i + 1) * 5}с): {info or 'нет ответа'}")

    logger.error("ОШИБКА: контейнер не перешёл в ONLINE за 60с после рестарта")
    return False


def check_nat_rules() -> None:
    env = load_env()
    all_subnets = env.get("ZT_SUBNETS") or env.get("ZT_SUBNET", "")
    main_iface = env.get("MAIN_IFACE") or get_main_iface()
    server_ip = env.get("PUBLIC_IP") or get_public_ip()
    is_openvz = env.get("IS_OPENVZ", "false") == "true"

    runtime_result = docker_exec(CONTAINER, "zerotier-cli -j listnetworks")
    runtime_subnets = []
    if runtime_result.ok:
        try:
            import json
            import ipaddress
            nets = json.loads(runtime_result.output)
            for n in nets:
                for a in n.get("assignedAddresses", []):
                    parts = a.split("/")
                    if len(parts) == 2:
                        net = ipaddress.ip_network(f"{parts[0]}/{parts[1]}", strict=False)
                        runtime_subnets.append(str(net))
        except (json.JSONDecodeError, ValueError):
            pass

    all_subs = set(runtime_subnets)
    if all_subnets:
        all_subs.update(s.strip() for s in all_subnets.split(",") if s.strip())

    for sub in all_subs:
        if not sub:
            continue
        check = run(f"iptables -t nat -L POSTROUTING -n 2>/dev/null | grep '{sub}'")
        if not check.ok or not check.output:
            logger.warning(f"NAT для {sub} отсутствует — восстанавливаем")
            setup_forward_for_subnet(sub)
            setup_nat_for_subnet(sub, main_iface, server_ip, is_openvz)
            save_iptables()
            logger.info(f"NAT для {sub} восстановлен")


def cleanup_stale_networks() -> None:
    result = docker_exec(CONTAINER, "zerotier-cli -j listnetworks")
    if not result.ok:
        return
    try:
        import json
        nets = json.loads(result.output)
        for n in nets:
            if n.get("status") == "NOT_FOUND":
                nwid = n.get("id")
                if nwid:
                    logger.warning(f"Удаление мертвой сети {nwid} (NOT_FOUND)")
                    docker_exec(CONTAINER, f"zerotier-cli leave {nwid}")
    except (json.JSONDecodeError, ValueError):
        pass


def run_watchdog() -> int:
    rotate_log(LOG_FILE)

    lock = FileLock(LOCK_FILE)
    if not lock.acquire():
        logger.info("Другой экземпляр уже запущен. Выход.")
        return 0

    try:
        bind_result = run(f"docker logs {CONTAINER} --since {CHECK_WINDOW} 2>&1 | grep -cE 'Could not bind|fatal error.*9993'", timeout=30)
        bind_errors = int(bind_result.output) if bind_result.ok and bind_result.output.isdigit() else 0

        zt_info = docker_exec(CONTAINER, "zerotier-cli info").output
        zt_status = ""
        for word in zt_info.split():
            if word in ("ONLINE", "OFFLINE", "TUNNELED", "DEGRADED"):
                zt_status = word
                break

        container_running = docker_inspect(CONTAINER, "{{.State.Running}}") == "true"

        problem = False

        if not container_running:
            logger.warning(f"ПРОБЛЕМА: контейнер {CONTAINER} не запущен")
            problem = True

        if bind_errors > 3:
            logger.warning(f"ПРОБЛЕМА: {bind_errors} ошибок биндинга за {CHECK_WINDOW}")
            problem = True

        if zt_status in ("OFFLINE", ""):
            logger.warning(f"ПРОБЛЕМА: ZT статус={zt_status or 'NO_RESPONSE'}")
            problem = True

        pgrep_result = docker_exec(CONTAINER, "pgrep -x zerotier-one")
        if not pgrep_result.ok:
            logger.warning("ПРОБЛЕМА: процесс zerotier-one не найден в контейнере")
            problem = True

        cleanup_stale_networks()
        check_nat_rules()

        if problem:
            logger.info("Начинаем восстановление...")
            kill_system_zt()
            kill_port_thief()
            if restart_container():
                logger.info("Восстановление завершено успешно")
            else:
                logger.error("Восстановление ЗАВЕРШЕНО С ОШИБКАМИ")
                return 1
        else:
            logger.info(f"OK — ZT {zt_status}, {bind_errors} bind errors")

        return 0
    finally:
        lock.release()
