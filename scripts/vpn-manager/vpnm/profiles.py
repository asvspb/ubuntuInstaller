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
    ProfileLink,
)

CONFIG_DIR = Path(os.environ.get("VPN_MANAGER_CONFIG_DIR",
                                 str(Path.home() / ".config" / "vpn-manager")))
STORE_PATH = CONFIG_DIR / "profiles.json"

DEFAULT_SOCKS_PORT = {
    PROTOCOL_XRAY_REALITY: 2080,
    PROTOCOL_XRAY_XHTTP: 2080,
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
