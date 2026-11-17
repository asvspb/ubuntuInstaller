"""Активный режим туннеля — контракт с ``vpn guard``.

``vpn start``/``vpn start relay``/``vpn down`` взаимоисключающие: одновременно
живёт либо прямой контур (AWG-split ``vpn-awg-split@laptop`` или xray-split
``tun xray0``), либо релейный (``vpn-awg-split@relay``).  Какой именно —
записывается сюда, чтобы ``vpn guard`` мог показать режим, а не угадывать его
по случайнo оставшемуся интерфейсу.

Artefact: ``~/.local/share/vpn-manager/mode.json`` (0600, env-overridable via
``VPN_MANAGER_DATA_DIR``).  Это **не** ``guard/activity.json``: тот файл —
probe-маркер ``{cmd,started,until}`` для FP-guard на время probe/speed/monitor,
и запись режима туда сломала бы ``mark_activity_end``/``activity_active``.

Payload (schema ``vpnm.mode/1``)::

    {"schema": "vpnm.mode/1", "mode": "relay", "proto": "amneziawg",
     "iface": "relay", "unit": "vpn-awg-split@relay.service",
     "target": "185.80.91.135", "updated": "2026-11-17T09:00:00+00:00"}

``mode`` — ``direct`` | ``relay`` | ``none``; ``iface``/``unit`` — что именно
поднято (может отсутствовать для socks-профиля).  Запись best-effort: CLI не
должен падать из-за того, что guard не установлен или диск полон.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA = "vpnm.mode/1"
MODES = ("direct", "relay", "none")

def data_dir() -> Path:
    """``~/.local/share/vpn-manager`` (env-overridable, mirrors guard.reader)."""
    env = os.environ.get("VPN_MANAGER_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "vpn-manager"


def mode_path() -> Path:
    return data_dir() / "mode.json"


def read_mode(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the stored payload, or ``{}`` when missing/broken/wrong schema."""
    target = path if path is not None else mode_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        return {}
    if payload.get("mode") not in MODES:
        return {}
    return payload


def _atomic_write_0600(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_mode(mode: str, *, proto: Optional[str] = None,
               iface: Optional[str] = None, unit: Optional[str] = None,
               target: Optional[str] = None,
               path: Optional[Path] = None,
               now: Optional[float] = None) -> bool:
    """Persist the active mode; never raises (best-effort by contract)."""
    if mode not in MODES:
        return False
    stamp = time.time() if now is None else now
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "mode": mode,
        "updated": datetime.fromtimestamp(
            stamp, tz=timezone.utc).isoformat(),
    }
    for key, value in (("proto", proto), ("iface", iface),
                       ("unit", unit), ("target", target)):
        if isinstance(value, str) and value:
            payload[key] = value
    destination = path if path is not None else mode_path()
    try:
        _atomic_write_0600(destination,
                           json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def clear_mode(path: Optional[Path] = None) -> bool:
    """Record ``mode=none`` (or drop the file when the write fails)."""
    if write_mode("none", path=path):
        return True
    target = path if path is not None else mode_path()
    try:
        target.unlink()
    except OSError:
        return False
    return True


def describe(payload: Dict[str, Any]) -> str:
    """One human line: ``RELAY (amneziawg, relay → 185.80.91.135)``."""
    mode = str(payload.get("mode") or "none")
    if mode == "none":
        return "NONE (туннелей нет)"
    parts = [mode.upper(), "("]
    details = []
    if payload.get("proto"):
        details.append(str(payload["proto"]))
    if payload.get("iface"):
        details.append(str(payload["iface"]))
    line = ", ".join(details)
    target = payload.get("target")
    if target:
        line += f" → {target}" if line else str(target)
    parts.append(line)
    parts.append(")")
    return "".join(parts)
