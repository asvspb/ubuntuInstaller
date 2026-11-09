"""GNOME system proxy control via gsettings.

- ``vpn sysproxy on``  -> mode 'manual' + socks host 127.0.0.1 + port of the
  active profile;
- ``vpn sysproxy off`` -> mode 'none';
- ``vpn sysproxy status`` -> read the current values.

gsettings is invoked with an argument list; when the binary is missing
(non-GNOME system) a SysproxyError with a readable message is raised.
"""

from __future__ import annotations

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


def _get(runner: RunCmd, schema: str, key: str) -> str:
    _rc, out, _err = _run(runner, ["gsettings", "get", schema, key])
    return out.strip().strip("'\"")


def _default_runner() -> RunCmd:
    return run_cmd


def on(socks_port: int, run: Optional[RunCmd] = None) -> None:
    """Enable the GNOME SOCKS proxy pointing at the active profile's port."""
    runner: RunCmd = run or _default_runner()
    _set(runner, SCHEMA, "mode", "manual")
    _set(runner, SCHEMA_SOCKS, "host", SOCKS_HOST)
    _set(runner, SCHEMA_SOCKS, "port", str(int(socks_port)))


def off(run: Optional[RunCmd] = None) -> None:
    """Disable the GNOME system proxy (mode 'none')."""
    runner: RunCmd = run or _default_runner()
    _set(runner, SCHEMA, "mode", "none")


def status(run: Optional[RunCmd] = None) -> Tuple[str, str, str]:
    """Return (mode, socks host, socks port) as currently configured."""
    runner: RunCmd = run or _default_runner()
    return (_get(runner, SCHEMA, "mode"), _get(runner, SCHEMA_SOCKS, "host"),
            _get(runner, SCHEMA_SOCKS, "port"))
