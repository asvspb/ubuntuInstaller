"""Repair: полная диагностика прокси-стека в лог + возврат трафика без nano.

Мотивация (инцидент 2026-11-11): при «полном блоке» трафика днём оказывалось,
что жив только канал провайдера, а DNS смотрит в мёртвую DoH-цепочку
(resolved → 127.0.0.1, dnsmasq/cloudflared не запущены). Вручную чинить
приходилось редактированием /etc/systemd/resolved.conf через nano. Эта
команда делает то же автоматически:

- ``snapshot_lines()`` — человекочитаемый снимок всего состояния
  (DNS-стек, каналы, порты, профиль) — он же уходит в лог-файл;
- ``fix_dns()`` — возвращает трафик: гасит мёртвую DoH-цепочку и/или
  направляет resolved на живой публичный резолвер через управляемый
  drop-in ``vpn-dns-fix.conf`` (никогда не трогает resolved.conf руками —
  только drop-in в ``resolved.conf.d``);
- ``write_log()`` — пишет снимок в ``~/.local/share/vpn-manager/logs/``.

Политика чистого старта не нарушается: drop-in создаётся/убирается только
командой (``vpn repair`` / ``vpn repair --dns off``), при загрузке ничего
не меняется, сервисы не включаются в autostart.

Все subprocess-взаимодействия идут через injectable ``run`` (rc, out, err),
как в doh.py; действия, требующие root, идут через ``sudo -n`` и при
недоступном passwordless sudo возвращают печатную ручную подсказку.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import doh as doh_mod
from .runners import RunCmd, run_cmd

RESOLVED_DIR = Path(os.environ.get(
    "VPN_MANAGER_RESOLVED_DIR", "/etc/systemd/resolved.conf.d"))
REPAIR_DROPIN = RESOLVED_DIR / "vpn-dns-fix.conf"
LOG_DIR = Path(os.environ.get(
    "VPN_MANAGER_LOG_DIR",
    str(Path.home() / ".local" / "share" / "vpn-manager" / "logs")))
PROXY_PORTS = (2080, 2083, 1080)
SERVICE_NAMES = ("dnsmasq", "cloudflared-proxy-dns",
                 "zapret", "zerotier-one", "systemd-resolved")
RESOLVER_CANDIDATES = ("77.88.8.8", "77.88.8.1", "1.1.1.1", "8.8.8.8")
PROBE_NAME = "ya.ru"
DIG_TIMEOUT = ["+time=3", "+tries=1"]


# --- resolver health ---------------------------------------------------------------

def _dig_ip(ip: str, run: RunCmd) -> Optional[str]:
    """Первый ответ резолвера @ip (строка) или None при таймауте/ошибке."""
    rc, out, _err = run(["dig", f"@{ip}", PROBE_NAME, "+short", *DIG_TIMEOUT])
    text = out.strip().splitlines()
    return text[0].strip() if rc == 0 and text else None


def find_working_resolver(run: RunCmd) -> Optional[str]:
    """Первый живой публичный резолвер из CANDIDATES (dig-проба)."""
    for ip in RESOLVER_CANDIDATES:
        if _dig_ip(ip, run) is not None:
            return ip
    return None


def system_resolve_ok(run: RunCmd) -> bool:
    """True, когда системный resolved (stub 127.0.0.53) резолвит PROBE_NAME."""
    rc, out, _err = run(["dig", "@127.0.0.53", PROBE_NAME,
                         "+short", *DIG_TIMEOUT])
    return rc == 0 and bool(out.strip())


# --- snapshot ---------------------------------------------------------------------

def snapshot_lines(run: RunCmd) -> List[str]:
    """Полный снимок состояния прокси-стека, строка = запись лога."""
    runner: RunCmd = run or run_cmd
    lines: List[str] = []
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    lines.append(f"== vpn repair snapshot {ts} ==")

    lines.append("-- DNS-стек --")
    rc, out, _ = runner(["resolvectl"])
    if rc == 0:
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith(("Current DNS Server:", "DNS Servers:",
                                    "resolv.conf mode:")):
                lines.append(f"resolved: {stripped}")
    else:
        lines.append(f"resolved: resolvectl exit {rc}")
    lines.append(f"doh drop-in: {doh_mod.dropin_state()} "
                 f"(enabled={doh_mod.DROPIN.exists()}, "
                 f"disabled={doh_mod.DROPIN_DISABLED.exists()})")
    lines.append(f"repair drop-in: "
                 f"{'есть' if REPAIR_DROPIN.exists() else 'нет'}")
    for ip in RESOLVER_CANDIDATES:
        answer = _dig_ip(ip, runner)
        lines.append(f"dig @{ip}: {answer if answer else 'FAIL'}")
    lines.append(f"system resolve (stub): "
                 f"{'OK' if system_resolve_ok(runner) else 'FAIL'}")

    lines.append("-- сервисы --")
    for svc in SERVICE_NAMES:
        rc, out, _ = runner(["systemctl", "is-active", svc])
        lines.append(f"{svc}: {out.strip() or 'unknown'}")

    lines.append("-- порты --")
    for port in PROXY_PORTS:
        rc, out, _ = runner(["ss", "-tlnH", f"sport = :{port}"])
        listening = rc == 0 and "LISTEN" in out
        lines.append(f"port {port}: {'LISTEN' if listening else 'closed'}")

    lines.append("-- канал провайдера --")
    rc, out, _ = runner(["curl", "-4", "--max-time", "5", "-s", "-o",
                         "/dev/null", "-w", "%{http_code} %{time_total}s",
                         "https://ya.ru"])
    lines.append(f"direct https ya.ru: {out.strip() if rc == 0 else 'FAIL'}")
    return lines


# --- dns fix ----------------------------------------------------------------------

def _dropin_content(resolver: str) -> str:
    return (f"[Resolve]\n# vpn repair: живой резолвер на голом канале "
            f"(до 'vpn repair --dns off')\nDNS={resolver}\nDomains=~.\n")


def fix_dns(run: RunCmd,
            dry_run: bool = False) -> Tuple[str, List[str]]:
    """Вернуть DNS (и значит трафик) без ручной правки resolved.conf.

    Возвращает (действие, список строк для лога/вывода). Действие: 'ok'
    (DNS уже живой), 'fixed' (изменения применены), 'dry-run' (план без
    применения), 'manual' (нужен root, напечатана ручная подсказка),
    'failed' (изменить не удалось).
    """
    runner: RunCmd = run or run_cmd
    lines: List[str] = []

    if system_resolve_ok(runner):
        lines.append("DNS: системный resolved уже отвечает — правки не нужны")
        return "ok", lines

    lines.append("DNS: системный resolved НЕ отвечает — начинаю восстановление")

    # 1) мёртвая DoH-цепочка — самый частый «полный блок»: гасим её.
    if doh_mod.dropin_state() == "on":
        dnsmasq_alive = any(state == "active" for _svc, state
                            in doh_mod.services_state(runner))
        if not dnsmasq_alive:
            lines.append(f"причина: drop-in {doh_mod.DROPIN.name} активен "
                         "(DNS=127.0.0.1), а цепочка dnsmasq/cloudflared "
                         "не запущена — resolved смотрит в никуда")
            if dry_run:
                lines.append(f"план: vpn doh off (mv {doh_mod.DROPIN} -> "
                             f"{doh_mod.DROPIN_DISABLED}, restart resolved)")
                return "dry-run", lines
            ok, msg = doh_mod.disable(runner)
            lines.append(f"vpn doh off: {msg}")
            if not ok:
                return "failed", lines
            if system_resolve_ok(runner):
                lines.append("DNS восстановлен после отключения DoH-цепочки; "
                             "чтобы мёртвый DNS при загрузке не повторялся, "
                             "поставьте защиту: 'vpn doh guard on'")
                return "fixed", lines

    # 2) направляем resolved на живой публичный резолвер через drop-in.
    resolver = find_working_resolver(runner)
    if resolver is None:
        lines.append("не найден ни один живой публичный резолвер из "
                     f"{', '.join(RESOLVER_CANDIDATES)} — вероятно, реально "
                     "заблокирован канал провайдера; чинить DNS нечем")
        return "failed", lines
    lines.append(f"живой резолвер: {resolver}")

    if dry_run:
        lines.append(f"план: создать {REPAIR_DROPIN} с DNS={resolver}, "
                     "restart systemd-resolved")
        return "dry-run", lines

    tmp = Path("/tmp/vpn-dns-fix.conf.tmp")
    try:
        tmp.write_text(_dropin_content(resolver), encoding="utf-8")
    except OSError as exc:
        lines.append(f"не удалось записать временный файл {tmp}: {exc}")
        return "failed", lines
    rc, _out, err = runner(["sudo", "-n", "cp", str(tmp), str(REPAIR_DROPIN)])
    tmp.unlink(missing_ok=True)
    if rc != 0:
        detail = (err or "").strip().splitlines()
        lines.append("sudo недоступен или копирование не удалось; вручную: "
                     f"sudo tee {REPAIR_DROPIN} <<'EOF'\n"
                     f"{_dropin_content(resolver)}EOF")
        if detail:
            lines.append(f"подробно: {detail[-1]}")
        return "manual", lines
    runner(["sudo", "-n", "chmod", "0644", str(REPAIR_DROPIN)])
    rc, _out, err = runner(
        ["sudo", "-n", "systemctl", "restart", "systemd-resolved"])
    if rc != 0:
        detail = (err or "").strip().splitlines()
        lines.append(f"drop-in создан, но systemd-resolved не перезапущен "
                     f"({detail[-1] if detail else f'exit {rc}'}); "
                     "вручную: sudo systemctl restart systemd-resolved")
        return "failed", lines
    if system_resolve_ok(runner):
        lines.append(f"DNS восстановлен: resolved -> {resolver} через "
                     f"{REPAIR_DROPIN.name} (убрать: vpn repair --dns off)")
        return "fixed", lines
    lines.append("drop-in применён, но stub всё ещё не отвечает — "
                 "проверьте resolvectl status")
    return "failed", lines


def remove_dns_fix(run: RunCmd) -> Tuple[bool, str]:
    """Убрать управляемый drop-in (вернуть DNS провайдера, чистый старт)."""
    runner: RunCmd = run or run_cmd
    if not REPAIR_DROPIN.exists():
        return True, ("drop-in vpn-dns-fix.conf отсутствует — DNS уже "
                      "по-старому (провайдер/DHCP)")
    rc, _out, err = runner(["sudo", "-n", "rm", str(REPAIR_DROPIN)])
    if rc != 0:
        detail = (err or "").strip().splitlines()
        return False, (f"не удалось удалить {REPAIR_DROPIN}; вручную: "
                       f"sudo rm {REPAIR_DROPIN} && sudo systemctl restart "
                       f"systemd-resolved"
                       + (f" ({detail[-1]})" if detail else ""))
    runner(["sudo", "-n", "systemctl", "restart", "systemd-resolved"])
    return True, (f"drop-in {REPAIR_DROPIN.name} удалён, resolved "
                  "перезапущен — DNS снова провайдерский")


# --- log --------------------------------------------------------------------------

def write_log(log_lines: List[str],
              log_dir: Optional[Path] = None) -> Optional[Path]:
    """Записать снимок в лог (0700 dir / 0600 file); None при ошибке."""
    directory = log_dir if log_dir is not None else LOG_DIR
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"repair-{ts}.log"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        return None
    return path


def snapshot_as_json(run: RunCmd) -> str:
    """Машиночитаемая версия снимка (для --json)."""
    runner: RunCmd = run or run_cmd
    data = {
        "system_resolve": system_resolve_ok(runner),
        "doh_dropin": doh_mod.dropin_state(),
        "repair_dropin": REPAIR_DROPIN.exists(),
        "resolvers": {ip: _dig_ip(ip, runner)
                      for ip in RESOLVER_CANDIDATES},
        "services": {svc: (runner(["systemctl", "is-active", svc])[1].strip()
                           or "unknown") for svc in SERVICE_NAMES},
        "ports": {port: bool("LISTEN" in
                             runner(["ss", "-tlnH", f"sport = :{port}"])[1])
                  for port in PROXY_PORTS},
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
