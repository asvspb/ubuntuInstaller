"""DoH DNS-режим (clean-boot policy, per-session).

Включает локальную цепочку  resolved → dnsmasq (127.0.0.1:53) →
cloudflared (127.0.0.1:5053) → Cloudflare DoH (HTTPS:443):

- ``dnsmasq`` и ``cloudflared-proxy-dns`` — system-сервисы (sudo);
- направление resolved на 127.0.0.1 — через drop-in
  ``/etc/systemd/resolved.conf.d/doh-local.conf`` (sudo mv);
- откат одним ``mv`` обратно в ``*.disabled-manualvpn`` — DNS мгновенно
  возвращается к провайдеру (DHCP), затем сервисы останавливаются.

По политике чистого старта (2026-11-11) режим НЕ включается при загрузке:
autostart обоих сервисов отключён, drop-in лежит в disabled-имени. Всё
включение/выключение — только вручную на сессию, через ``vpn doh
on|off|status``.

Все subprocess-взаимодействия идут через injectable ``run`` (rc, out, err);
systemctl/mv требуют root, поэтому идут через ``sudo -n`` и при недоступном
passwordless sudo возвращают печатную ручную подсказку.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from .runners import RunCmd, run_cmd

SERVICES = ("dnsmasq", "cloudflared-proxy-dns")
RESOLVED_DIR = Path("/etc/systemd/resolved.conf.d")
DROPIN = RESOLVED_DIR / "doh-local.conf"
DROPIN_DISABLED = RESOLVED_DIR / "doh-local.conf.disabled-manualvpn"
LOCAL_RESOLVER = "127.0.0.1"

# Boot-guard: one-shot systemd unit that runs at boot and returns the
# drop-in to its disabled name. Reason: 'vpn doh on' moves the drop-in
# FILE, and the file survives a reboot while the services do not
# (clean-boot policy) -> at boot resolved points to a dead 127.0.0.1 and
# DNS is dead until the first 'vpn repair'. The guard makes boot DNS
# always provider-based (DHCP); 'vpn doh on' re-activates per session.
GUARD_UNIT = Path("/etc/systemd/system/vpn-doh-boot-guard.service")


def _guard_unit_content() -> str:
    return f"""[Unit]
Description=vpn-manager DoH boot guard: provider DNS at boot (drop-in must not outlive its services)
After=systemd-resolved.service
ConditionPathExists={DROPIN}

[Service]
Type=oneshot
ExecStart=/usr/bin/mv {DROPIN} {DROPIN_DISABLED}
ExecStartPost=/usr/bin/systemctl try-restart systemd-resolved

[Install]
WantedBy=multi-user.target
"""


def guard_state(run: Optional[RunCmd] = None) -> str:
    """'on' (юнит включён в загрузку), 'off' (не включён) или 'missing'."""
    runner: RunCmd = run or run_cmd
    if not GUARD_UNIT.exists():
        return "missing"
    rc, out, _err = runner(["systemctl", "is-enabled", GUARD_UNIT.name])
    return "on" if rc == 0 and out.strip() == "enabled" else "off"


def guard_install(run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """Установить и включить boot-guard (при загрузке DNS = провайдер).

    Идемпотентно: повторный запуск перезаписывает юнит свежим контентом.
    Никогда не бросает исключений — ошибки возвращаются строкой.
    """
    runner: RunCmd = run or run_cmd
    tmp = Path("/tmp/vpn-doh-boot-guard.service.tmp")
    try:
        tmp.write_text(_guard_unit_content(), encoding="utf-8")
    except OSError as exc:
        return False, f"не удалось записать временный файл {tmp}: {exc}"
    rc, _out, err = runner(["sudo", "-n", "cp", str(tmp), str(GUARD_UNIT)])
    tmp.unlink(missing_ok=True)
    if rc != 0:
        detail = (err or "").strip().splitlines()
        hint = (f"вручную: sudo tee {GUARD_UNIT} <<'EOF'\n"
                f"{_guard_unit_content()}EOF")
        return False, ("не удалось установить boot-guard "
                       f"({detail[-1] if detail else f'exit {rc}'}); {hint}")
    runner(["sudo", "-n", "chmod", "0644", str(GUARD_UNIT)])
    rc, _out, err = runner(
        ["sudo", "-n", "systemctl", "enable", GUARD_UNIT.name])
    if rc != 0:
        detail = (err or "").strip().splitlines()
        return False, ("юнит установлен, но enable не прошёл; вручную: "
                       f"sudo systemctl enable {GUARD_UNIT.name}"
                       + (f" ({detail[-1]})" if detail else ""))
    runner(["sudo", "-n", "systemctl", "daemon-reload"])
    return True, (f"boot-guard включён: при загрузке {DROPIN.name} "
                  "возвращается в disabled-имя — DNS у провайдера, "
                  "мёртвая DoH-цепочка при старте больше невозможна; "
                  "'vpn doh on' продолжает работать на сессию")


def guard_remove(run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """Выключить и удалить boot-guard (не рекомендуется)."""
    runner: RunCmd = run or run_cmd
    if not GUARD_UNIT.exists():
        return True, "boot-guard не установлен — удалять нечего"
    runner(["sudo", "-n", "systemctl", "disable", GUARD_UNIT.name])
    rc, _out, err = runner(["sudo", "-n", "rm", str(GUARD_UNIT)])
    if rc != 0:
        detail = (err or "").strip().splitlines()
        return False, (f"не удалось удалить {GUARD_UNIT}; вручную: "
                       f"sudo systemctl disable {GUARD_UNIT.name} && "
                       f"sudo rm {GUARD_UNIT}"
                       + (f" ({detail[-1]})" if detail else ""))
    runner(["sudo", "-n", "systemctl", "daemon-reload"])
    return True, "boot-guard удалён: загрузка больше не защищает DNS"


def guard_hint(run: Optional[RunCmd] = None) -> str:
    """Строка про boot-guard для status_lines."""
    state = guard_state(run)
    if state == "on":
        return ("boot-guard: включён — при загрузке DNS гарантированно "
                "у провайдера")
    if state == "off":
        return ("boot-guard: юнит есть, но НЕ включён в загрузку — "
                "'vpn doh guard on' (риск мёртвого DNS при ребуте "
                "после doh on)")
    return ("boot-guard: не установлен — при ребуте после 'vpn doh on' "
            "DNS будет мёртв; установите: 'vpn doh guard on'")



def dropin_state() -> str:
    """'on' (drop-in активен), 'off' (disabled-имя) или 'missing'."""
    if DROPIN.exists():
        return "on"
    if DROPIN_DISABLED.exists():
        return "off"
    return "missing"


def services_state(run: Optional[RunCmd] = None) -> List[Tuple[str, str]]:
    """[(service, is-active output)] для обоих сервисов цепочки."""
    runner: RunCmd = run or run_cmd
    states: List[Tuple[str, str]] = []
    for svc in SERVICES:
        _rc, out, _err = runner(["systemctl", "is-active", svc])
        states.append((svc, out.strip() or "unknown"))
    return states


def _manual_hint(action: str) -> str:
    """Полная ручная последовательность для копирования из вывода."""
    if action == "on":
        return (f"sudo systemctl start {' '.join(SERVICES)} && "
                f"sudo mv {DROPIN_DISABLED} {DROPIN} && "
                "sudo systemctl restart systemd-resolved")
    return (f"sudo mv {DROPIN} {DROPIN_DISABLED} && "
            "sudo systemctl restart systemd-resolved && "
            f"sudo systemctl stop {' '.join(SERVICES)}")


def actual_server(run: Optional[RunCmd] = None) -> Optional[str]:
    """Текущий DNS-сервер resolved (из 'resolvectl', строка Current DNS)."""
    runner: RunCmd = run or run_cmd
    rc, out, _err = runner(["resolvectl"])
    if rc != 0:
        return None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Current DNS Server:"):
            return stripped.split(":", 1)[1].strip() or None
    return None


def enable(run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """Включить DoH-режим на сессию.

    Идемпотентно: если drop-in уже активен, только убеждается, что сервисы
    запущены. Никогда не бросает исключений — ошибки возвращаются строкой.
    """
    runner: RunCmd = run or run_cmd
    if dropin_state() == "missing":
        return False, (f"drop-in не найден ни как {DROPIN}, ни как "
                       f"{DROPIN_DISABLED}; восстановите его из бэкапа или "
                       "создайте: [Resolve] DNS=127.0.0.1, Domains=~.")
    rc, _out, err = runner(
        ["sudo", "-n", "systemctl", "start", *SERVICES])
    if rc != 0:
        detail = (err or "").strip().splitlines()
        return False, (f"не удалось запустить {' '.join(SERVICES)} "
                       f"({detail[-1] if detail else f'exit {rc}'}); "
                       f"вручную: {_manual_hint('on')}")
    if dropin_state() != "on":
        rc, _out, err = runner(
            ["sudo", "-n", "mv", str(DROPIN_DISABLED), str(DROPIN)])
        if rc != 0:
            detail = (err or "").strip().splitlines()
            return False, (f"сервисы запущены, но drop-in не активирован "
                           f"({detail[-1] if detail else f'exit {rc}'}); "
                           f"вручную: {_manual_hint('on')}")
    rc, _out, err = runner(
        ["sudo", "-n", "systemctl", "restart", "systemd-resolved"])
    if rc != 0:
        detail = (err or "").strip().splitlines()
        return False, ("DoH включён, но systemd-resolved не перезапущен — "
                       "резолвинг может ещё идти по-старому "
                       f"({detail[-1] if detail else f'exit {rc}'}); "
                       "вручную: sudo systemctl restart systemd-resolved")
    chosen = actual_server(run)
    message = (f"DoH-режим включён: resolved → {LOCAL_RESOLVER} "
               "(dnsmasq → cloudflared → Cloudflare DoH). "
               "Сессия — до выключения/ребута или 'vpn doh off'")
    if chosen and chosen != LOCAL_RESOLVER:
        message += (f"\nВНИМАНИЕ: resolved фактически использует "
                    f"{chosen}, а не {LOCAL_RESOLVER} — статическая строка "
                    "DNS= в /etc/systemd/resolved.conf отвечает раньше "
                    "цепочки DoH. Закомментируйте её, чтобы DoH реально "
                    "использовался, иначе режим только номинальный.")
    return True, message


def disable(run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """Выключить DoH-режим и вернуть DNS провайдера (DHCP).

    Идемпотентно: без drop-in только останавливает сервисы, если запущены.
    """
    runner: RunCmd = run or run_cmd
    if dropin_state() == "on":
        rc, _out, err = runner(
            ["sudo", "-n", "mv", str(DROPIN), str(DROPIN_DISABLED)])
        if rc != 0:
            detail = (err or "").strip().splitlines()
            return False, (f"не удалось откатить drop-in "
                           f"({detail[-1] if detail else f'exit {rc}'}); "
                           f"вручную: {_manual_hint('off')}")
        runner(["sudo", "-n", "systemctl", "restart", "systemd-resolved"])
    elif dropin_state() == "missing":
        return True, ("drop-in отсутствует — DoH-режим уже выключен, "
                      "DNS идёт к провайдеру")
    running = [svc for svc, state in services_state(run)
               if state == "active"]
    if running:
        rc, _out, err = runner(
            ["sudo", "-n", "systemctl", "stop", *running])
        if rc != 0:
            detail = (err or "").strip().splitlines()
            return False, (f"drop-in откачен (DNS уже у провайдера), но "
                           f"сервисы {' '.join(running)} не остановлены "
                           f"({detail[-1] if detail else f'exit {rc}'}); "
                           "вручную: sudo systemctl stop "
                           f"{' '.join(running)}")
    return True, ("DoH-режим выключен: DNS снова у провайдера (DHCP), "
                  f"сервисы {' '.join(SERVICES)} остановлены")


def status_lines(run: Optional[RunCmd] = None) -> List[str]:
    """Human-readable статус: drop-in, сервисы, активный сервер resolved."""
    runner: RunCmd = run or run_cmd
    lines: List[str] = []
    state = dropin_state()
    if state == "on":
        lines.append(f"drop-in: активен ({DROPIN.name}) — resolved "
                     f"направлен на {LOCAL_RESOLVER}")
    elif state == "off":
        lines.append(f"drop-in: отключён ({DROPIN_DISABLED.name}) — "
                     "resolved использует DNS провайдера (DHCP)")
    else:
        lines.append(f"drop-in: не найден ({DROPIN}) — конфигурация "
                     "DoH-режима отсутствует")
    for svc, svc_state in services_state(run):
        lines.append(f"{svc}: {svc_state}")
    rc, out, _err = runner(["resolvectl"])
    if rc == 0:
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("Current DNS Server:"):
                lines.append(f"фактически: {stripped}")
                break
    else:
        lines.append("фактический сервер: не удалось прочитать "
                     "(resolvectl недоступен)")
    lines.append(guard_hint(run))
    return lines
