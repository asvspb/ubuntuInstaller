import platform
import socket
import time
import requests
from typing import Optional

import psutil
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.core.shell import run

console = Console()


def get_public_ip() -> str:
    try:
        resp = requests.get("https://ifconfig.me", timeout=3)
        if resp.status_code == 200:
            return resp.text.strip()
    except requests.RequestException:
        pass
    return "N/A"


def get_docker_status() -> tuple[int, int]:
    running = run("docker ps -q 2>/dev/null | wc -l")
    total = run("docker ps -aq 2>/dev/null | wc -l")
    r = int(running.output) if running.ok and running.output.isdigit() else 0
    t = int(total.output) if total.ok and total.output.isdigit() else 0
    return r, t


def make_bar(percent: float, width: int = 20) -> Text:
    filled = int(percent * width / 100)
    empty = width - filled
    text = Text()
    if percent >= 90:
        color = "red"
    elif percent >= 70:
        color = "yellow"
    else:
        color = "green"
    text.append("█" * filled, style=color)
    text.append("░" * empty, style="dim")
    return text


def get_uptime_str() -> str:
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    mins = int((uptime_seconds % 3600) // 60)
    if days > 0:
        return f"{days} days, {hours} hours, {mins} mins"
    elif hours > 0:
        return f"{hours} hours, {mins} mins"
    else:
        return f"{mins} mins"


def get_local_ip() -> str:
    local_ip = ""
    net_if_addrs = psutil.net_if_addrs()
    for iface, addrs in net_if_addrs.items():
        if iface != "lo" and not iface.startswith("docker") and not iface.startswith("br-") and not iface.startswith("veth") and not iface.startswith("zt"):
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    local_ip = addr.address
                    break
        if local_ip:
            break
    return local_ip


def show_dashboard() -> None:
    hostname = platform.node()
    kernel = platform.release()
    arch = platform.machine()

    try:
        with open("/etc/os-release") as f:
            os_info = {}
            for line in f:
                if "=" in line:
                    k, _, v = line.strip().partition("=")
                    os_info[k] = v.strip('"')
        os_name = os_info.get("PRETTY_NAME", "")
    except FileNotFoundError:
        os_name = ""

    cpu_model = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu_model = line.split(":")[1].strip()
                    break
    except FileNotFoundError:
        pass

    cpu_cores = psutil.cpu_count()
    load_avg = psutil.getloadavg()
    cpu_percent = psutil.cpu_percent(interval=1)

    uptime_str = get_uptime_str()

    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()

    public_ip = get_public_ip()
    local_ip = get_local_ip()

    docker_running, docker_total = get_docker_status()

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]{hostname}[/bold cyan]  [dim]{os_name} | {kernel} ({arch})[/dim]",
        border_style="cyan",
    ))

    console.print(f"  [dim]CPU:[/dim] {cpu_model[:40]} {cpu_cores}cores | "
                  f"[dim]Load:[/dim] {load_avg[0]:.2f} {load_avg[1]:.2f} {load_avg[2]:.2f} | "
                  f"[dim]Uptime:[/dim] {uptime_str}")

    console.print()

    cpu_bar = make_bar(cpu_percent)
    ram_percent = ram.percent
    ram_bar = make_bar(ram_percent)
    ram_used_mb = ram.used // (1024 * 1024)
    ram_total_mb = ram.total // (1024 * 1024)

    console.print(f"  [dim]CPU:[/dim] {cpu_percent:5.1f}% {cpu_bar}")
    console.print(f"  [dim]RAM:[/dim] {ram_used_mb}M/{ram_total_mb}M {ram_bar}")

    if swap.total > 0:
        swap_percent = swap.percent
        swap_bar = make_bar(swap_percent)
        swap_used_mb = swap.used // (1024 * 1024)
        swap_total_mb = swap.total // (1024 * 1024)
        console.print(f"  [dim]SWP:[/dim] {swap_used_mb}M/{swap_total_mb}M {swap_bar}")

    console.print()
    
    table = Table(show_header=True, header_style="bold cyan", border_style="dim", box=None)
    table.add_column("Mount", style="dim")
    table.add_column("Type", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Avail", justify="right")
    table.add_column("Use%", justify="right", style="green")
    table.add_column("Total", justify="right")

    partitions = psutil.disk_partitions(all=False)
    for part in partitions:
        if part.fstype in ("ext4", "xfs", "btrfs", "zfs", "vfat"):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                used_gb = usage.used / (1024**3)
                avail_gb = usage.free / (1024**3)
                total_gb = usage.total / (1024**3)
                
                pct_style = "red" if usage.percent > 90 else "yellow" if usage.percent > 75 else "green"
                
                table.add_row(
                    part.mountpoint,
                    part.fstype,
                    f"{used_gb:.1f}G",
                    f"{avail_gb:.1f}G",
                    f"[{pct_style}]{usage.percent:.1f}%[/{pct_style}]",
                    f"{total_gb:.1f}G"
                )
            except PermissionError:
                continue
                
    console.print(table)

    console.print()
    console.print("  [bold cyan]─── Network & Docker ───────────────────────────────────[/bold cyan]")
    console.print(f"  [dim]Pub IP:[/dim] {public_ip:<15} [dim]Loc IP:[/dim] {local_ip}")
    console.print(f"  [dim]Docker:[/dim] {docker_running}/{docker_total} containers running")
    console.print()
