"""Settings store: ~/.config/vpn-manager/settings.json.

Currently holds the default ssh host for ``vpn profile pull``. Written
atomically; the file is chmod 0600 for consistency with the rest of the
config directory (keys may be added later that deserve it).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_DIR = Path(os.environ.get("VPN_MANAGER_CONFIG_DIR",
                                 str(Path.home() / ".config" / "vpn-manager")))
SETTINGS_PATH = CONFIG_DIR / "settings.json"


def _load(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path if path is not None else SETTINGS_PATH
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _atomic_write_0600(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                    suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def get_host(path: Optional[Path] = None) -> Optional[str]:
    """Return the saved ssh host, or None when not configured."""
    value = _load(path).get("ssh_host")
    return value if isinstance(value, str) and value else None


def set_host(host: str, path: Optional[Path] = None) -> None:
    """Persist the ssh host (merges into the existing settings)."""
    settings = _load(path)
    settings["ssh_host"] = host
    _atomic_write_0600(path if path is not None else SETTINGS_PATH,
                       json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


def get_mux(path: Optional[Path] = None) -> bool:
    """True when new `vpn up` runs should generate mux-enabled WS configs.

    Applies only to xray-cdn-ws profiles (see SPEC-20261113); absence of the
    key means False (older settings files migrate cleanly, unknown keys are
    preserved on write).
    """
    value = _load(path).get("mux")
    return value is True


def set_mux(enabled: bool, path: Optional[Path] = None) -> None:
    """Persist the mux flag (merges into the existing settings)."""
    settings = _load(path)
    settings["mux"] = bool(enabled)
    _atomic_write_0600(path if path is not None else SETTINGS_PATH,
                       json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
