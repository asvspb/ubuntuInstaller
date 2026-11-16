"""WP-G1 counter tests: AWG sysfs discovery, ambiguity, G-04 branch, idle ban.

No root, no network, no real sysfs: a temporary directory imitates
``/sys/class/net`` (``type``, ``statistics/{rx,tx}_{bytes,packets}``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import counters  # noqa: E402


def make_iface(root: Path, name: str, *, type_: str = counters.ARPHRD_NONE,
               tx: int = 0, rx: int = 0) -> Path:
    base = root / name
    stats = base / "statistics"
    stats.mkdir(parents=True, exist_ok=True)
    (base / "type").write_text(type_ + "\n", encoding="ascii")
    (stats / "tx_bytes").write_text(f"{tx}\n", encoding="ascii")
    (stats / "rx_bytes").write_text(f"{rx}\n", encoding="ascii")
    (stats / "tx_packets").write_text("1\n", encoding="ascii")
    (stats / "rx_packets").write_text("1\n", encoding="ascii")
    return base


# --- discovery ---------------------------------------------------------------

def test_discover_filters_arp_none_and_lo(tmp_path: Path) -> None:
    make_iface(tmp_path, "lo", type_="772")
    make_iface(tmp_path, "wlan0", type_="1")
    make_iface(tmp_path, "laptop", tx=10, rx=10)
    candidates, note = counters.discover_awg_ifaces(tmp_path)
    assert candidates == ["laptop"]
    assert note is None


def test_discover_multiple_is_ambiguous(tmp_path: Path) -> None:
    make_iface(tmp_path, "awg-home")
    make_iface(tmp_path, "awg-work")
    candidates, note = counters.discover_awg_ifaces(tmp_path)
    assert candidates == ["awg-home", "awg-work"]
    assert note == "awg_iface_ambiguous"


def test_discover_unreadable_root_is_missing(tmp_path: Path) -> None:
    assert counters.discover_awg_ifaces(tmp_path / "absent") == ([], None)


def test_resolve_iface_never_hardcodes_awg0(tmp_path: Path) -> None:
    make_iface(tmp_path, "laptop", tx=1, rx=1)
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    assert poller.resolve_iface() == "laptop"
    assert poller.note is None


def test_resolve_iface_prefers_lifecycle_hint(tmp_path: Path) -> None:
    make_iface(tmp_path, "awg-home")
    make_iface(tmp_path, "awg-work")
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    assert poller.resolve_iface("awg-work") == "awg-work"
    assert poller.note is None
    assert poller.ambiguous is False


def test_iface_from_unit_and_validation() -> None:
    assert counters.iface_from_unit("vpn-awg-split@laptop.service") == "laptop"
    assert counters.iface_from_unit("awg-quick@home.service") == "home"
    assert counters.iface_from_unit("awg-quick@*.service") is None  # glob
    assert counters.iface_from_unit("dnsmasq.service") is None
    assert counters.iface_from_unit(None) is None
    assert counters.valid_iface("laptop") is True
    assert counters.valid_iface("../etc") is False
    assert counters.valid_iface("a" * 16) is False


# --- ambiguity resolution ----------------------------------------------------

def test_ambiguity_resolved_by_tx_growth(tmp_path: Path) -> None:
    make_iface(tmp_path, "awg-a", tx=100, rx=100)
    make_iface(tmp_path, "awg-b", tx=100, rx=100)
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    assert poller.resolve_iface() == "awg-a"
    assert poller.note == "awg_iface_ambiguous"
    assert poller.evaluate()["g04_branch_enabled"] is False

    poller.poll(mono_s=0.0)  # first sample: no growth yet
    assert poller.ambiguous is True
    # awg-b starts talking
    (tmp_path / "awg-b" / "statistics" / "tx_bytes").write_text(
        "5000\n", encoding="ascii")
    poller.poll(mono_s=1.0)
    assert poller.iface == "awg-b"
    assert poller.note is None
    assert poller.evaluate()["g04_branch_enabled"] is True


def test_no_candidates_keeps_g04_off(tmp_path: Path) -> None:
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    assert poller.resolve_iface() is None
    state = poller.evaluate()
    assert state["iface"] is None
    assert state["g04_branch_enabled"] is False
    assert poller.poll(mono_s=0.0) is None


# --- window evaluation (G-04 awg_tx_no_rx) -----------------------------------

def test_two_windows_of_tx_without_rx(tmp_path: Path) -> None:
    make_iface(tmp_path, "laptop", tx=0, rx=0)
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path, window_s=120)
    assert poller.resolve_iface() == "laptop"
    poller.poll(mono_s=0.0)
    (tmp_path / "laptop" / "statistics" / "tx_bytes").write_text(
        "300000\n", encoding="ascii")
    (tmp_path / "laptop" / "statistics" / "rx_bytes").write_text(
        "100\n", encoding="ascii")
    poller.poll(mono_s=120.0)
    assert poller.consecutive_tx_no_rx == 1
    assert poller.evaluate()["signals"] == ["awg_tx_growth", "awg_rx_flat"]

    (tmp_path / "laptop" / "statistics" / "tx_bytes").write_text(
        "700000\n", encoding="ascii")
    (tmp_path / "laptop" / "statistics" / "rx_bytes").write_text(
        "200\n", encoding="ascii")
    poller.poll(mono_s=240.0)
    assert poller.consecutive_tx_no_rx == 2  # G-04 branch fires on >= 2


def test_healthy_rx_resets_window_counter(tmp_path: Path) -> None:
    make_iface(tmp_path, "laptop", tx=0, rx=0)
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path, window_s=120)
    assert poller.resolve_iface() == "laptop"
    poller.poll(mono_s=0.0)
    (tmp_path / "laptop" / "statistics" / "tx_bytes").write_text(
        "300000\n", encoding="ascii")
    (tmp_path / "laptop" / "statistics" / "rx_bytes").write_text(
        "150000\n", encoding="ascii")
    poller.poll(mono_s=120.0)
    assert poller.consecutive_tx_no_rx == 0
    assert "awg_rx_flat" not in poller.evaluate()["signals"]


def test_iface_gone_disables_branch(tmp_path: Path) -> None:
    base = make_iface(tmp_path, "laptop", tx=1, rx=1)
    poller = counters.AwgCounterPoller(sysfs_root=tmp_path)
    assert poller.resolve_iface() == "laptop"
    import shutil
    shutil.rmtree(base)
    assert poller.poll(mono_s=1.0) is None
    state = poller.evaluate()
    assert state["iface_gone"] is True
    assert "awg_iface_gone" in state["signals"]
    assert state["g04_branch_enabled"] is False


def test_read_sample_missing_counters_is_none(tmp_path: Path) -> None:
    assert counters.read_sample("ghost", tmp_path) is None


# --- idle prohibition (R-11 / AC-15) -----------------------------------------

def test_awg_alive_is_armed_not_idle() -> None:
    # a live AWG interface must never look idle, even with zero traffic
    assert counters.channel_state(awg_iface="laptop") == "armed"
    assert counters.channel_state(
        awg_lifecycle_active=True) == "armed"
    assert counters.channel_state(vpnm_units=("asv2",)) == "armed"


def test_idle_only_without_any_channel() -> None:
    assert counters.channel_state() == "idle"
