import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from src.core.shell import run, command_exists
from src.core.logger import console

logger = logging.getLogger(__name__)


def get_free_bytes() -> int:
    result = run("df -B1 --output=avail / 2>/dev/null | tail -1 | tr -d ' '")
    return int(result.output) if result.ok and result.output.isdigit() else 0


def human_size(bytes_val: int) -> str:
    result = run(f"echo {bytes_val} | numfmt --to=iec --suffix=B 2>/dev/null")
    return result.output if result.ok else f"{bytes_val} B"


def apt_maintenance(dry_run: bool = False) -> None:
    console.print("[info]APT maintenance...[/info]")
    if dry_run:
        console.print("  [dim]DRY-RUN: apt clean, autoclean, autoremove --purge[/dim]")
        return
    run("apt clean")
    run("apt autoclean")
    run("apt autoremove --purge -y")
    if command_exists("deborphan"):
        orphans_result = run("deborphan 2>/dev/null")
        if orphans_result.ok and orphans_result.output:
            orphans = orphans_result.output.split()
            if orphans:
                subprocess.run(["apt", "purge", "-y"] + orphans, check=False)
    console.print("[success]APT cleaned[/success]")


def clean_old_kernels(dry_run: bool = False) -> None:
    console.print("[info]Cleaning old kernels...[/info]")
    current = run("uname -r | sed 's/-[a-z]*$//'").output
    if dry_run:
        console.print(f"  [dim]DRY-RUN: remove old kernels (current: {current})[/dim]")
        return
    result = run("dpkg -l 'linux-image-*' 2>/dev/null | grep '^ii' | awk '{print $2}'")
    if not result.ok:
        return
    for pkg in result.output.splitlines():
        pkg = pkg.strip()
        if not pkg or "generic" in pkg or "virtual" in pkg:
            continue
        ver = pkg.replace("linux-image-", "").replace("-generic", "")
        if ver != current:
            run(f"apt purge -y {pkg}")
    run("apt autoremove --purge -y")
    console.print("[success]Old kernels cleaned[/success]")


def vacuum_journal(vacuum_size: str = "200M", dry_run: bool = False) -> None:
    console.print("[info]Vacuuming journal...[/info]")
    if dry_run:
        console.print(f"  [dim]DRY-RUN: journalctl --vacuum-size={vacuum_size}[/dim]")
        return
    run(f"journalctl --vacuum-size={vacuum_size}")
    console.print(f"[success]Journal vacuumed to {vacuum_size}[/success]")


def clean_logs(
    gz_days: int = 7,
    log_days: int = 30,
    dry_run: bool = False,
) -> None:
    console.print("[info]Cleaning old logs...[/info]")
    if dry_run:
        console.print(f"  [dim]DRY-RUN: remove .gz >{gz_days}d, .log >{log_days}d[/dim]")
        return
    run(f"find /var/log -type f -name '*.gz' -mtime +{gz_days} -delete 2>/dev/null")
    run(f"find /var/log -maxdepth 1 -type f -name '*.log' -mtime +{log_days} -delete 2>/dev/null")
    run("find /var/log -type f \\( -name '*.1' -o -name '*.old' -o -name '*.2.gz' -o -name '*.3.gz' \\) -mtime +30 -delete 2>/dev/null")
    console.print("[success]Logs cleaned[/success]")


def clean_tmp(tmp_days: int = 7, dry_run: bool = False) -> None:
    console.print("[info]Cleaning /tmp and /var/tmp...[/info]")
    if dry_run:
        console.print(f"  [dim]DRY-RUN: remove files >{tmp_days}d[/dim]")
        return
    run(f"find /tmp -xdev -type f -mtime +{tmp_days} -delete 2>/dev/null")
    run(f"find /var/tmp -xdev -type f -mtime +{tmp_days} -delete 2>/dev/null")
    console.print("[success]Temp files cleaned[/success]")


def docker_prune(safe: bool = True, dry_run: bool = False) -> None:
    if not command_exists("docker"):
        return
    console.print("[info]Docker prune...[/info]")
    if dry_run:
        console.print("  [dim]DRY-RUN: docker container/image/builder prune[/dim]")
        return
    if safe:
        run("docker container prune -f --filter 'until=48h'")
        run("docker image prune -f")
        run("docker builder prune -af --filter 'until=168h'")
    else:
        run("docker system prune -af")
    console.print("[success]Docker pruned[/success]")


def pip_cache_purge(dry_run: bool = False) -> None:
    if not command_exists("pip3"):
        return
    console.print("[info]Pip cache purge...[/info]")
    if dry_run:
        console.print("  [dim]DRY-RUN: pip3 cache purge[/dim]")
        return
    run("pip3 cache purge 2>/dev/null")
    console.print("[success]Pip cache purged[/success]")


def clean_var_cache(dry_run: bool = False) -> None:
    console.print("[info]Cleaning /var/cache...[/info]")
    if dry_run:
        console.print("  [dim]DRY-RUN: clean apt archives[/dim]")
        return
    run("find /var/cache/apt/archives -type f -name '*.deb' ! -path '*/partial/*' -delete 2>/dev/null")
    console.print("[success]/var/cache cleaned[/success]")


def clean_var_crash(dry_run: bool = False) -> None:
    console.print("[info]Cleaning /var/crash...[/info]")
    if dry_run:
        console.print("  [dim]DRY-RUN: rm /var/crash/*[/dim]")
        return
    run("rm -f /var/crash/* 2>/dev/null")
    console.print("[success]/var/crash cleaned[/success]")


def clean_var_mail(dry_run: bool = False) -> None:
    console.print("[info]Cleaning /var/mail...[/info]")
    if dry_run:
        console.print("  [dim]DRY-RUN: truncate /var/mail/*[/dim]")
        return
    run("for f in /var/mail/*; do [ -f \"$f\" ] && truncate -s 0 \"$f\" 2>/dev/null; done")
    console.print("[success]/var/mail cleaned[/success]")


def run_cleanup(
    dry_run: bool = False,
    safe_docker: bool = True,
) -> int:
    console.print("\n[info]═══ System Cleanup ═══[/info]\n")

    before = get_free_bytes()
    console.print(f"[info]Free before: {human_size(before)}[/info]")
    if dry_run:
        console.print("[warning]DRY RUN mode — no changes will be made[/warning]\n")

    apt_maintenance(dry_run)
    clean_old_kernels(dry_run)
    vacuum_journal(dry_run=dry_run)
    clean_logs(dry_run=dry_run)
    clean_tmp(dry_run=dry_run)
    docker_prune(safe=safe_docker, dry_run=dry_run)
    pip_cache_purge(dry_run)
    clean_var_cache(dry_run)
    clean_var_crash(dry_run)
    clean_var_mail(dry_run)

    after = get_free_bytes()
    delta = after - before
    console.print(f"\n[success]Free after: {human_size(after)} (Δ={human_size(delta)})[/success]")

    disk = run("df -h / 2>/dev/null")
    if disk.ok:
        console.print(f"\n{disk.output}")

    return 0
