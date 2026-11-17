"""Global test isolation for vpnm.

Two invariants are enforced here, both autouse:

1. ``vpnm.mode`` resolves its data directory at call time (``data_dir()``), so an
   env override keeps every CLI test away from the developer's real
   ``~/.local/share/vpn-manager/mode.json`` — ``vpn start``/``vpn down``/``vpn
   scheme`` write the active mode as a side effect even when systemctl is mocked.
2. **No test may touch the live system.** A global guard wraps
   ``subprocess.run``/``subprocess.Popen`` and fails the test on any
   system-mutating command (``systemctl``/``awg``/``nft``/``ip``/… or a run of
   ``17_xray-split.sh``). This exists because a 2026-11-18 pytest run really
   executed ``17_xray-split.sh --off`` and ``systemctl stop
   vpn-awg-split@relay.service`` and tore down a live tunnel (AGENTS §6).
   Deliberate "live" runs opt in with ``VPNM_TEST_ALLOW_SYSTEM=1``.

The guard lives in ``tests/sys_guard.py`` (imported as a plain module: pytest
puts this ``tests/`` directory on ``sys.path``).
"""

from __future__ import annotations

import pytest

import sys_guard


@pytest.fixture(autouse=True)
def _isolate_vpnm_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "vpnm-data"))
    yield


@pytest.fixture(autouse=True)
def _forbid_real_system_commands(monkeypatch):
    """Любой реальный вызов systemctl/awg/nft/ip/17_xray-split падает тестом."""
    sys_guard.install(monkeypatch)
    yield
