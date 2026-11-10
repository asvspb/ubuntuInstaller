"""GNOME system proxy control via gsettings.

- ``vpn sysproxy on``  -> reset http/https/ftp host+port (socks-only manual
  configuration) + mode 'manual' + socks host 127.0.0.1 + port of the active
  profile;
- ``vpn sysproxy off`` -> reset http/https/ftp host+port + mode 'none';
- ``vpn sysproxy status`` -> read the current values.

gsettings is invoked with an argument list; when the binary is missing
(non-GNOME system) a SysproxyError with a readable message is raised.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional, Tuple

from .runners import RunCmd, run_cmd

SCHEMA = "org.gnome.system.proxy"
SCHEMA_SOCKS = "org.gnome.system.proxy.socks"
SOCKS_HOST = "127.0.0.1"


class SysproxyError(Exception):
    """Raised when gsettings is unavailable or a call fails."""


def _run(runner: RunCmd, args: list) -> Tuple[int, str, str]:
    rc, out, err = runner(args)
    if rc == 127:
        raise SysproxyError(
            "gsettings not found: this system does not look like GNOME "
            "(install gsettings / dconf-cli or use another proxy method)")
    if rc != 0:
        raise SysproxyError(f"gsettings {' '.join(args[1:])} failed: "
                            f"{(err or out).strip()}")
    return rc, out, err


def _set(runner: RunCmd, schema: str, key: str, value: str) -> None:
    _run(runner, ["gsettings", "set", schema, key, value])


# Stale http/https/ftp entries (e.g. left over from another tool) make
# Chrome and other GNOME-aware apps route manual-mode traffic through a
# dead proxy even when the socks host is correct. Reset them so the
# configuration is unambiguously socks-only.
RESET_SCHEMA_KEYS: Tuple[Tuple[str, str], ...] = tuple(
    (f"{SCHEMA}.{schema}", key)
    for schema in ("http", "https", "ftp")
    for key in ("host", "port")
)


def _reset(runner: RunCmd, schema: str, key: str,
           warn: Callable[[str], None]) -> None:
    """Reset one gsettings key; a failure here is never fatal (warn only)."""
    rc, out, err = runner(["gsettings", "reset", schema, key])
    if rc != 0:
        warn(f"gsettings reset {schema}.{key} skipped: "
             f"{(err or out).strip()}")


def _reset_http_https_ftp(runner: RunCmd,
                          warn: Optional[Callable[[str], None]]) -> None:
    """Clear http/https/ftp host+port; individual failures are warn-level."""
    warn = warn if warn is not None else _default_warn
    for schema, key in RESET_SCHEMA_KEYS:
        _reset(runner, schema, key, warn)


def _get(runner: RunCmd, schema: str, key: str) -> str:
    _rc, out, _err = _run(runner, ["gsettings", "get", schema, key])
    return out.strip().strip("'\"")


def _default_runner() -> RunCmd:
    return run_cmd


def _default_warn(message: str) -> None:
    print(f"[warning] {message}", file=sys.stderr)


def on(socks_port: int, run: Optional[RunCmd] = None,
       warn: Optional[Callable[[str], None]] = None) -> None:
    """Enable a clean socks-only manual proxy at the active profile's port."""
    runner: RunCmd = run or _default_runner()
    _reset_http_https_ftp(runner, warn)
    _set(runner, SCHEMA, "mode", "manual")
    _set(runner, SCHEMA_SOCKS, "host", SOCKS_HOST)
    _set(runner, SCHEMA_SOCKS, "port", str(int(socks_port)))


def off(run: Optional[RunCmd] = None,
        warn: Optional[Callable[[str], None]] = None) -> None:
    """Disable the GNOME system proxy (mode 'none') and clear http/https/ftp."""
    runner: RunCmd = run or _default_runner()
    _reset_http_https_ftp(runner, warn)
    _set(runner, SCHEMA, "mode", "none")


def status(run: Optional[RunCmd] = None) -> Tuple[str, str, str]:
    """Return (mode, socks host, socks port) as currently configured."""
    runner: RunCmd = run or _default_runner()
    return (_get(runner, SCHEMA, "mode"), _get(runner, SCHEMA_SOCKS, "host"),
            _get(runner, SCHEMA_SOCKS, "port"))
