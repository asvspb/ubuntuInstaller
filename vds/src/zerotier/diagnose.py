import json
import logging
import os
import time
from typing import Optional

from src.core.shell import run, docker_exec, docker_inspect, systemctl, command_exists
from src.core.lock import FileLock
from src.core.logger import console
from src.zerotier.api import ZeroTierAPI
from src.zerotier.nat import load_env, get_main_iface

logger = logging.getLogger(__name__)

INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/ztnet")
ENV_FILE = os.path.join(INSTALL_DIR, ".env.info")
LOCK_FILE = "/var/run/ztnet-diagnose.lock"
CONTAINER = "ztnet_zerotier"

def run_diagnose(fix: bool = False, auto_yes: bool = False) -> int:
    lock = FileLock(LOCK_FILE)
    if not lock.acquire():
        console.print("[error]Другой экземпляр диагностики уже запущен[/error]")
        return 1

    try:
        critical = 0
        warnings = 0

        console.print("\n[info]═══ ZeroTier + ZTNET — Диагностика ═══[/info]\n")
        env = load_env()

        # 1. Конфликт порта 9993
        console.print("[bold][1/12] Конфликт порта 9993[/bold]")
        if command_exists("zerotier-one") or run("dpkg -l | grep -q zerotier-one").ok:
            console.print("[error][XX] Обнаружен системный zerotier-one. Возможен конфликт.[/error]")
            critical += 1
            if fix or auto_yes:
                run("apt-get purge -y -qq zerotier-one 2>/dev/null")
                run("pkill -9 -x zerotier-one 2>/dev/null")
                console.print("[success][fix] Системный zerotier-one удален.[/success]")
        else:
            console.print("[success][OK] Системный zerotier-one не установлен (конфликтов нет)[/success]")

        # 2. Docker контейнеры
        console.print("\n[bold][2/12] Docker-контейнеры ZTNET[/bold]")
        for svc in ["ztnet", "ztnet_postgres", "ztnet_zerotier"]:
            status = docker_inspect(svc, "{{.State.Status}}")
            restarts = docker_inspect(svc, "{{.RestartCount}}")
            
            if status == "running":
                restarts_int = int(restarts) if restarts.isdigit() else 0
                if restarts_int > 5:
                    console.print(f"[warning][!!] {svc}: running (restarts: {restarts}) — возможен crash loop[/warning]")
                    warnings += 1
                else:
                    console.print(f"[success][OK] {svc}: running (restarts: {restarts})[/success]")
            else:
                console.print(f"[error][XX] {svc}: {status or 'NOT FOUND'}[/error]")
                critical += 1

        # 3. ZeroTier демон
        console.print("\n[bold][3/12] ZeroTier демон[/bold]")
        with ZeroTierAPI() as api:
            status = api.get_status()
            if not status:
                console.print("[error][XX] ZeroTier демон не отвечает (API недоступно)[/error]")
                critical += 1
            else:
                if status.online:
                    console.print(f"[success][OK] Статус: ONLINE, версия {status.version}, адрес {status.address}[/success]")
                else:
                    console.print(f"[warning][!!] Статус: OFFLINE, версия {status.version}[/warning]")
                    warnings += 1

            # 4. TUN/TAP
            console.print("\n[bold][4/12] TUN/TAP устройство[/bold]")
            if docker_exec(CONTAINER, "ls -la /dev/net/tun").ok:
                console.print("[success][OK] /dev/net/tun доступен в контейнере[/success]")
            else:
                console.print("[error][XX] /dev/net/tun НЕ доступен в контейнере[/error]")
                critical += 1

            if os.path.exists("/dev/net/tun"):
                console.print("[success][OK] /dev/net/tun существует на хосте[/success]")
            else:
                console.print("[error][XX] /dev/net/tun не найден на хосте[/error]")
                critical += 1

            # 5. Сети и маршруты
            console.print("\n[bold][5/12] Сети и маршруты[/bold]")
            networks = api.get_networks()
            if not networks:
                console.print("[warning][!!] Нет подключённых сетей[/warning]")
                warnings += 1
            
            for net in networks:
                nwid = net.get("id", "?")
                name = net.get("name", "?")
                net_status = net.get("status", "?")
                ips = ", ".join(net.get("assignedAddresses", [])) or "no IP"
                dev = net.get("portDeviceName", "?")
                
                if net_status == "OK":
                    console.print(f"[success][OK] {nwid} ({name}): OK dev={dev} ip={ips}[/success]")
                else:
                    console.print(f"[warning][!!] {nwid} ({name}): {net_status} ip={ips}[/warning]")
                    warnings += 1

            # 6. Члены сетей (Members)
            console.print("\n[bold][6/12] Члены сетей (Members)[/bold]")
            zt_addr = api.get_zt_addr()
            for net in networks:
                nwid = net.get("id")
                if not nwid: continue
                name = net.get("name", "?")
                console.print(f"[cyan][>>] Сеть {nwid} ({name}):[/cyan]")
                members = api.get_members(nwid)
                
                if not members:
                    console.print("  [warning]Нет участников или ошибка доступа к Controller API[/warning]")
                    continue
                    
                for addr, member in members.items():
                    ip_assignments = member.get("ipAssignments", [])
                    ips_str = ",".join(ip_assignments) if ip_assignments else "no-ip"
                    vrev = member.get("vRev", -1)
                    
                    if addr == zt_addr:
                        console.print(f"  {addr}: ip={ips_str} vRev={vrev} CONTROLLER")
                    else:
                        if vrev == 0:
                            console.print(f"  [error][XX] {addr}: ip={ips_str} vRev=0 CONFIG_STUCK[/error]")
                            warnings += 1
                            if fix or auto_yes:
                                console.print(f"  [success][fix] Принудительное обновление конфига для {addr}...[/success]")
                                api.authorize_member(nwid, addr, False)
                                time.sleep(1)
                                api.authorize_member(nwid, addr, True)
                        elif vrev == -1:
                            console.print(f"  [warning]{addr}: ip={ips_str} vRev=-1 NEVER_CONNECTED[/warning]")
                        else:
                            console.print(f"  {addr}: ip={ips_str} vRev={vrev}")

            # 7. Пир-соединения (Peers)
            console.print("\n[bold][7/12] Пир-соединения (Peers)[/bold]")
            peers = api.get_peers()
            leaf_count = 0
            planet_count = 0
            for peer in peers:
                role = peer.get("role", "")
                if role == "PLANET":
                    planet_count += 1
                elif role == "LEAF":
                    leaf_count += 1
                    addr = peer.get("address", "")
                    lat = peer.get("latency", -1)
                    path = peer.get("paths", [{}])[0].get("address", "no path") if peer.get("paths") else "no path"
                    console.print(f"  {addr}: LEAF latency={lat}ms path={path}")
            
            console.print(f"[success][OK] PLANET-пиров: {planet_count}, LEAF-пиров: {leaf_count}[/success]")

            # 8. Доступность Планет
            console.print("\n[bold][8/12] Доступность корневых серверов (Planets)[/bold]")
            planet_ip = "103.195.103.66"
            
            ping_res = run(f"ping -c 2 -W 2 {planet_ip}")
            if ping_res.ok:
                console.print(f"[success][OK] Ping до планеты ({planet_ip}): УСПЕШНО[/success]")
            else:
                console.print(f"[error][XX] Ping до планеты ({planet_ip}): ПРОВАЛЕН (Блокировка ICMP?)[/error]")
                critical += 1

            curl_res = run(f"curl -I -m 3 https://{planet_ip} 2>&1")
            out_lower = curl_res.output.lower()
            if "couldn't connect" in out_lower or "timed out" in out_lower or "timeout" in out_lower:
                console.print(f"[error][XX] TCP/443 к планете ({planet_ip}): ПРОВАЛЕН (Провайдер режет TCP до ZeroTier)[/error]")
                critical += 1
            else:
                console.print(f"[success][OK] TCP/443 к планете ({planet_ip}): УСПЕШНО (TCP Fallback работает)[/success]")

            # 9. NAT / IP Forwarding / Firewall
            console.print("\n[bold][9/12] NAT / IP Forwarding / Firewall[/bold]")
            ip_fwd = run("sysctl -n net.ipv4.ip_forward 2>/dev/null").output.strip()
            if ip_fwd == "1":
                console.print("[success][OK] IP forwarding: включён[/success]")
            else:
                console.print("[error][XX] IP forwarding: ВЫКЛЮЧЕН[/error]")
                critical += 1
                if fix or auto_yes:
                    run("sysctl -w net.ipv4.ip_forward=1")
                    run("echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-zt-forward.conf")

            if command_exists("ufw"):
                ufw_status = run("ufw status 2>/dev/null").output
                if "9993" in ufw_status:
                    console.print("[success][OK] UFW: порт 9993 открыт[/success]")
                else:
                    console.print("[warning][!!] UFW активен, но порт 9993 НЕ открыт явно[/warning]")
                    warnings += 1

            # 10. Тест связности
            console.print("\n[bold][10/12] Тест связности[/bold]")
            for net in networks:
                nwid = net.get("id")
                name = net.get("name", "?")
                my_ips = net.get("assignedAddresses", [])
                
                if my_ips:
                    my_ip = my_ips[0].split("/")[0]
                    ping_res = run(f"ping -c 1 -W 2 {my_ip}")
                    if ping_res.ok:
                        console.print(f"[success][OK] Self-ping {my_ip} ({name}): OK[/success]")
                    else:
                        console.print(f"[error][XX] Self-ping {my_ip} ({name}): FAIL[/error]")
                        critical += 1
                
                members = api.get_members(nwid) if nwid else {}
                for addr, member in members.items():
                    if addr == zt_addr: continue
                    ip_assignments = member.get("ipAssignments", [])
                    if not ip_assignments: continue
                    
                    target_ip = ip_assignments[0]
                    ping_res = run(f"ping -c 1 -W 2 {target_ip}")
                    if ping_res.ok:
                        lat = ping_res.output.split("time=")[-1].split(" ")[0] if "time=" in ping_res.output else "?"
                        console.print(f"[success][OK] Ping {target_ip} ({addr}): OK ({lat}ms)[/success]")
                    else:
                        console.print(f"[warning][!!] Ping {target_ip} ({addr}): НЕУДАЧА — пир недоступен[/warning]")
                        # It's a warning because peers could simply be offline
            
        # 11. Персистентность
        console.print("\n[bold][11/12] Персистентность[/bold]")
        if os.path.isfile("/etc/iptables/rules.v4"):
            console.print("[success][OK] /etc/iptables/rules.v4 существует[/success]")
        else:
            console.print("[warning][!!] /etc/iptables/rules.v4 НЕ найден (правила iptables могут слететь при перезагрузке)[/warning]")
            warnings += 1
            if fix or auto_yes:
                run("mkdir -p /etc/iptables && iptables-save > /etc/iptables/rules.v4")
                
        if os.path.isfile("/etc/sysctl.d/99-zt-forward.conf"):
            console.print("[success][OK] sysctl ip_forward сохранён[/success]")
        else:
            console.print("[warning][!!] sysctl ip_forward НЕ сохранён[/warning]")
            warnings += 1
            if fix or auto_yes:
                run("echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-zt-forward.conf")

        # Итог
        console.print("\n[info]═══ Итог ═══[/info]")
        if critical == 0 and warnings == 0:
            console.print("[success]Всё в порядке — проблем не обнаружено[/success]")
        elif critical == 0:
            console.print(f"[warning]Предупреждений: {warnings} — рекомендуется обратить внимание (выключенные пиры - это норма)[/warning]")
        else:
            console.print(f"[error]Критических: {critical}, предупреждений: {warnings}[/error]")

        return 0 if critical == 0 else 1
    finally:
        lock.release()
