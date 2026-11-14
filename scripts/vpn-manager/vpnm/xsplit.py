"""System XHTTP split (xray tun → VLESS+XHTTP) — SPEC-20261114-xhttp-split.

Same zone-split mechanics as AWG (dnsmasq → nft set → fwmark → table → dev),
but ``dev = xray0`` — the tun inbound of an xray client built from an
``xray-xhttp`` profile. Root work goes through ``scripts/17_xray-split.sh``
(unit ``vpn-xray-split@<profile>``, pinned xray 26.1.23 binary, domain split
via ``15_domain-split.sh --dev xray0 --mark 0x23000 --table 23000
--nft-table xray_split``).

AWG-split and XHTTP-split are mutually exclusive: ``apply`` stops the AWG
scheme first, ``amneziawg``-side switching stops this one (cli handles both
directions). Kill-switch: the unit's ExecStopPost removes ip rule/table, so
a crash never blackholes the laptop.

Root interaction pattern mirrors ``amneziawg.tunnel_control``: ``sudo -n``;
without passwordless sudo a manual command hint is returned, never raised.
The xray config (contains the UUID) is written to a user-owned 0600 temp
file and moved into /etc/vpn-manager with ``sudo install`` — the secret
never appears in argv or logs.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import profiles
from .links import ProfileLink, parse_link, PROTOCOL_XRAY_XHTTP
from .runners import RunCmd, run_cmd
from .xclient import xray_tun_config

XRAY_SPLIT_UNIT_PREFIX = "vpn-xray-split@"
CONF_DIR = "/etc/vpn-manager"
CONF_PREFIX = "xray-split-"
XRAY_SPLIT_SCRIPT_ENV = "VPNM_XRAY_SPLIT_SCRIPT"
DEV = "xray0"
MARK_HEX = "0x23000"
ROUTE_TABLE = "23000"
NFT_TABLE = "xray_split"


class XSplitError(Exception):
    """Raised on invalid profile/config operations (no sudo hint duplication)."""


def _scripts_dir() -> Path:
    """Каталог скриптов ubuntuInstaller: env или дефолт репозитория."""
    env = os.environ.get("VPNM_SCRIPTS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Dev" / "ubuntuInstaller" / "scripts"


def xray_split_script() -> Optional[Path]:
    """Путь к 17_xray-split.sh (env или дефолт репозитория), None если нет."""
    env = os.environ.get(XRAY_SPLIT_SCRIPT_ENV)
    if env:
        path = Path(env).expanduser()
        return path if path.exists() else None
    default = _scripts_dir() / "17_xray-split.sh"
    return default if default.exists() else None


def resolve_profile(name: Optional[str] = None,
                    path: Optional[Path] = None) -> str:
    """Имя xray-xhttp профиля: явное (с валидацией) или первый из store.

    Raises XSplitError, если профиля нет/протокол не xray-xhttp.
    """
    store = profiles.list_profiles(path)
    if name:
        record = profiles.get(name, path)
        if str(record.get("protocol")) != PROTOCOL_XRAY_XHTTP:
            raise XSplitError(
                f"профиль '{name}' — протокол {record.get('protocol')}, "
                f"xsplit строится только от {PROTOCOL_XRAY_XHTTP} "
                "(ссылка вида vless://…#network=xhttp)")
        return name
    candidates = [str(p.get("name")) for p in store
                  if str(p.get("protocol")) == PROTOCOL_XRAY_XHTTP]
    if not candidates:
        raise XSplitError(
            "xray-xhttp профиля нет — импортируйте XHTTP-ссылку "
            "('vpn profile pull -H <host>' или 'vpn profile import')")
    return candidates[0]


def _link(profile_name: str, path: Optional[Path] = None) -> ProfileLink:
    record = profiles.get(profile_name, path)
    return parse_link(str(record.get("link")))


def _sudo_ok(runner: RunCmd) -> bool:
    rc, _out, _err = runner(["sudo", "-n", "true"])
    return rc == 0


def _manual_hint(script: Path, args: str = "") -> str:
    return f"sudo bash {script} {args}".strip()


def _write_config_via_sudo(profile_name: str, link: ProfileLink,
                           runner: RunCmd) -> Tuple[bool, str]:
    """Сгенерировать tun-конфиг и положить в /etc/vpn-manager через sudo.

    Секрет (UUID) живёт в user-owned tmp-файле 0600 и argv не попадает.
    """
    payload = xray_tun_config(link, tun_name=DEV)
    dst = f"{CONF_DIR}/{CONF_PREFIX}{profile_name}.json"
    # /etc/vpn-manager может отсутствовать на свежей машине (17 читает, но
    # не создаёт каталог): создаём до install, иначе install падает ENOENT.
    rc_dir, _o, err_dir = runner(
        ["sudo", "-n", "install", "-d", "-m", "0700", CONF_DIR])
    if rc_dir != 0:
        detail = (err_dir or "").strip().splitlines()
        return False, (f"каталог {CONF_DIR} не создан "
                       f"({detail[-1] if detail else f'exit {rc_dir}'}); "
                       f"вручную: sudo install -d -m 0700 {CONF_DIR}")
    fd, tmp_name = tempfile.mkstemp(prefix=f"xray-split-{profile_name}.",
                                    suffix=".json.tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        rc, _out, err = runner(
            ["sudo", "-n", "install", "-m", "0600", tmp_name, dst])
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
    if rc != 0:
        detail = (err or "").strip().splitlines()
        return False, (f"конфиг не записан в {dst} "
                       f"({detail[-1] if detail else f'exit {rc}'}); "
                       f"вручную: sudo install -m 0600 <config> {dst}")
    return True, dst


def _ensure_doh(runner: RunCmd) -> Tuple[bool, str]:
    """DoH-цепочка обязательна: dnsmasq смотрит в 127.0.0.1:5053.

    Без неё split поднимается, но резолвинг не-split доменов уходит в мёртвый
    upstream (найдено на ноутбуке 2026-11-14: curl example.com timeout при
    рабочем ya.ru). Идемпотентно, как `vpn doh on`.
    """
    from . import doh as doh_mod
    return doh_mod.enable(runner)


def _stop_awg_scheme(runner: RunCmd) -> Tuple[bool, str]:
    """Mutual exclusion: снять AWG-схему (туннель + split), если активна."""
    from . import amneziawg as awg
    # list-units, а не is-active с glob: systemctl не раскрывает '*' в
    # аргументе-списке (runner без shell), из-за чего активная AWG-схема
    # не детектилась и xsplit вставал поверх AWG (два tun/mark).
    if not awg.local_instance(runner):
        return True, "AWG-схема не активна"
    ok_t, msg_t = awg.tunnel_control("off", run=runner)
    if not ok_t:
        return False, f"AWG-туннель не остановлен ({msg_t}) — xsplit не применён"
    ok_s, msg_s = awg.domain_split_apply("off", run=runner)
    if not ok_s:
        # не фатально: split механика будет пересоздана под xray, но предупредить
        return True, f"AWG-split снят с предупреждением: {msg_s}"
    return True, "AWG-схема снята (туннель + split)"


def apply(profile: Optional[str] = None,
          run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """Поднять системный XHTTP-split. Возвращает (ok, сообщение).

    Порядок: sudo-проба → профиль → конфиг через sudo → стоп AWG-схемы →
    17_xray-split.sh --profile (юнит+бинарь+split). Никогда не бросает.
    """
    runner: RunCmd = run or run_cmd
    script = xray_split_script()
    if script is None:
        return False, (f"17_xray-split.sh не найден (env {XRAY_SPLIT_SCRIPT_ENV})")
    if not _sudo_ok(runner):
        return False, ("нет passwordless sudo — выполните вручную: "
                       + _manual_hint(script, "--profile <профиль>"))
    try:
        name = resolve_profile(profile)
    except XSplitError as exc:
        return False, str(exc)
    try:
        link = _link(name)
    except profiles.ProfileStoreError as exc:
        return False, str(exc)
    ok, msg = _write_config_via_sudo(name, link, runner)
    if not ok:
        return False, msg
    ok_awg, msg_awg = _stop_awg_scheme(runner)
    if not ok_awg:
        return False, msg_awg
    # DoH включаем ПОСЛЕ снятия AWG: 15_domain-split.sh --off (внутри
    # _stop_awg_scheme) гасит dnsmasq/DoH, и включение до него терялось.
    ok_doh, msg_doh = _ensure_doh(runner)
    if not ok_doh:
        return False, f"DoH-цепочка не включена ({msg_doh}) — xsplit без неё ломает DNS"
    rc, out, err = runner(
        ["sudo", "-n", "bash", str(script), "--profile", name])
    if rc == 0:
        detail = (out or "").strip().splitlines()
        tail = detail[-1] if detail else "применён"
        parts = [f"xray-split применён (профиль '{name}', unit "
                 f"{XRAY_SPLIT_UNIT_PREFIX}{name})"]
        if msg_awg != "AWG-схема не активна":
            parts.append(msg_awg)
        return True, "; ".join(parts)
    detail = (err or out or "").strip().splitlines()
    return False, (f"xray-split не применён "
                   f"({detail[-1] if detail else f'exit {rc}'}); вручную: "
                   + _manual_hint(script, f"--profile {name}"))


def stop(run: Optional[RunCmd] = None) -> Tuple[bool, str]:
    """Демонтировать xray-split (unit + правила). Никогда не бросает."""
    runner: RunCmd = run or run_cmd
    script = xray_split_script()
    if script is None:
        return False, (f"17_xray-split.sh не найден (env {XRAY_SPLIT_SCRIPT_ENV})")
    if not _sudo_ok(runner):
        return False, ("нет passwordless sudo — выполните вручную: "
                       + _manual_hint(script, "--off"))
    rc, out, err = runner(["sudo", "-n", "bash", str(script), "--off"])
    if rc == 0:
        return True, "xray-split демонтирован (unit + правила сняты)"
    detail = (err or out or "").strip().splitlines()
    return False, (f"xray-split не демонтирован "
                   f"({detail[-1] if detail else f'exit {rc}'}); вручную: "
                   + _manual_hint(script, "--off"))


def unit_name(profile: str) -> str:
    return f"{XRAY_SPLIT_UNIT_PREFIX}{profile}.service"


def active_profile(runner: RunCmd) -> Optional[str]:
    """Имя профиля активного юнита vpn-xray-split@<p>, или None."""
    rc, out, _err = runner(
        ["systemctl", "list-units", f"{XRAY_SPLIT_UNIT_PREFIX}*", "--no-legend"])
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3] == "running":
            unit = parts[0]
            return unit[len(XRAY_SPLIT_UNIT_PREFIX):-len(".service")]
    return None


def status(run: Optional[RunCmd] = None) -> Dict[str, Any]:
    """Снимок xray-split для канала монитора: unit/iface/nft/rule/проба."""
    runner: RunCmd = run or run_cmd
    conf_dir = Path(CONF_DIR)
    confs = list(conf_dir.glob(f"{CONF_PREFIX}*.json"))
    out: Dict[str, Any] = {
        "profile": active_profile(runner),
        "configs": [c.name for c in confs],
        "iface_up": False,
        "nft_table": False,
        "ip_rule": False,
        "probe_ok": None,
    }
    rc, out_txt, _err = runner(["ip", "link", "show", DEV])
    out["iface_up"] = rc == 0
    # nft требует root: без sudo -n правило считалось бы отсутствующим
    rc, out_txt, _err = runner(
        ["sudo", "-n", "nft", "list", "table", f"inet {NFT_TABLE}"])
    out["nft_table"] = rc == 0 and "Error" not in out_txt
    rc, out_txt, _err = runner(["ip", "rule", "show"])
    out["ip_rule"] = rc == 0 and f"fwmark {MARK_HEX}" in out_txt
    return out
