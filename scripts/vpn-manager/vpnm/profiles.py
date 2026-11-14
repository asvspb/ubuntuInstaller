"""Profile store: ~/.config/vpn-manager/profiles.json.

The store holds client profiles (share links + derived metadata). Writes are
atomic (tmp + os.replace) and the file is always chmod 0600 because links
contain private credentials.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .links import (
    PROTOCOL_HYSTERIA2,
    PROTOCOL_XRAY_REALITY,
    PROTOCOL_XRAY_XHTTP,
    PROTOCOL_XRAY_WS,
    LinkError,
    ProfileLink,
    parse_link,
)

CONFIG_DIR = Path(os.environ.get("VPN_MANAGER_CONFIG_DIR",
                                 str(Path.home() / ".config" / "vpn-manager")))
STORE_PATH = CONFIG_DIR / "profiles.json"

DEFAULT_SOCKS_PORT = {
    PROTOCOL_XRAY_REALITY: 2080,
    PROTOCOL_XRAY_XHTTP: 2080,
    PROTOCOL_XRAY_WS: 2081,
    PROTOCOL_HYSTERIA2: 2083,
}


class ProfileStoreError(Exception):
    """Raised on invalid profile operations (duplicate/missing names)."""


def _atomic_write_0600(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        # best-effort cleanup of the temp file; the original error is re-raised
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = path if path is not None else STORE_PATH
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ProfileStoreError(f"cannot read store {path}: {exc}") from exc
    if isinstance(raw, dict):
        profiles = raw.get("profiles", [])
    else:
        profiles = raw
    if not isinstance(profiles, list):
        raise ProfileStoreError(f"store {path}: 'profiles' must be a list")
    return profiles


def _save(profiles: List[Dict[str, Any]], path: Optional[Path] = None) -> None:
    _atomic_write_0600(path if path is not None else STORE_PATH, json.dumps({"profiles": profiles}, indent=2,
                                        ensure_ascii=False) + "\n")


def _find(profiles: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    for p in profiles:
        if p.get("name") == name:
            return p
    raise ProfileStoreError(f"profile '{name}' not found")


def add(link: ProfileLink, raw_link: str, name: str | None = None,
        socks_port: int | None = None, path: Optional[Path] = None) -> Dict[str, Any]:
    """Add a profile parsed from a share link. Name must be unique.

    ``raw_link`` is the original link text as given by the user; the store
    keeps it verbatim so re-export stays lossless.
    """
    profiles = _load(path)
    profile_name = name or link.name
    if not profile_name:
        raise ProfileStoreError("profile name is empty; pass a name or a link fragment")
    if any(p.get("name") == profile_name for p in profiles):
        raise ProfileStoreError(f"profile '{profile_name}' already exists")
    port = socks_port or DEFAULT_SOCKS_PORT[link.protocol]
    record: Dict[str, Any] = {
        "name": profile_name,
        "protocol": link.protocol,
        "link": raw_link,
        "socks_port": int(port),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    profiles.append(record)
    _save(profiles, path)
    return record


def remove(name: str, path: Optional[Path] = None) -> Dict[str, Any]:
    """Remove a profile by name; returns the removed record."""
    profiles = _load(path)
    record = _find(profiles, name)
    profiles = [p for p in profiles if p is not record]
    _save(profiles, path)
    return record


def list_profiles(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    return _load(path)


def get(name: str, path: Optional[Path] = None) -> Dict[str, Any]:
    return _find(_load(path), name)


def resolve_cdn(path: Optional[Path] = None) -> Optional[str]:
    """Имя CDN-профиля (protocol ``xray-cdn-ws``) или ``None``.

    Имя CDN-профиля не должно быть захардкожено: сервер (vds) рендерит
    CDN-ссылки как ``<name>@<front>: vless://...`` и имя задаёт пользователь.
    Приоритет: ``cdn-home`` (историческое имя), затем любое имя со ``cdn``,
    затем первый ws-профиль в порядке store.
    """
    ws = [p for p in _load(path) if p.get("protocol") == PROTOCOL_XRAY_WS]
    for profile in ws:
        if profile.get("name") == "cdn-home":
            return "cdn-home"
    for profile in ws:
        if "cdn" in str(profile.get("name", "")).lower():
            return str(profile.get("name"))
    return str(ws[0].get("name")) if ws else None


# --- сохранение/передача профилей ------------------------------------------

BUNDLE_FORMAT = "vpnm-profiles"
BUNDLE_VERSION = 1


def default_bundle_path() -> Path:
    """Путь по умолчанию: ~/vpn-profiles-YYYYMMDD-HHMMSS.json."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.home() / f"vpn-profiles-{stamp}.json"


def export_bundle(path: Optional[Path] = None) -> Dict[str, Any]:
    """Payload для бэкапа/переноса: все профили (ссылки = секреты)."""
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profiles": _load(path),
    }


def save_bundle(payload: Dict[str, Any], path: Path) -> None:
    """Атомарная запись бандла, всегда 0600 (внутри share-ссылки)."""
    _atomic_write_0600(
        path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def load_bundle(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileStoreError(f"bundle {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileStoreError("bundle: ожидался JSON-объект")
    return raw


def import_bundle(payload: Dict[str, Any], replace: bool = False,
                  dry_run: bool = False,
                  path: Optional[Path] = None) -> Dict[str, List[str]]:
    """Слить бандл в store; существующие имена — skip или replace.

    Ссылки валидируются до записи (битый профиль отменяет весь импорт).
    Возвращает имена по категориям added/replaced/skipped.
    """
    if payload.get("format") != BUNDLE_FORMAT:
        raise ProfileStoreError(
            f"bundle: неизвестный формат {payload.get('format')!r}")
    try:
        version = int(payload.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version != BUNDLE_VERSION:
        raise ProfileStoreError(
            f"bundle: версия {payload.get('version')!r} не поддерживается "
            f"(нужна {BUNDLE_VERSION})")
    records = payload.get("profiles")
    if not isinstance(records, list) or not records:
        raise ProfileStoreError("bundle: нет профилей")

    existing = _load(path)
    by_name = {str(p.get("name")): p for p in existing}
    added: List[str] = []
    replaced: List[str] = []
    skipped: List[str] = []
    appended: List[Dict[str, Any]] = []
    seen = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise ProfileStoreError("bundle: профиль должен быть объектом")
        name = str(raw.get("name") or "").strip()
        link_text = str(raw.get("link") or "").strip()
        if not name:
            raise ProfileStoreError("bundle: профиль без имени")
        if not link_text:
            raise ProfileStoreError(f"bundle: профиль '{name}' без ссылки")
        if name in seen:
            raise ProfileStoreError(f"bundle: дубль имени '{name}'")
        seen.add(name)
        try:
            parsed = parse_link(link_text)
        except LinkError as exc:
            raise ProfileStoreError(
                f"bundle: профиль '{name}': {exc}") from exc
        try:
            port = int(raw.get("socks_port")
                       or DEFAULT_SOCKS_PORT[parsed.protocol])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileStoreError(
                f"bundle: профиль '{name}': некорректный socks_port") from exc
        if not 1 <= port <= 65535:
            raise ProfileStoreError(
                f"bundle: профиль '{name}': socks_port {port} вне 1..65535")
        record = {
            "name": name,
            "protocol": parsed.protocol,
            "link": link_text,
            "socks_port": port,
            "created": str(raw.get("created")
                           or datetime.now(timezone.utc).isoformat(
                               timespec="seconds")),
        }
        if name in by_name:
            if not replace:
                skipped.append(name)
                continue
            replaced.append(name)
            by_name[name] = record
        else:
            added.append(name)
            by_name[name] = record
            appended.append(record)

    if not dry_run:
        merged = [by_name[str(p.get("name"))] for p in existing] + appended
        _save(merged, path)
    return {"added": added, "replaced": replaced, "skipped": skipped}
