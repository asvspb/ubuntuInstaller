"""Global test isolation for vpnm.

``vpnm.mode`` resolves its data directory at call time (``data_dir()``), so an
autouse env override keeps every CLI test away from the developer's real
``~/.local/share/vpn-manager/mode.json`` — ``vpn start``/``vpn down``/``vpn
scheme`` write the active mode as a side effect even when systemctl is mocked.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_vpnm_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "vpnm-data"))
    yield
