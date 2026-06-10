import time
import json
import os
from pathlib import Path
from typing import Optional

import questionary

from src.core.logger import console
from src.core.shell import run
from src.zerotier.api import ZeroTierAPI
from src.zerotier.nat import get_main_iface, get_public_ip

def zt_cli(cmd: str) -> str:
    res = run(f"docker exec ztnet_zerotier zerotier-cli {cmd}")
    return res.output.strip() if res.ok else ""

def get_zt_ip(network_id: str) -> str:
    out = zt_cli("listnetworks")
    for line in out.splitlines():
        if network_id in line:
            parts = line.split()
            if len(parts) > 8 and "/" in parts[-1]:
                return parts[-1]
            if len(parts) > 7 and "/" in parts[-2]:
                return parts[-2]
    return ""

def update_env_info(new_subnet: str, network_id: str):
    env_file = Path("/opt/ztnet/.env.info")
    if not env_file.exists():
        return
        
    content = env_file.read_text()
    lines = content.splitlines()
    
    # Extract existing subnets
    existing_subnets = []
    existing_nids = []
    
    for line in lines:
        if line.startswith("ZT_SUBNETS="):
            existing_subnets = line.split("=", 1)[1].split(",")
        elif line.startswith("NETWORK_IDS="):
            existing_nids = line.split("=", 1)[1].split(",")
            
    if new_subnet and new_subnet not in existing_subnets:
        existing_subnets.append(new_subnet)
    if network_id and network_id not in existing_nids:
        existing_nids.append(network_id)
        
    out_lines = []
    for line in lines:
        if line.startswith("ZT_SUBNETS="):
            out_lines.append(f"ZT_SUBNETS={','.join(existing_subnets)}")
        elif line.startswith("NETWORK_IDS="):
            out_lines.append(f"NETWORK_IDS={','.join(existing_nids)}")
        else:
            out_lines.append(line)
            
    if not any(l.startswith("ZT_SUBNETS=") for l in lines):
        out_lines.append(f"ZT_SUBNETS={','.join(existing_subnets)}")
    if not any(l.startswith("NETWORK_IDS=") for l in lines):
        out_lines.append(f"NETWORK_IDS={','.join(existing_nids)}")
        
    env_file.write_text("\n".join(out_lines) + "\n")
    return existing_subnets

def regen_nat_script():
    # Will just call the Python nat.py which handles everything dynamically
    from src.zerotier.nat import setup_nat_all
    setup_nat_all()

def run_add_network() -> int:
    if os.geteuid() != 0:
        console.print("[red]Запустите команду от имени root[/red]")
        return 1

    console.print("\n[cyan]=== ZeroTier — Добавление новой сети ===[/cyan]")
    
    if "ztnet_zerotier" not in run("docker ps --format '{{.Names}}'").output:
        console.print("[red]Контейнер ztnet_zerotier не запущен![/red]")
        return 1

    zt_addr = zt_cli("info").split()[2] if len(zt_cli("info").split()) > 2 else ""
    if not zt_addr:
        console.print("[red]Не удалось получить ZeroTier node address[/red]")
        return 1

    console.print(f"[green][+][/green] ZeroTier node: {zt_addr}")
    
    console.print("\n[cyan]Текущие сети ZeroTier:[/cyan]")
    console.print(zt_cli("listnetworks"))
    
    network_id = questionary.text("Вставьте Network ID новой сети (16 символов):").ask()
    if not network_id:
        return 1
    network_id = network_id.strip()

    if network_id in zt_cli("listnetworks"):
        console.print(f"[yellow]Нода уже подключена к сети {network_id}[/yellow]")
        if not questionary.confirm("Продолжить настройку NAT?").ask():
            return 0
    else:
        console.print(f"[cyan][->][/cyan] Подключение к сети {network_id}...")
        res = run(f"docker exec ztnet_zerotier zerotier-cli join {network_id}")
        if not res.ok:
            console.print(f"[red]Не удалось подключиться к сети {network_id}[/red]")
            return 1

    console.print(f"[cyan][->][/cyan] Само-авторизация ноды {zt_addr}...")
    api = ZeroTierAPI()
    try:
        api.set_member_auth(network_id, zt_addr, True)
        api.set_member_name(network_id, zt_addr, "Gateway Node")
        console.print(f"[green][+][/green] Нода {zt_addr} авторизована в сети {network_id}")
    except Exception as e:
        console.print(f"[yellow]Авто-авторизация не удалась: {e}[/yellow]")
        console.print("\n[bold yellow]ДЕЙСТВИЕ ТРЕБУЕТСЯ:[/bold yellow]")
        console.print(f"Авторизуйте ноду [cyan]{zt_addr}[/cyan] в панели ZTNET!")
        questionary.press_any_key_to_continue("Нажмите Enter после авторизации...").ask()

    console.print(f"[cyan][->][/cyan] Ожидание назначения ZT-IP...")
    new_zt_ip = ""
    for i in range(30):
        new_zt_ip = get_zt_ip(network_id)
        if new_zt_ip and not new_zt_ip.startswith("-"):
            console.print(f"\n[green][+][/green] ZT-IP получен: {new_zt_ip}")
            break
        print(f"\r  Ожидание ZT-IP... ({i+1}/30)", end="")
        time.sleep(5)
        
    if not new_zt_ip or new_zt_ip.startswith("-"):
        console.print("\n[yellow]ZT-IP не получен автоматически.[/yellow]")
        manual_ip = questionary.text("Введите ZT-IP вручную (например 10.121.16.1/24) или оставьте пустым для отмены:").ask()
        if not manual_ip:
            return 1
        new_zt_ip = manual_ip

    new_subnet = ""
    try:
        net_info = api.get_network(network_id)
        pools = net_info.get("ipAssignmentPools", [])
        if pools:
            start_ip = pools[0].get("ipRangeStart", "")
            if start_ip:
                parts = start_ip.split(".")
                new_subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    except:
        pass

    if not new_subnet:
        import re
        ip_part = new_zt_ip.split("/")[0]
        prefix = new_zt_ip.split("/")[1] if "/" in new_zt_ip else "24"
        parts = ip_part.split(".")
        new_subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/{prefix}"

    console.print(f"[green][+][/green] Subnet новой сети: {new_subnet}")

    console.print(f"[cyan][->][/cyan] Обновление конфигурации и NAT...")
    update_env_info(new_subnet, network_id)
    regen_nat_script()
    
    console.print(f"\n[bold green]Сеть {network_id} добавлена![/bold green]")
    console.print(f"  Network ID: [cyan]{network_id}[/cyan]")
    console.print(f"  ZT-IP:      [cyan]{new_zt_ip}[/cyan]")
    console.print(f"  Subnet:     [cyan]{new_subnet}[/cyan]\n")

    zt_ip_only = new_zt_ip.split("/")[0] if "/" in new_zt_ip else new_zt_ip
    
    # Check default routes
    conflict = False
    try:
        nets = json.loads(zt_cli("-j listnetworks"))
        for n in nets:
            nid = n.get("id")
            for r in n.get("routes", []):
                if r.get("target") == "0.0.0.0/0" and r.get("via") and nid != network_id:
                    console.print(f"[yellow]ОБНАРУЖЕН МАРШРУТ 0.0.0.0/0 В ДРУГОЙ СЕТИ: {nid}[/yellow]")
                    conflict = True
    except:
        pass

    if conflict:
        console.print("\n[bold yellow]ВНИМАНИЕ: Managed Route 0.0.0.0/0 НЕ добавлен[/bold yellow]")
        console.print("Только ОДНА сеть может раздавать интернет через 0.0.0.0/0!")
        console.print("Если вам нужен интернет через эту сеть, удалите 0.0.0.0/0 из старой сети.")
    else:
        console.print("\n[bold yellow]ДЕЙСТВИЕ ТРЕБУЕТСЯ (Managed Route):[/bold yellow]")
        console.print("Для раздачи интернета добавьте маршрут в ZTNET Panel:")
        console.print("  Destination: [bold]0.0.0.0/0[/bold]")
        console.print(f"  Via:         [bold]{zt_ip_only}[/bold]")
        console.print("\nНа клиентах выполните:")
        console.print(f"  [cyan]zerotier-cli set {network_id} allowDefault=1[/cyan]")

    return 0
