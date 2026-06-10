import logging
import os
from typing import Optional

from src.core.shell import run, command_exists
from src.core.logger import console

logger = logging.getLogger(__name__)

INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/ztnet")
ENV_FILE = os.path.join(INSTALL_DIR, ".env.info")


def load_env() -> dict[str, str]:
    env = {}
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    env[key.strip()] = val.strip()
    return env


def get_main_iface() -> str:
    result = run("ip -4 route show default | grep -oP 'dev \\K\\S+' | head -1")
    if result.ok and result.output:
        return result.output
    result = run("ip -4 route show default | awk '/dev/{for(i=1;i<=NF;i++) if($i==\"dev\") print $(i+1)}' | head -1")
    return result.output if result.ok else "eth0"


def get_public_ip() -> str:
    result = run("curl -s4 --max-time 5 https://ifconfig.me 2>/dev/null || curl -s --max-time 5 https://api.ipify.org 2>/dev/null")
    return result.output if result.ok else ""


def setup_nat_for_subnet(
    subnet: str,
    main_iface: str,
    server_ip: str,
    is_openvz: bool = False,
) -> bool:
    if is_openvz:
        check = run(f"iptables -t nat -C POSTROUTING -s {subnet} -o {main_iface} -j SNAT --to-source {server_ip} 2>/dev/null")
        if not check.ok:
            result = run(f"iptables -t nat -A POSTROUTING -s {subnet} -o {main_iface} -j SNAT --to-source {server_ip}")
            if result.ok:
                console.print(f"[success]SNAT: {subnet} -> {main_iface} (src={server_ip})[/success]")
                return True
            console.print(f"[error]Failed to add SNAT for {subnet}[/error]")
            return False
        return True
    else:
        check = run(f"iptables -t nat -C POSTROUTING -s {subnet} -o {main_iface} -j MASQUERADE 2>/dev/null")
        if not check.ok:
            result = run(f"iptables -t nat -A POSTROUTING -s {subnet} -o {main_iface} -j MASQUERADE")
            if result.ok:
                console.print(f"[success]MASQUERADE: {subnet} -> {main_iface}[/success]")
                return True
            console.print(f"[error]Failed to add MASQUERADE for {subnet}[/error]")
            return False
        return True


def setup_forward_for_subnet(subnet: str) -> bool:
    run(f"iptables -C FORWARD -s {subnet} -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -s {subnet} -j ACCEPT")
    run(f"iptables -C FORWARD -d {subnet} -j ACCEPT 2>/dev/null || iptables -I FORWARD 2 -d {subnet} -j ACCEPT")
    return True


def setup_zt_interface_forward(main_iface: str) -> None:
    result = run("ip -o link show | grep -oP 'zt[a-z0-9]+'")
    if not result.ok or not result.output:
        return
    for iface in result.output.splitlines():
        iface = iface.strip()
        if not iface:
            continue
        run(f"iptables -C FORWARD -i {iface} -o {main_iface} -j ACCEPT 2>/dev/null || iptables -A FORWARD -i {iface} -o {main_iface} -j ACCEPT")
        run(f"iptables -C FORWARD -i {main_iface} -o {iface} -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || iptables -A FORWARD -i {main_iface} -o {iface} -m state --state RELATED,ESTABLISHED -j ACCEPT")
        console.print(f"[success]FORWARD rules for {iface} configured[/success]")


def save_iptables() -> bool:
    result = run("netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4 2>/dev/null")
    return result.ok


def setup_nat_all() -> int:
    env = load_env()
    main_iface = env.get("MAIN_IFACE") or get_main_iface()
    server_ip = env.get("PUBLIC_IP") or get_public_ip()
    is_openvz = env.get("IS_OPENVZ", "false") == "true"
    docker_bridge = env.get("DOCKER_BRIDGE_SUBNET", "172.31.255.0/29")

    console.print(f"[info]Main iface  : {main_iface}[/info]")
    console.print(f"[info]Server IP   : {server_ip}[/info]")
    console.print(f"[info]OpenVZ      : {is_openvz}[/info]")

    subnets_str = env.get("ZT_SUBNETS") or env.get("ZT_SUBNET", "10.121.15.0/24")
    subnets = [s.strip() for s in subnets_str.split(",") if s.strip()]

    for subnet in subnets:
        console.print(f"[info]ZT subnet   : {subnet}[/info]")
        setup_forward_for_subnet(subnet)
        setup_nat_for_subnet(subnet, main_iface, server_ip, is_openvz)

    setup_zt_interface_forward(main_iface)

    run(f"iptables -t nat -C POSTROUTING -s {docker_bridge} -o {main_iface} -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s {docker_bridge} -o {main_iface} -j MASQUERADE")

    save_iptables()
    console.print(f"[success]Rules saved for {len(subnets)} networks[/success]")
    return 0


def cleanup_nat(subnets: list[str]) -> int:
    env = load_env()
    main_iface = env.get("MAIN_IFACE") or get_main_iface()
    server_ip = env.get("PUBLIC_IP") or get_public_ip()
    is_openvz = env.get("IS_OPENVZ", "false") == "true"
    docker_bridge = env.get("DOCKER_BRIDGE_SUBNET", "172.31.255.0/29")

    for subnet in subnets:
        if is_openvz and main_iface and server_ip:
            run(f"iptables -t nat -D POSTROUTING -s {subnet} -o {main_iface} -j SNAT --to-source {server_ip} 2>/dev/null")
        else:
            run(f"iptables -t nat -D POSTROUTING -s {subnet} -o {main_iface} -j MASQUERADE 2>/dev/null")
        run(f"iptables -t nat -D POSTROUTING -s {subnet} -j MASQUERADE 2>/dev/null")
        run(f"iptables -D FORWARD -s {subnet} -j ACCEPT 2>/dev/null")
        run(f"iptables -D FORWARD -d {subnet} -j ACCEPT 2>/dev/null")

    run(f"iptables -t nat -D POSTROUTING -s {docker_bridge} -o {main_iface} -j MASQUERADE 2>/dev/null")
    run(f"iptables -D FORWARD -s {docker_bridge} -j ACCEPT 2>/dev/null")
    run(f"iptables -D FORWARD -d {docker_bridge} -j ACCEPT 2>/dev/null")

    save_iptables()
    console.print(f"[success]NAT rules cleaned for {len(subnets)} subnets[/success]")
    return 0
