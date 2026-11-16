"""sysfs interface-counter poller for AmneziaWG (WP-G1, SPEC §6.3/§7.2).

AmneziaWG prints no application logs at all, so its observability is built
from the world-readable monotonic counters
``/sys/class/net/<iface>/statistics/{rx,tx}_{bytes,packets}``.

Hard rules (R-4 / SPEC §6.3):

- **no ``systemctl``** for interface discovery — candidates come from
  ``/sys/class/net/*/type`` with ``ARPHRD_NONE`` (wireguard-like tunnels),
  ``lo`` and already-known profile interfaces excluded;
- **no ``awg show`` and no ``sudo``** — everything needed is two 64-bit
  counters per interface;
- **no network sockets** — pure ``read_text`` on sysfs.

Ambiguity is handled honestly (SPEC §6.3): more than one candidate →
``awg_iface_ambiguous`` note and the interface whose ``tx_bytes`` actually
grows wins; zero candidates → the G-04 branch stays off and coverage
reports ``amneziawg=missing``.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional, Sequence, Tuple

#: WireGuard/AmneziaWG interfaces report ``ARPHRD_NONE`` in /sys class/net/type.
ARPHRD_NONE = "65534"

DEFAULT_SYSFS_ROOT = Path("/sys/class/net")

#: Linux interface names: 1–15 chars, no '/', no '..' — safe as a path part.
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")

#: AWG unit instance prefixes (systemd template units, SPEC §6.2/§6.3).
AWG_UNIT_PREFIXES: Tuple[str, ...] = ("awg-quick@", "vpn-awg-split@")

# Window defaults (SPEC §8.1 threshold table; live in guard.json at runtime).
DEFAULT_WINDOW_S = 120
DEFAULT_TX_MIN_BYTES = 262144  # ~2 KiB/s — "the client really transmits"
DEFAULT_RX_RATIO = 0.05        # rx growth below this share of tx = no replies


def valid_iface(name: Optional[str]) -> bool:
    """``True`` for a name that can be joined to ``/sys/class/net``."""
    return bool(name) and bool(_IFACE_RE.match(name)) and name not in ("..", ".")


def iface_from_unit(unit: Optional[str]) -> Optional[str]:
    """``vpn-awg-split@laptop.service`` → ``laptop`` (S2 lifecycle priority 1)."""
    if not isinstance(unit, str) or "@" not in unit:
        return None
    instance = unit.split("@", 1)[1]
    if instance.endswith(".service"):
        instance = instance[:-len(".service")]
    instance = instance.split(":", 1)[0]  # drop ``:cmd`` journald suffixes
    return instance if valid_iface(instance) and instance != "*" else None



@dataclass(frozen=True)
class CounterSample:
    """One point-in-time reading of an interface's sysfs counters."""
    iface: str
    tx_bytes: int
    rx_bytes: int
    tx_packets: int
    rx_packets: int
    mono_s: float


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def read_sample(iface: str,
                sysfs_root: Path = DEFAULT_SYSFS_ROOT,
                mono_s: Optional[float] = None) -> Optional[CounterSample]:
    """Read the four counters of *iface*; ``None`` when sysfs fails."""
    base = sysfs_root / iface / "statistics"
    tx_bytes = _read_int(base / "tx_bytes")
    rx_bytes = _read_int(base / "rx_bytes")
    tx_packets = _read_int(base / "tx_packets")
    rx_packets = _read_int(base / "rx_packets")
    if (tx_bytes is None or rx_bytes is None
            or tx_packets is None or rx_packets is None):
        return None
    return CounterSample(
        iface=iface,
        tx_bytes=tx_bytes,
        rx_bytes=rx_bytes,
        tx_packets=tx_packets,
        rx_packets=rx_packets,
        mono_s=mono_s if mono_s is not None else time.monotonic(),
    )


def discover_awg_ifaces(sysfs_root: Path = DEFAULT_SYSFS_ROOT,
                        exclude: Tuple[str, ...] = ("lo",)
                        ) -> Tuple[List[str], Optional[str]]:
    """Candidate tunnel interfaces plus an honest ambiguity note.

    Priority 1 of SPEC §6.3 (instance name from an S2 lifecycle event) is
    supplied by the caller as a hint; this scan is priority 2.  Returns
    ``([], None)`` when sysfs is unreadable — coverage then reports
    ``amneziawg=missing``, never a fabricated interface.
    """
    candidates: List[str] = []
    try:
        entries = sorted(sysfs_root.iterdir())
    except OSError:
        return [], None
    for entry in entries:
        if not entry.is_dir() or entry.name in exclude:
            continue
        try:
            iftype = (entry / "type").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if iftype == ARPHRD_NONE:
            candidates.append(entry.name)
    note = "awg_iface_ambiguous" if len(candidates) > 1 else None
    return candidates, note


def iface_exists(iface: str, sysfs_root: Path = DEFAULT_SYSFS_ROOT) -> bool:
    return (sysfs_root / iface / "type").exists()


class AwgCounterPoller:
    """1 Hz sysfs sampler producing the derived AWG signals (§7.2).

    The daemon calls :meth:`poll` once per second (2 file reads per
    interface).  Every completed ``window_s`` window the poller evaluates:

    - ``awg_tx_growth`` — ``tx_bytes`` grew by ≥ ``tx_min_bytes``;
    - ``awg_rx_flat``  — ``rx_bytes`` grew by < ``rx_ratio`` of the tx
      growth (only meaningful together with ``awg_tx_growth``);
    - ``awg_iface_gone`` — the tracked interface vanished from sysfs
      (then the window evaluation pauses until it reappears).

    ``consecutive_tx_no_rx`` counts back-to-back windows where both
    ``awg_tx_growth`` and ``awg_rx_flat`` held — the G-04 ``awg_tx_no_rx``
    branch (WP-G3) fires on ≥ 2.
    """

    def __init__(self, *, sysfs_root: Path = DEFAULT_SYSFS_ROOT,
                 iface: Optional[str] = None,
                 window_s: int = DEFAULT_WINDOW_S,
                 tx_min_bytes: int = DEFAULT_TX_MIN_BYTES,
                 rx_ratio: float = DEFAULT_RX_RATIO,
                 mono_fn=time.monotonic) -> None:
        self.sysfs_root = sysfs_root
        self.window_s = window_s
        self.tx_min_bytes = tx_min_bytes
        self.rx_ratio = rx_ratio
        self._mono_fn = mono_fn
        self.note: Optional[str] = None
        self.iface_gone = False
        self.consecutive_tx_no_rx = 0
        self._samples: Deque[CounterSample] = deque(maxlen=8192)
        self._window_base: Optional[CounterSample] = None
        self._evaluated_window: Optional[Dict[str, object]] = None
        #: Last closed window kept for coverage/rules introspection even after
        #: ``pop_closed_window()`` handed it to the run loop (D-07).
        self._last_window: Optional[Dict[str, object]] = None
        self._iface = iface if valid_iface(iface) else None
        #: candidate → last observed tx_bytes while ambiguity is unresolved
        self._candidates: Dict[str, Optional[int]] = {}

    # -- interface selection ------------------------------------------------

    @property
    def iface(self) -> Optional[str]:
        return self._iface

    @property
    def ambiguous(self) -> bool:
        return bool(self._candidates)

    def resolve_iface(self, hint: Optional[str] = None) -> Optional[str]:
        """Pick the interface: S2 lifecycle hint first, then a sysfs scan.

        With several candidates the poller tracks all of them for one
        window and keeps the one whose ``tx_bytes`` grows (SPEC §6.3);
        until then the first candidate is tracked and the ambiguity note
        stays visible in coverage *and* the G-04 branch stays off.
        """
        if hint and valid_iface(hint) and iface_exists(hint, self.sysfs_root):
            self._iface = hint
            self.note = None
            self._candidates = {}
            return hint
        candidates, note = discover_awg_ifaces(self.sysfs_root)
        if not candidates:
            self._iface = None
            self.note = None
            self._candidates = {}
            return None
        self._iface = candidates[0]
        if len(candidates) > 1:
            self.note = note or "awg_iface_ambiguous"
            self._candidates = {name: None for name in candidates}
        else:
            self.note = None
            self._candidates = {}
        return self._iface

    # -- sampling -------------------------------------------------------------

    def poll(self, mono_s: Optional[float] = None) -> Optional[CounterSample]:
        """Take one sample of the tracked interface (call ~1 Hz)."""
        stamp = mono_s if mono_s is not None else self._mono_fn()
        if self._candidates:
            return self._poll_candidates(stamp)
        if self._iface is None:
            return None
        if not iface_exists(self._iface, self.sysfs_root):
            self.iface_gone = True
            self.consecutive_tx_no_rx = 0
            self._window_base = None
            return None
        self.iface_gone = False
        sample = read_sample(self._iface, self.sysfs_root, stamp)
        if sample is None:
            return None
        self._samples.append(sample)
        closed = self._maybe_close_window(sample)
        if closed is not None:
            # D-07: keep the window until the run loop pops it exactly once —
            # assigning unconditionally would drop it on the next 1 s tick.
            self._evaluated_window = closed
            self._last_window = closed
        return sample

    def _poll_candidates(self, stamp: float) -> Optional[CounterSample]:
        """Sample every candidate; a talking interface resolves ambiguity."""
        samples: Dict[str, CounterSample] = {}
        for name in list(self._candidates):
            sample = read_sample(name, self.sysfs_root, stamp)
            if sample is None:
                self._candidates.pop(name, None)
                continue
            samples[name] = sample
        if not samples:
            self._iface = None
            self.note = None
            self._candidates = {}
            return None
        winner: Optional[str] = None
        for name, sample in samples.items():
            previous = self._candidates.get(name)
            self._candidates[name] = sample.tx_bytes
            if previous is not None and sample.tx_bytes > previous:
                winner = name
        if winner is not None:
            # a talking interface resolves the ambiguity (SPEC §6.3)
            self._iface = winner
            self.note = None
            self._candidates = {}
            chosen = samples[winner]
            self.iface_gone = False
            self._samples.append(chosen)
            self._window_base = chosen
            return chosen
        # still ambiguous: keep the provisional interface, note stays
        self.iface_gone = False
        return samples.get(self._iface or "") or next(iter(samples.values()))

    # -- window evaluation ------------------------------------------------------

    def _maybe_close_window(self, sample: CounterSample) -> Optional[Dict[str, object]]:
        if self._window_base is None:
            self._window_base = sample
            return None
        base = self._window_base
        if sample.mono_s - base.mono_s < self.window_s:
            return None
        self._window_base = sample
        tx_growth = sample.tx_bytes - base.tx_bytes
        rx_growth = sample.rx_bytes - base.rx_bytes
        tx_grew = tx_growth >= self.tx_min_bytes
        rx_flat = tx_grew and (rx_growth < self.rx_ratio * max(tx_growth, 1))
        if tx_grew and rx_flat:
            self.consecutive_tx_no_rx += 1
        else:
            self.consecutive_tx_no_rx = 0
        signals: List[str] = []
        if tx_grew:
            signals.append("awg_tx_growth")
        if rx_flat:
            signals.append("awg_rx_flat")
        return {
            "iface": sample.iface,
            "window_s": self.window_s,
            "tx_growth": tx_growth,
            "rx_growth": rx_growth,
            "signals": signals,
            # D-06/D-07: the run loop stamps the closed window in the single
            # monotonic domain instead of re-deriving it from wall-clock.
            "mono_s": sample.mono_s,
            "mono_us": int(sample.mono_s * 1_000_000),
        }

    def pop_closed_window(self) -> Optional[Dict[str, object]]:
        """Hand the last closed window to the run loop **exactly once** (D-07).

        The 1 Hz ``poll()`` keeps the window until it is popped, so the daemon
        can call ``engine.note_awg_window(win)`` once per 120 s window instead
        of re-noting the same window on every one-second tick.
        """
        window = self._evaluated_window
        self._evaluated_window = None
        return window

    def evaluate(self) -> Dict[str, object]:
        """Current derived state for coverage/rules (no I/O)."""
        last_window = self._evaluated_window or self._last_window
        signals: List[str] = []
        if isinstance(last_window, dict):
            raw_signals = last_window.get("signals")
            if isinstance(raw_signals, list):
                signals = [str(item) for item in raw_signals
                           if isinstance(item, str)]
        if self.iface_gone:
            signals.append("awg_iface_gone")
        branch = (self._iface is not None
                  and not self.ambiguous
                  and not self.iface_gone)
        return {
            "iface": self._iface,
            "note": self.note,
            "signals": signals,
            "consecutive_tx_no_rx": self.consecutive_tx_no_rx,
            "iface_gone": self.iface_gone,
            "ambiguous": self.ambiguous,
            # SPEC §6.3/§8.2: ambiguity keeps the G-04 branch off
            "g04_branch_enabled": branch,
        }


def channel_state(*, vpnm_units: Sequence[str] = (),
                  awg_iface: Optional[str] = None,
                  awg_lifecycle_active: bool = False) -> str:
    """``armed``/``idle`` per SPEC §8.1 (R-11 / AC-15).

    ``idle`` is allowed **only** when no channel exists at all: no
    ``vpnm-*`` unit, no AmneziaWG interface in sysfs and no active AWG
    lifecycle.  A live AWG with no traffic is ``armed``, never ``idle`` —
    a laptop whose main channel is AWG must not look dead.
    """
    if vpnm_units or awg_iface or awg_lifecycle_active:
        return "armed"
    return "idle"
