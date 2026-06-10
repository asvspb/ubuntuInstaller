import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from src.core.shell import run, command_exists, systemctl
from src.core.logger import console

logger = logging.getLogger(__name__)

WG_CONF = "/etc/wireguard/wg0.conf"


def validate_client_name(name: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))


def detect_os() -> tuple[str, str]:
    os_id = ""
    ver = ""
    if os.path.exists("/etc/os-release"):
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    os_id = line.split("=")[1].strip().strip('"')
                if line.startswith("VERSION_ID="):
                    ver = line.split("=")[1].strip().strip('"').replace(".", "")
    return os_id, ver


def get_public_nic() -> str:
    result = run("ip -4 route ls 2>/dev/null | grep default | grep -Po '(?<=dev )(\\S+)' | head -1")
    if result.ok and result.output:
        return result.output
    result = run("ip -4 route ls 2>/dev/null | grep default | awk '{print $5}' | head -1")
    return result.output if result.ok else "eth0"


def generate_keys() -> dict[str, str]:
    server_priv = run("wg genkey").output
    server_pub = subprocess.run(
        ["wg", "pubkey"],
        input=server_priv,
        capture_output=True,
        text=True,
    ).stdout.strip()
    client_key = run("wg genkey").output
    client_psk = run("wg genpsk").output
    client_pub = subprocess.run(
        ["wg", "pubkey"],
        input=client_key,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "server_priv": server_priv,
        "server_pub": server_pub,
        "client_key": client_key,
        "client_psk": client_psk,
        "client_pub": client_pub,
    }


def run_install(
    port: int = 51820,
    client_name: str = "client",
    dns: str = "8.8.8.8, 8.8.4.4",
) -> int:
    if not validate_client_name(client_name):
        console.print("[error]Недопустимое имя клиента. Используйте только буквы, цифры, дефис и подчёркивание[/error]")
        return 1
    
    if os.path.exists(WG_CONF):
        console.print("[warning]WireGuard уже установлен[/warning]")
        console.print("Используйте 'vds wireguard add-client' для добавления клиента")
        console.print("Используйте 'vds wireguard remove' для удаления")
        return 0

    console.print("\n[info]═══ WireGuard VPN Installer ═══[/info]\n")

    pub_nic = get_public_nic()
    console.print(f"[info]Основной интерфейс: {pub_nic}[/info]")

    virt = run("systemd-detect-virt 2>/dev/null").output
    use_boringtun = False
    if "openvz" in virt.lower() or "lxc" in virt.lower():
        if not run("grep -q '^wireguard ' /proc/modules 2>/dev/null").ok:
            use_boringtun = True
            console.print("[warning]Контейнер без модуля — будет установлен BoringTun[/warning]")

    public_ip_result = run("curl -s4 --max-time 5 https://ifconfig.me 2>/dev/null || curl -s --max-time 5 https://api.ipify.org 2>/dev/null")
    public_ip = public_ip_result.output if public_ip_result.ok else ""
    if not public_ip:
        console.print("[error]Не удалось определить публичный IP[/error]")
        return 1

    console.print(f"[info]Публичный IP: {public_ip}[/info]")

    # Установка пакетов
    console.print("[info]Установка пакетов...[/info]")
    run("apt-get update -qq")
    if use_boringtun:
        run("apt-get install -y -qq wireguard-tools ca-certificates wget tar qrencode iproute2 iptables")
    else:
        run("apt-get install -y -qq wireguard qrencode iproute2 iptables")

    if use_boringtun:
        arch = run("uname -m").output
        run(f"wget -qO- https://wg.nyr.be/1/latest/download | tar xz -C /usr/local/sbin/ --wildcards 'boringtun-*/boringtun' --strip-components 1")
        Path("/etc/systemd/system/wg-quick@wg0.service.d/").mkdir(parents=True, exist_ok=True)
        Path("/etc/systemd/system/wg-quick@wg0.service.d/boringtun.conf").write_text(
            "[Service]\nEnvironment=WG_QUICK_USERSPACE_IMPLEMENTATION=boringtun\nEnvironment=WG_SUDO=1\n"
        )

    # Генерация ключей
    keys = generate_keys()

    # Конфиг сервера
    Path("/etc/wireguard").mkdir(parents=True, exist_ok=True)
    os.chmod("/etc/wireguard", 0o700)

    server_conf = f"""# ENDPOINT {public_ip}

[Interface]
Address = 10.7.0.1/24
PrivateKey = {keys['server_priv']}
ListenPort = {port}

# BEGIN_PEER {client_name}
[Peer]
PublicKey = {keys['client_pub']}
PresharedKey = {keys['client_psk']}
AllowedIPs = 10.7.0.2/32
# END_PEER {client_name}
"""
    Path(WG_CONF).write_text(server_conf)
    os.chmod(WG_CONF, 0o600)

    # Конфиг клиента
    client_conf = f"""[Interface]
Address = 10.7.0.2/24
DNS = {dns}
PrivateKey = {keys['client_key']}

[Peer]
PublicKey = {keys['server_pub']}
PresharedKey = {keys['client_psk']}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {public_ip}:{port}
PersistentKeepalive = 25
"""
    client_path = Path.home() / f"{client_name}.conf"
    client_path.write_text(client_conf)
    os.chmod(client_path, 0o600)

    # IP Forwarding
    Path("/etc/sysctl.d/99-wireguard-forward.conf").write_text("net.ipv4.ip_forward=1\n")
    run("sysctl -p /etc/sysctl.d/99-wireguard-forward.conf")

    # UFW
    if command_exists("ufw"):
        run(f"ufw allow {port}/udp")
        run(f"ufw route allow in on wg0 out on {pub_nic}")

    # iptables service
    iptables_svc = f"""[Unit]
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/iptables -w 5 -t nat -A POSTROUTING -s 10.7.0.0/24 ! -d 10.7.0.0/24 -o {pub_nic} -j MASQUERADE
ExecStart=/usr/sbin/iptables -w 5 -I INPUT -p udp --dport {port} -j ACCEPT
ExecStart=/usr/sbin/iptables -w 5 -I FORWARD -s 10.7.0.0/24 -j ACCEPT
ExecStart=/usr/sbin/iptables -w 5 -I FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT
ExecStop=/usr/sbin/iptables -w 5 -t nat -D POSTROUTING -s 10.7.0.0/24 ! -d 10.7.0.0/24 -o {pub_nic} -j MASQUERADE
ExecStop=/usr/sbin/iptables -w 5 -D INPUT -p udp --dport {port} -j ACCEPT
ExecStop=/usr/sbin/iptables -w 5 -D FORWARD -s 10.7.0.0/24 -j ACCEPT
ExecStop=/usr/sbin/iptables -w 5 -D FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
    Path("/etc/systemd/system/wg-iptables.service").write_text(iptables_svc)
    systemctl("enable", "wg-iptables.service")
    systemctl("start", "wg-iptables.service")

    # Запуск WireGuard
    systemctl("enable", "wg-quick@wg0.service")
    systemctl("start", "wg-quick@wg0.service")

    if systemctl("is-active", "wg-quick@wg0.service").ok:
        console.print(f"\n[success]═══ WireGuard запущен ═══[/success]")
        console.print(f"  Конфиг клиента: {client_path}")
        console.print(f"  QR-код:")
        if command_exists("qrencode"):
            with open(client_path, "r") as f:
                subprocess.run(["qrencode", "-t", "ansiutf8", "-m", "1"], stdin=f)
    else:
        console.print("[error]WireGuard не запустился. Проверьте: journalctl -u wg-quick@wg0[/error]")
        return 1

    return 0


def add_client(client_name: str = "client") -> int:
    if not validate_client_name(client_name):
        console.print("[error]Недопустимое имя клиента. Используйте только буквы, цифры, дефис и подчёркивание[/error]")
        return 1
    
    if not os.path.exists(WG_CONF):
        console.print("[error]WireGuard не установлен[/error]")
        return 1

    conf_text = Path(WG_CONF).read_text()

    octet = 2
    while f"10.7.0.{octet}/32" in conf_text:
        octet += 1
        if octet >= 254:
            console.print("[error]Подсеть заполнена (лимит 253 клиента)[/error]")
            return 1

    client_ip = f"10.7.0.{octet}"
    keys = generate_keys()

    endpoint_match = re.search(r"# ENDPOINT (\S+)", conf_text)
    endpoint = endpoint_match.group(1) if endpoint_match else ""
    port_match = re.search(r"ListenPort = (\d+)", conf_text)
    port = port_match.group(1) if port_match else "51820"

    server_pub_result = run("wg show wg0 public-key")
    server_pub = server_pub_result.output if server_pub_result.ok else ""

    peer_block = f"""
# BEGIN_PEER {client_name}
[Peer]
PublicKey = {keys['client_pub']}
PresharedKey = {keys['client_psk']}
AllowedIPs = {client_ip}/32
# END_PEER {client_name}
"""
    with open(WG_CONF, "a") as f:
        f.write(peer_block)

    psk_fd, psk_path = tempfile.mkstemp(prefix="wg-psk-", suffix=".tmp")
    try:
        os.write(psk_fd, keys["client_psk"].encode())
        os.close(psk_fd)
        os.chmod(psk_path, 0o600)
        subprocess.run(
            ["wg", "set", "wg0", "peer", keys["client_pub"], "preshared-key", psk_path, "allowed-ips", f"{client_ip}/32"],
            check=True,
        )
    finally:
        Path(psk_path).unlink(missing_ok=True)

    client_conf = f"""[Interface]
Address = {client_ip}/24
DNS = 8.8.8.8, 1.1.1.1
PrivateKey = {keys['client_key']}

[Peer]
PublicKey = {server_pub}
PresharedKey = {keys['client_psk']}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {endpoint}:{port}
PersistentKeepalive = 25
"""
    client_path = Path.home() / f"{client_name}.conf"
    client_path.write_text(client_conf)
    os.chmod(client_path, 0o600)

    console.print(f"[success]Клиент {client_name} добавлен![/success]")
    console.print(f"  Конфиг: {client_path}")
    if command_exists("qrencode"):
        with open(client_path, "r") as f:
            subprocess.run(["qrencode", "-t", "ansiutf8", "-m", "1"], stdin=f)

    return 0


def remove() -> int:
    if not os.path.exists(WG_CONF):
        console.print("[warning]WireGuard не установлен[/warning]")
        return 0

    systemctl("disable", "wg-quick@wg0.service")
    systemctl("stop", "wg-quick@wg0.service")
    systemctl("disable", "wg-iptables.service")
    systemctl("stop", "wg-iptables.service")

    run("rm -f /etc/systemd/system/wg-iptables.service")
    run("rm -f /etc/sysctl.d/99-wireguard-forward.conf")
    run("rm -rf /etc/wireguard/")
    systemctl("daemon-reload")

    run("apt-get remove -y wireguard wireguard-tools qrencode 2>/dev/null")

    console.print("[success]WireGuard удалён[/success]")
    return 0
