"""AWG poller must survive a direct↔relay interface switch (W1 relay).

Before the fix the poller resolved the interface once at startup: after
``vpn start relay`` (``@laptop`` → ``@relay``) ``poll()`` saw a vanished iface
and stayed ``iface_gone`` forever — the G-04 branch silently stayed off and the
guard looked degraded until a restart.  A vanished tracked iface is now
re-discovered once; a genuine disappearance (no tunnel left) still reports
``iface_gone``.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import counters  # noqa: E402


def make_iface(root: Path, name: str, *, tx: int = 0, rx: int = 0) -> Path:
    base = root / name
    stats = base / "statistics"
    stats.mkdir(parents=True, exist_ok=True)
    (base / "type").write_text(counters.ARPHRD_NONE + "\n", encoding="ascii")
    (stats / "tx_bytes").write_text(f"{tx}\n", encoding="ascii")
    (stats / "rx_bytes").write_text(f"{rx}\n", encoding="ascii")
    (stats / "tx_packets").write_text("1\n", encoding="ascii")
    (stats / "rx_packets").write_text("1\n", encoding="ascii")
    return base


def test_switch_laptop_to_relay_is_followed(tmp_path: Path) -> None:
    make_iface(tmp_path, "laptop", tx=100, rx=50)
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    assert poller.resolve_iface() == "laptop"
    first = poller.poll(1.0)
    assert first is not None and first.iface == "laptop"

    # `vpn start relay`: @laptop goes away, `relay` appears
    shutil.rmtree(tmp_path / "laptop")
    make_iface(tmp_path, "relay", tx=200, rx=100)

    second = poller.poll(2.0)
    assert second is not None
    assert second.iface == "relay"
    assert poller.iface == "relay"
    assert poller.iface_gone is False


def test_genuine_disappearance_still_reports_gone(tmp_path: Path) -> None:
    make_iface(tmp_path, "laptop", tx=1, rx=1)
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    poller.resolve_iface()
    poller.poll(1.0)
    shutil.rmtree(tmp_path / "laptop")

    assert poller.poll(2.0) is None
    assert poller.iface_gone is True


def test_poller_without_channel_picks_up_later_tunnel(tmp_path: Path) -> None:
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    assert poller.resolve_iface() is None
    assert poller.poll(1.0) is None
    make_iface(tmp_path, "relay", tx=5, rx=5)
    sample = poller.poll(2.0)
    assert sample is not None and sample.iface == "relay"
    assert poller.iface_gone is False
