"""Binary installer for xray-core and hysteria into ~/.local/lib/vpn/.

Downloads from GitHub releases, walking a mirror list in order (direct ->
ghproxy.net -> ghfast.top). Install is idempotent: if the binary already
reports the target version, the download is skipped.
"""

from __future__ import annotations

import io
import os
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, List, Tuple

BIN_DIR = Path(os.environ.get(
    "VPN_MANAGER_BIN_DIR", str(Path.home() / ".local" / "lib" / "vpn")))

XRAY_VERSION = "25.1.1"
HYSTERIA_VERSION = "v2.13.0"

XRAY_URL = (f"https://github.com/XTLS/Xray-core/releases/download/"
            f"v{XRAY_VERSION}/Xray-linux-64.zip")
HYSTERIA_URL = ("https://github.com/apernet/hysteria/releases/download/"
                f"app/{HYSTERIA_VERSION}/hysteria-linux-amd64")

MIRRORS = ("https://ghproxy.net/", "https://ghfast.top/")

DOWNLOAD_TIMEOUT = 120  # seconds per mirror attempt


class BinError(Exception):
    """Raised on download or install failures."""


def mirror_urls(url: str) -> List[str]:
    """Direct URL first, then each proxy mirror in order."""
    return [url] + [mirror + url for mirror in MIRRORS]


def _fetch(urls: List[str]) -> bytes:
    """Download the first reachable URL; raise BinError when all fail."""
    last_error: Exception = BinError("no download mirrors given")
    for url in urls:
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "vpn-manager/0.1"})
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as resp:
                return resp.read()
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
    raise BinError(f"all download mirrors failed, last error: {last_error}")


def _version_output(bin_path: Path) -> str:
    try:
        proc = subprocess.run([str(bin_path), "version"], capture_output=True,
                              text=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout + proc.stderr)


def xray_version_ok(bin_path: Path) -> bool:
    return XRAY_VERSION in _version_output(bin_path)


def hysteria_version_ok(bin_path: Path) -> bool:
    return HYSTERIA_VERSION in _version_output(bin_path)


def _install_file(data: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    os.chmod(dest, 0o755)


def install_xray(bin_dir: Path = BIN_DIR, fetch: Callable[[List[str]], bytes] = _fetch,
                 version_ok: Callable[[Path], bool] = xray_version_ok) -> str:
    """Install xray + geoip.dat + geosite.dat. Returns 'installed'|'skipped'."""
    target = bin_dir / "xray"
    if target.exists() and version_ok(target):
        return "skipped"
    archive = zipfile.ZipFile(io.BytesIO(fetch(mirror_urls(XRAY_URL))))
    wanted = {"xray", "geoip.dat", "geosite.dat"}
    found = {name for name in archive.namelist() if name in wanted}
    missing = wanted - found
    if missing:
        raise BinError(f"xray zip is missing members: {sorted(missing)}")
    for name in sorted(wanted):
        _install_file(archive.read(name), bin_dir / name)
    if not version_ok(target):
        raise BinError(f"installed xray failed version check "
                       f"(expected {XRAY_VERSION})")
    return "installed"


def install_hysteria(bin_dir: Path = BIN_DIR,
                     fetch: Callable[[List[str]], bytes] = _fetch,
                     version_ok: Callable[[Path], bool] = hysteria_version_ok) -> str:
    """Install the hysteria client binary. Returns 'installed'|'skipped'."""
    target = bin_dir / "hysteria"
    if target.exists() and version_ok(target):
        return "skipped"
    _install_file(fetch(mirror_urls(HYSTERIA_URL)), target)
    if not version_ok(target):
        raise BinError(f"installed hysteria failed version check "
                       f"(expected {HYSTERIA_VERSION})")
    return "installed"


def install_all(bin_dir: Path = BIN_DIR,
                fetch: Callable[[List[str]], bytes] = _fetch,
                xray_ok: Callable[[Path], bool] = xray_version_ok,
                hysteria_ok: Callable[[Path], bool] = hysteria_version_ok
                ) -> List[Tuple[str, str]]:
    """Install both binaries; returns [(name, 'installed'|'skipped'), ...]."""
    return [
        ("xray", install_xray(bin_dir, fetch, xray_ok)),
        ("hysteria", install_hysteria(bin_dir, fetch, hysteria_ok)),
    ]
