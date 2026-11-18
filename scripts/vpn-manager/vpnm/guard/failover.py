"""Client-side channel failover engine (SPEC-20261118-vpn-failover, WP-F2).

This module **acts**: it switches the VPN channel when the direct channel is
provably gone.  It is deliberately separate from the read-only guard daemon
(ADR-014/ADR-015): ``vpn-guard.service`` keeps parsing logs and writing
evidence, while ``vpn-failover.service`` (prefix ``vpn-``, never ``vpnm-``)
executes a fixed policy.

Contract highlights:

- narrow triggers only — ``G-01`` (high), ``G-09`` (≥ medium) or the
  **derived** ``expected_lockdown`` condition from a fresh
  ``server-verdict.json``; ``G-02`` never triggers (OQ-2);
- a manual stop wins: an active ``activity_scope("down"/"reset")`` suppresses
  the agent for :data:`vpnm.guard.store.ACTIVITY_GRACE_S`;
- ``local_net_check`` is step 0 — a broken local uplink is not a VPN fault;
- ladder ``relay → asv2-hy2 → asv2-awg → asv2-cdn`` with ``pre_flight``
  before the switch and ``post_flight`` after it;
- total blackout = exactly one pass, exponential backoff, explicit banner;
- **no automatic return** after the server lockdown is lifted (§6).

Everything non-deterministic (clock, network, switch, HTTP) is injectable:
the tests run without ``sleep`` and without real sockets.
"""

from __future__ import annotations

import socket
import subprocess
import time
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence,
                    Tuple)

from . import store

# --- results / candidates -------------------------------------------------------

RESULT_OK = "ok"
RESULT_DEAD_ENTRY = "dead_entry"
RESULT_DEAD_EXIT = "dead_exit"
RESULT_SWITCH_ERROR = "switch_error"
RESULT_SKIPPED = "skipped"

#: One rung of the ladder (SPEC §4.1).  ``kind`` decides the guard clause:
#: a ``scheme`` needs root (``sudo -n``), a ``profile`` is a socks client.
Candidate = namedtuple("Candidate", "id kind args")
Candidate.__doc__ = "One ladder rung: id, kind ('scheme'|'profile'), CLI args."

LADDER: Tuple[Candidate, ...] = (
    Candidate("relay", "scheme", ("start", "relay")),
    Candidate("asv2-hy2", "profile", ("switch", "asv2-hy2")),
    Candidate("asv2-awg", "scheme", ("start",)),
    Candidate("asv2-cdn", "profile", ("switch", "asv2-cdn")),
)
LADDER_BY_ID: Mapping[str, Candidate] = {item.id: item for item in LADDER}

#: Where the AWG/relay configs live: the invoking user's ``~/.awg``.  ``sudo``
#: resets HOME to ``/root``, so a scheme rung would otherwise look for
#: ``/root/.awg/relay.conf`` and fail.
_HOME_ENV = f"HOME={Path.home()}"


def vpn_executable() -> Tuple[str, ...]:
    """Absolute argv prefix of the vpn CLI (never a bare name).

    Root's PATH has no ``vpn`` (pipx installs into ``~/.local/bin``), so the
    bare name fails under ``sudo`` with "command not found" — found in live
    acceptance.  Fall back to the interpreter that runs the agent, which is
    the same venv that has ``vpnm`` importable.
    """
    import shutil
    import sys

    exe = shutil.which("vpn")
    if exe:
        return (exe,)
    return (sys.executable, "-m", "vpnm")


def candidate_argv(candidate: Candidate) -> Tuple[str, ...]:
    """Full argv for one rung.

    ``scheme`` rungs need root (awg/nft/UFW) and must keep the user's HOME so
    ``~/.awg/relay.conf`` resolves; ``profile`` rungs are plain user-scope
    socks switches and must NOT use sudo.
    """
    if candidate.kind == "scheme":
        return ("sudo", "-n", "env", _HOME_ENV, *vpn_executable(),
                *candidate.args)
    return (*vpn_executable(), *candidate.args)

#: Verdict freshness for ``expected_lockdown`` (SPEC §3).
VERDICT_FRESH_S = 600
#: Findings considered "recent" for the trigger decision.
FINDING_LOOKBACK_S = 900
#: Local uplink probe target (Yandex DNS over TCP — reachable outside the VPN).
LOCAL_NET_TARGETS: Tuple[Tuple[str, int], ...] = (
    ("77.88.8.8", 53),
    ("1.1.1.1", 53),
)

RunCmd = Callable[[Sequence[str]], Tuple[int, str, str]]


def _run_cmd(cmd: Sequence[str]) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(list(cmd), capture_output=True, text=True,
                              check=False)
    except (FileNotFoundError, OSError) as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


# --- data carriers ---------------------------------------------------------------

@dataclass
class Attempt:
    """One ladder rung as it was actually handled."""

    candidate: str
    result: str
    detail: str = ""
    latency_ms: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"candidate": self.candidate,
                                   "result": self.result}
        if self.detail:
            payload["detail"] = self.detail[:120]
        return payload


@dataclass
class TriggerDecision:
    trigger: bool
    reason: str
    rule: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"trigger": self.trigger, "reason": self.reason,
                "rule": self.rule}


@dataclass
class FailoverOutcome:
    status: str
    trigger: TriggerDecision
    attempts: List[Attempt] = field(default_factory=list)
    switched_to: Optional[str] = None
    latency_ms: Optional[float] = None
    lockdown_confirmed: bool = False
    backoff_sec: Optional[int] = None
    notified: Dict[str, Any] = field(default_factory=dict)
    #: Ids of the candidates whose ``switch`` was actually invoked.
    switch_calls: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "trigger": self.trigger.as_dict(),
            "attempts": [item.as_dict() for item in self.attempts],
            "switched_to": self.switched_to,
            "latency_ms": self.latency_ms,
            "lockdown_confirmed": self.lockdown_confirmed,
            "backoff_sec": self.backoff_sec,
            "notified": dict(self.notified),
        }


# --- clock helpers ---------------------------------------------------------------

def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(text: Any) -> Optional[float]:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def seconds_since_last_switch(state: Mapping[str, Any], *,
                             now_mono_ns: Optional[int],
                             now_wall: float) -> Optional[float]:
    """Elapsed seconds since the last switch; ``None`` when there was none.

    Monotonic clock first (reboot-safe baseline: it only counts when it did
    not go backwards), wall clock as the fallback after a reboot (SPEC §3).
    """
    mono = state.get("last_switch_mono_ns")
    if (isinstance(mono, int) and not isinstance(mono, bool)
            and now_mono_ns is not None and now_mono_ns >= mono):
        return (now_mono_ns - mono) / 1e9
    wall = _parse_iso(state.get("last_switch_ts"))
    if wall is None:
        return None
    return max(0.0, now_wall - wall)


def switches_last_hour(state: Mapping[str, Any], *,
                       now_wall: float) -> int:
    """Switch timestamps within the last hour (rate limit, SPEC §3)."""
    stamps = state.get("switches")
    if not isinstance(stamps, list):
        return 0
    return sum(1 for item in stamps
               if (parsed := _parse_iso(item)) is not None
               and now_wall - parsed <= 3600)


# --- trigger ---------------------------------------------------------------------

def _rule_hits(incident: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """``(rule, severity)`` pairs carried by the incident payload."""
    findings = incident.get("findings")
    pairs: List[Tuple[str, str]] = []
    if isinstance(findings, (list, tuple)):
        for item in findings:
            if isinstance(item, Mapping):
                pairs.append((str(item.get("rule", "")),
                              str(item.get("severity", ""))))
    rule = incident.get("rule")
    if isinstance(rule, str) and rule:
        pairs.append((rule, str(incident.get("severity", ""))))
    return pairs


def should_trigger(incident: Mapping[str, Any], state: Mapping[str, Any],
                   activity: Mapping[str, Any], *,
                   config: Optional[Mapping[str, Any]] = None,
                   now_wall: float = 0.0,
                   now_mono_ns: Optional[int] = None) -> TriggerDecision:
    """All SPEC §3 conditions must hold; returns the first failing reason.

    ``incident``: ``{profile, findings|rule+severity, expected_lockdown}``.
    ``state``: ``guard.failover_state/1`` (cooldown/rate fields).
    ``activity``: ``{"down": bool, "reset": bool}``.
    """
    cfg = dict(config) if isinstance(config, Mapping) else \
        store.failover_settings()
    if not cfg.get("enabled"):
        return TriggerDecision(False, "disabled")
    profile = incident.get("profile")
    trigger_profiles = [str(item) for item in
                        (cfg.get("trigger_profiles") or [])]
    if not isinstance(profile, str) or profile not in trigger_profiles:
        return TriggerDecision(False, "profile_not_trigger")
    if activity.get("down"):
        return TriggerDecision(False, "activity_down")
    if activity.get("reset"):
        return TriggerDecision(False, "activity_reset")

    pairs = _rule_hits(incident)
    rule: Optional[str] = None
    for name, severity in pairs:
        if name == "G-01" and severity == "high":
            rule = name
            break
        if name == "G-09" and severity in ("medium", "high"):
            rule = name
            break
    if rule is None and incident.get("expected_lockdown"):
        rule = "expected_lockdown"
    if rule is None:
        return TriggerDecision(False, "no_rule")

    cooldown = cfg.get("cooldown_sec")
    if isinstance(cooldown, (int, float)):
        elapsed = seconds_since_last_switch(
            state, now_mono_ns=now_mono_ns, now_wall=now_wall)
        if elapsed is not None and elapsed < float(cooldown):
            return TriggerDecision(False, "cooldown", rule)
    limit = cfg.get("max_switches_per_hour")
    if isinstance(limit, int):
        if switches_last_hour(state, now_wall=now_wall) >= limit:
            return TriggerDecision(False, "rate_limited", rule)
    return TriggerDecision(True, "ok", rule)


# --- step 0: is the local uplink alive? ------------------------------------------

def _has_default_route(path: Path = Path("/proc/net/route")) -> Optional[bool]:
    """``True``/``False`` from the routing table, ``None`` when unreadable."""
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 2 and cols[1] == "00000000":
            return True
    return False


def local_net_check(*, connect: Optional[Callable[[str, int, float], bool]] = None,
                    timeout: float = 1.5) -> bool:
    """Default route **and** an out-of-tunnel TCP handshake (budget ≤ 1.5 s).

    ``connect`` is the injected probe of the tests.  A missing default route
    is decisive; an unreadable ``/proc/net/route`` (non-Linux) leaves the
    answer to the connect probe.
    """
    if connect is not None:
        return bool(connect("local", 0, timeout))
    if _has_default_route() is False:
        return False
    for host, port in LOCAL_NET_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


# --- endpoints / pre-flight / post-flight ----------------------------------------

def xsplit_active_profile() -> Optional[str]:
    """Profile of the running ``vpn-xray-split@<p>`` unit, or ``None``.

    The tun scheme (``vpn start reality`` / ``vpn scheme xray-split <p>``)
    never touches ``state.json["active"]`` — that file only tracks socks
    profiles.  Without this fallback the agent would sit in
    ``profile_not_trigger`` while the real direct channel dies, which is
    exactly the case ``trigger_profiles`` exists for.
    """
    from .. import xsplit

    try:
        return xsplit.active_profile(xsplit.run_cmd)
    except (OSError, ValueError, RuntimeError):
        return None


def teardown_xsplit() -> Tuple[bool, str]:
    """Tear the tun scheme down (unit + rules); never raises."""
    from .. import xsplit

    try:
        return xsplit.stop()
    except (OSError, ValueError, RuntimeError) as exc:
        return False, f"xsplit_stop_exc:{type(exc).__name__}"


def profile_endpoints(profiles_path: Optional[Path] = None
                      ) -> Dict[str, Tuple[str, int, Optional[int]]]:
    """``name -> (host, port, socks_port)`` from the profile store.

    The share link is parsed exactly once, here; nothing but the endpoint and
    the loopback socks port leaves this function (no UUID/password).
    """
    from .. import profiles as profiles_mod
    from ..scout import context as scout_context

    socks: Dict[str, int] = {}
    try:
        for record in profiles_mod.list_profiles(profiles_path):
            name = str(record.get("name") or "")
            port = record.get("socks_port")
            if name and isinstance(port, int) and not isinstance(port, bool):
                socks[name] = port
    except profiles_mod.ProfileStoreError:
        return {}
    candidates, _notes = scout_context.profile_candidates(profiles_path)
    endpoints: Dict[str, Tuple[str, int, Optional[int]]] = {}
    for candidate in candidates:
        name = candidate.profile
        if not isinstance(name, str) or name in endpoints:
            continue
        endpoints[name] = (candidate.host, candidate.port, socks.get(name))
    return endpoints


def _tcp_handshake(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _udp_route(host: str, port: int, timeout: float) -> bool:
    """Route lookup only — **zero bytes** are sent (scout probe contract)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except socket.gaierror:
        return False
    for family, socktype, proto, _canon, sockaddr in infos:
        try:
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout)
                sock.connect(sockaddr)  # route lookup; no datagram emitted
                return True
        except OSError:
            continue
    return False


def _relay_endpoint() -> Optional[Tuple[str, int]]:
    from ..scout import context as scout_context

    hosts = scout_context.relay_hosts()
    return (hosts[0], 443) if hosts else None


def _awg_endpoint() -> Optional[Tuple[str, int]]:
    from ..scout import context as scout_context

    candidates, _notes = scout_context.awg_candidates()
    for candidate in candidates:
        if candidate.route == "direct":
            return (candidate.host, candidate.port)
    return (candidates[0].host, candidates[0].port) if candidates else None


def pre_flight(candidate: Candidate, *,
               tcp_probe: Optional[Callable[[str, int, float], bool]] = None,
               udp_probe: Optional[Callable[[str, int, float], bool]] = None,
               awg_show: Optional[Callable[[], Tuple[int, str, str]]] = None,
               timeout: float = 2.0,
               endpoints: Optional[Mapping[str, Tuple[str, int, Optional[int]]]] = None
               ) -> Tuple[bool, str]:
    """Reachability of the candidate endpoint **before** any switch (≤2 s).

    A profile is checked with a TCP handshake, the relay with a zero-byte UDP
    route lookup, AWG with a route plus a sane ``awg show``.  Any failure is
    ``dead_entry`` — the switch is never attempted in that case.
    """
    tcp = tcp_probe or _tcp_handshake
    udp = udp_probe or _udp_route
    if candidate.kind == "profile":
        table = endpoints if endpoints is not None else profile_endpoints()
        endpoint = table.get(candidate.id)
        if endpoint is None:
            return False, "profile_missing"
        host, port, _socks = endpoint
        if tcp(host, port, timeout):
            return True, "tcp_ok"
        return False, "tcp_no_handshake"
    if candidate.id == "relay":
        endpoint = _relay_endpoint()
        if endpoint is None:
            return False, "relay_endpoint_unknown"
        ok = udp(endpoint[0], endpoint[1], timeout)
        return ok, "udp_route_ok" if ok else "udp_no_route"
    if candidate.id == "asv2-awg":
        endpoint = _awg_endpoint()
        if endpoint is None:
            return False, "awg_endpoint_unknown"
        if not udp(endpoint[0], endpoint[1], timeout):
            return False, "udp_no_route"
        show = awg_show or (lambda: _run_cmd(["sudo", "-n", "awg", "show"]))
        rc, _out, err = show()
        if "Address family not supported" in (err or ""):
            return False, "awg_privilege_error"
        if rc != 0:
            return False, f"awg_show_rc={rc}"
        return True, "awg_ok"
    return False, "candidate_unknown"


def _egress_via_socks(socks_port: Optional[int], run: RunCmd,
                      timeout: float) -> Optional[str]:
    cmd = ["curl", "-s", "--max-time", f"{max(0.5, timeout):.1f}"]
    if socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    cmd.append("https://api.ipify.org")
    rc, out, _err = run(cmd)
    return out.strip() if rc == 0 and out.strip() else None


def _awg_rx() -> Optional[int]:
    try:
        from . import counters
        ifaces, _note = counters.discover_awg_ifaces()
        if not ifaces:
            return None
        sample = counters.read_sample(ifaces[0])
        return sample.rx_bytes if sample is not None else None
    except (OSError, ValueError):
        return None


def post_flight(candidate: Candidate, *,
                egress: Optional[Callable[[Optional[int], float], Optional[str]]] = None,
                awg_rx: Optional[Callable[[], Optional[int]]] = None,
                run: Optional[RunCmd] = None,
                timeout: float = 4.0,
                endpoints: Optional[Mapping[str, Tuple[str, int, Optional[int]]]] = None,
                attempts: int = 1,
                pause_s: float = 1.5,
                sleep: Callable[[float], None] = time.sleep
                ) -> Tuple[bool, str]:
    """Prove the channel end-to-end **after** the switch.

    A scheme rung (``relay`` / ``awg``) pays a cold start: config install,
    interface up, handshake.  A single 4 s probe reported ``egress_empty`` on
    a relay that was healthy (live acceptance), which then cost the preferred
    reserve a 10-minute cooldown.  Scheme rungs therefore get a few attempts.
    """
    for index in range(max(1, attempts)):
        ok, detail = _post_flight_once(candidate, egress=egress, awg_rx=awg_rx,
                                       run=run, timeout=timeout,
                                       endpoints=endpoints)
        if ok:
            return ok, detail
        if index + 1 < max(1, attempts):
            sleep(pause_s)
    return ok, detail


def _post_flight_once(candidate: Candidate, *,
                      egress: Optional[Callable[[Optional[int], float], Optional[str]]] = None,
                      awg_rx: Optional[Callable[[], Optional[int]]] = None,
                      run: Optional[RunCmd] = None,
                      timeout: float = 4.0,
                      endpoints: Optional[Mapping[str, Tuple[str, int, Optional[int]]]] = None
                      ) -> Tuple[bool, str]:
    runner = run or _run_cmd
    probe = egress or (lambda port, budget: _egress_via_socks(port, runner, budget))
    socks: Optional[int] = None
    if candidate.kind == "profile":
        table = endpoints if endpoints is not None else profile_endpoints()
        endpoint = table.get(candidate.id)
        if endpoint is None:
            return False, "profile_missing"
        socks = endpoint[2]
    if candidate.id == "asv2-awg":
        reader = awg_rx or _awg_rx
        before = reader()
        if before is None:
            return False, "awg_counters_unavailable"
        ip = probe(None, timeout)
        after = reader()
        if after is None:
            return False, "awg_counters_unavailable"
        if after <= before:
            return False, "awg_rx_flat"
        return (bool(ip), "awg_rx_growth" if ip else "egress_empty")
    ip = probe(socks, timeout)
    return (bool(ip), "egress_ok" if ip else "egress_empty")


# --- verdict / lockdown ----------------------------------------------------------

def fresh_lockdown(verdict_path: Optional[Path] = None, *,
                   now_wall: Optional[float] = None,
                   fresh_s: int = VERDICT_FRESH_S) -> Optional[bool]:
    """``lockdown.active`` of a **fresh** verdict, else ``None`` (SPEC §3)."""
    from . import verdict as verdict_mod

    payload = verdict_mod.load_verdict(verdict_path)
    if not isinstance(payload, Mapping):
        return None
    age = verdict_mod.age_seconds(payload.get("fetched_at"))
    if age is None or age > fresh_s:
        return None
    context = verdict_mod.context_from_payload(payload)
    return bool(context.lockdown)


def incident_from_findings(findings: Sequence[Mapping[str, Any]], *,
                           profile: Optional[str],
                           expected_lockdown: bool,
                           now_wall: float,
                           lookback_s: int = FINDING_LOOKBACK_S
                           ) -> Dict[str, Any]:
    """Recent findings + derived condition → the ``should_trigger`` payload."""
    recent: List[Dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        window = finding.get("window")
        stamp = None
        if isinstance(window, Mapping):
            stamp = _parse_iso(window.get("last"))
        if stamp is None:
            stamp = _parse_iso(finding.get("ts"))
        if stamp is not None and now_wall - stamp > lookback_s:
            continue
        recent.append({"rule": finding.get("rule"),
                       "severity": finding.get("severity")})
    return {"profile": profile, "findings": recent,
            "expected_lockdown": bool(expected_lockdown)}


# --- notification plumbing -------------------------------------------------------

def _attempts_summary(attempts: Sequence[Attempt]) -> str:
    return ", ".join(f"{item.candidate}: {item.result}" for item in attempts)


def _notify(notifier: Any, *, title: str, body: str, urgency: str,
            telegram_text: str, event: str,
            queue_on_fail: bool = True) -> Dict[str, Any]:
    """Desktop (opt-in) + Telegram; a failed send is queued (§8.2)."""
    result: Dict[str, Any] = {"desktop": False, "telegram": "skipped"}
    if notifier is None:
        return result
    try:
        result["desktop"] = bool(notifier.send_failover(
            title, body, urgency=urgency))
    except (OSError, ValueError, RuntimeError):
        result["desktop"] = False
    try:
        ok, reason = notifier.send_telegram(telegram_text)
    except (OSError, ValueError, RuntimeError) as exc:
        ok, reason = False, f"telegram_exc:{type(exc).__name__}"
    if ok:
        result["telegram"] = "sent"
    else:
        if queue_on_fail:
            store.enqueue_pending_alert(telegram_text, event=event)
            result["telegram"] = "queued"
        else:
            result["telegram"] = "failed"
        result["telegram_reason"] = str(reason)[:120]
    return result


# --- apply state transitions -----------------------------------------------------

def _trim_switches(stamps: Sequence[str], now_wall: float) -> List[str]:
    kept = [item for item in stamps
            if (parsed := _parse_iso(item)) is not None
            and now_wall - parsed <= 3600]
    return kept[-19:]


def _save_state(payload: Dict[str, Any], state_path: Optional[Path],
                config_root: Optional[Path]) -> None:
    store.write_failover_state(payload, state_path, config_root=config_root)


def _history(history_path: Optional[Path], record: Mapping[str, Any]) -> None:
    try:
        store.FailoverHistory(history_path).append(record)
    except (ValueError, OSError):
        # history is observability, not the action: never fail a switch on it
        pass


def _history_record(event: str, now: str, *,
                    from_: Optional[str] = None,
                    to: Optional[str] = None,
                    **extra: Any) -> Dict[str, Any]:
    """Build a ``guard.failover_history/1`` record (``from`` is a keyword)."""
    from . import events

    payload: Dict[str, Any] = {"schema": events.FAILOVER_HISTORY_SCHEMA,
                               "ts": now, "event": event}
    if from_ is not None:
        payload["from"] = from_
    if to is not None:
        payload["to"] = to
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload


# --- main entry point ------------------------------------------------------------

def run_once(*, config_root: Optional[Path] = None,
             state_path: Optional[Path] = None,
             history_path: Optional[Path] = None,
             findings: Optional[Sequence[Mapping[str, Any]]] = None,
             active_profile: Optional[str] = None,
             profiles_path: Optional[Path] = None,
             mono_ns: Callable[[], int] = time.monotonic_ns,
             wall_ts: Callable[[], float] = time.time,
             sleep_fn: Callable[[float], None] = time.sleep,
             local_net: Callable[[], bool] = local_net_check,
             switch_fn: Optional[Callable[[Candidate], int]] = None,
             tcp_probe: Optional[Callable[[str, int, float], bool]] = None,
             udp_probe: Optional[Callable[[str, int, float], bool]] = None,
             awg_show: Optional[Callable[[], Tuple[int, str, str]]] = None,
             egress: Optional[Callable[[Optional[int], float], Optional[str]]] = None,
             awg_rx: Optional[Callable[[], Optional[int]]] = None,
             sync: Optional[Callable[[], bool]] = None,
             xsplit_active: Optional[Callable[[], Optional[str]]] = None,
             teardown_xsplit_fn: Optional[Callable[[], Tuple[bool, str]]] = None,
             verdict_path: Optional[Path] = None,
             notifier: Any = None,
             runner: Optional[RunCmd] = None) -> FailoverOutcome:
    """One failover pass (SPEC §4/§5/§6).  Never raises on a channel fault."""
    cfg = store.failover_settings(config_root)
    state = store.read_failover_state(state_path)
    now_wall = float(wall_ts())
    now_mono = int(mono_ns())
    now_text = _iso(now_wall)
    runner_fn = runner or _run_cmd

    if not cfg.get("enabled"):
        return FailoverOutcome("disabled", TriggerDecision(False, "disabled"))

    # §6: the server lockdown was lifted — inform, do NOT go back.
    lockdown_now = fresh_lockdown(verdict_path, now_wall=now_wall)
    pin = state.get("pin") if isinstance(state.get("pin"), Mapping) else {}
    if pin.get("lockdown_active") and lockdown_now is False:
        pinned = pin.get("profile") if isinstance(pin.get("profile"), str) else "?"
        text = (f"ℹ️ [client] Серверный lockdown на 443/tcp снят. Вы остаётесь "
                f"на профиле {pinned}. Для возврата: vpn switch asv2-reality")
        notified = _notify(notifier, title="VPN: lockdown снят", body=text,
                           urgency="normal", telegram_text=text,
                           event="lockdown_off")
        pin = dict(pin)
        pin["lockdown_active"] = False
        state["pin"] = pin
        state["updated"] = now_text
        _save_state(state, state_path, config_root)
        _history(history_path, _history_record(
            "lockdown_off", now_text, to=pinned if pinned != "?" else None,
            note="lockdown.active=false в свежем verdict; авто-возврата нет"))
        return FailoverOutcome("lockdown_off", TriggerDecision(False, "lockdown_off"),
                               notified=notified)

    # §5.2: backoff window is authoritative.
    backoff = state.get("backoff") if isinstance(state.get("backoff"), Mapping) else {}
    until = _parse_iso(backoff.get("until"))
    if until is not None and until > now_wall:
        return FailoverOutcome("backoff", TriggerDecision(False, "backoff"),
                               backoff_sec=int(until - now_wall))

    profile = active_profile
    source_via_xsplit = False
    if profile is None:
        from .. import runners
        try:
            profile = runners.get_active()
        except (OSError, ValueError):
            profile = None
    if profile is None:
        # The tun scheme (``vpn start reality``) does not register in
        # ``state.json`` — read the running unit instead, otherwise the agent
        # sleeps through exactly the outage it was built for.
        resolver = xsplit_active if xsplit_active is not None \
            else xsplit_active_profile
        try:
            tun_profile = resolver()
        except (OSError, ValueError, RuntimeError):
            tun_profile = None
        if isinstance(tun_profile, str) and tun_profile:
            profile = tun_profile
            source_via_xsplit = True

    if findings is None:
        findings = store.FindingsStore(path=store.findings_path()).read(
            since_ts=now_wall - FINDING_LOOKBACK_S, limit=20)
    incident = incident_from_findings(
        findings, profile=profile, expected_lockdown=bool(lockdown_now),
        now_wall=now_wall)

    activity = {
        "down": store.activity_active_cmd("down", now=now_wall),
        "reset": store.activity_active_cmd("reset", now=now_wall),
    }
    decision = should_trigger(incident, state, activity, config=cfg,
                              now_wall=now_wall, now_mono_ns=now_mono)
    if not decision.trigger:
        return FailoverOutcome("no_trigger", decision)

    # §4.3: the pin suppresses a repeat failover from the same profile.
    if isinstance(pin.get("profile"), str) and pin.get("profile") == profile:
        return FailoverOutcome("pinned", TriggerDecision(False, "pinned", decision.rule))

    # step 0: a broken local uplink is not a VPN fault.
    if not local_net():
        _history(history_path, _history_record(
            "suppressed", now_text, from_=profile, trigger=decision.rule,
            note="suppressed_local_net_down"))
        state["updated"] = now_text
        _save_state(state, state_path, config_root)
        return FailoverOutcome("suppressed_local_net_down", decision)

    # --- ladder ------------------------------------------------------------
    cooldown_map = state.get("candidate_cooldown")
    cooldowns: Dict[str, float] = {}
    if isinstance(cooldown_map, Mapping):
        for key, value in cooldown_map.items():
            parsed = _parse_iso(value)
            if parsed is not None:
                cooldowns[str(key)] = parsed
    candidate_cooldown = float(cfg.get("candidate_cooldown_sec") or 600)
    ladder_ids = cfg.get("ladder") or [item.id for item in LADDER]
    candidates = [LADDER_BY_ID[str(item)] for item in ladder_ids
                  if str(item) in LADDER_BY_ID]
    eligible = [item for item in candidates
                if now_wall - cooldowns.get(item.id, 0.0) >= candidate_cooldown]
    if not eligible:
        eligible = candidates  # all cooling down must not mean "no attempt"

    switch_runner = switch_fn or (
        lambda cand: runner_fn(list(candidate_argv(cand)))[0])
    endpoints = profile_endpoints(profiles_path)

    # SPEC §8.1 "старт": desktop only (the Telegram report is sent once the
    # new channel is proven — there is nothing useful to spam before that).
    if notifier is not None and eligible:
        try:
            notifier.send_failover(
                "⚠️ VPN Guard: прямой канал недоступен",
                f"Профиль {profile} (443/tcp) не отвечает. Переключаю на "
                f"резерв ({eligible[0].id})...",
                urgency="normal")
        except (OSError, ValueError, RuntimeError):
            pass

    attempts: List[Attempt] = []
    torn_xsplit = False
    skipped_ids = {item.id for item in candidates if item not in eligible}
    for candidate in candidates:
        if candidate.id in skipped_ids:
            attempts.append(Attempt(candidate.id, RESULT_SKIPPED,
                                    "candidate_cooldown"))
            continue
        ok, detail = pre_flight(candidate, tcp_probe=tcp_probe,
                                udp_probe=udp_probe, awg_show=awg_show,
                                timeout=float(cfg.get("pre_flight_timeout_sec")
                                              or 2.0),
                                endpoints=endpoints)
        if not ok:
            attempts.append(Attempt(candidate.id, RESULT_DEAD_ENTRY, detail))
            cooldowns[candidate.id] = now_wall
            _history(history_path, _history_record(
                "attempt", now_text, from_=profile, to=candidate.id,
                trigger=decision.rule, result=RESULT_DEAD_ENTRY, note=detail))
            continue

        # The tun scheme must be gone before a socks profile is stacked on
        # top: a dead xray0 would otherwise stay in the routing table over a
        # live reserve.  Scheme rungs handle this themselves (`vpn start
        # relay` / `vpn start` call the direct-contour teardown), so only
        # profile rungs need it here.
        if source_via_xsplit and not torn_xsplit and candidate.kind == "profile":
            torn_xsplit = True
            teardown = teardown_xsplit_fn if teardown_xsplit_fn is not None \
                else teardown_xsplit
            try:
                td_ok, td_detail = teardown()
            except (OSError, ValueError, RuntimeError) as exc:
                td_ok, td_detail = False, f"xsplit_stop_exc:{type(exc).__name__}"
            if not td_ok:
                attempts.append(Attempt(candidate.id, RESULT_SWITCH_ERROR,
                                        f"xsplit_teardown_failed:{td_detail}"))
                _history(history_path, _history_record(
                    "attempt", now_text, from_=profile, to=candidate.id,
                    trigger=decision.rule, result=RESULT_SWITCH_ERROR,
                    note=f"xsplit_teardown_failed:{td_detail}"))
                state["updated"] = now_text
                _save_state(state, state_path, config_root)
                return FailoverOutcome("teardown_failed", decision,
                                       attempts=attempts)
            _history(history_path, _history_record(
                "teardown", now_text, from_=profile, to=candidate.id,
                trigger=decision.rule, result="xsplit_stopped", note=td_detail))

        start = float(wall_ts())
        detail = ""
        try:
            rc = int(switch_runner(candidate))
        except (OSError, ValueError, RuntimeError) as exc:
            rc = 1
            detail = f"switch_exc:{type(exc).__name__}"
        if rc != 0:
            attempts.append(Attempt(candidate.id, RESULT_SWITCH_ERROR, detail))
            _history(history_path, _history_record(
                "attempt", now_text, from_=profile, to=candidate.id,
                trigger=decision.rule, result=RESULT_SWITCH_ERROR))
            continue

        ok, detail = post_flight(
            candidate, egress=egress, awg_rx=awg_rx, run=runner_fn,
            timeout=float(cfg.get("post_flight_timeout_sec") or 4.0),
            endpoints=endpoints,
            # scheme rungs need a cold-start retry (relay handshake/routing)
            attempts=2 if candidate.kind == "scheme" else 1,
            sleep=sleep_fn)
        latency_ms = round((float(wall_ts()) - start) * 1000.0, 1)
        if not ok:
            attempts.append(Attempt(candidate.id, RESULT_DEAD_EXIT, detail,
                                    latency_ms))
            # A scheme rung that failed both probes is unproven, not proven
            # dead: do not burn the preferred reserve's cooldown on it.
            if candidate.kind == "profile":
                cooldowns[candidate.id] = now_wall
            _history(history_path, _history_record(
                "attempt", now_text, from_=profile, to=candidate.id,
                trigger=decision.rule, result=RESULT_DEAD_EXIT, note=detail,
                latency_ms=latency_ms))
            continue

        attempts.append(Attempt(candidate.id, RESULT_OK, detail, latency_ms))
        confirmed = bool(lockdown_now)
        if cfg.get("confirm_lockdown_via_sync") and lockdown_now:
            # One read-only ssh (`vpn guard sync`).  Its failure means "not
            # confirmed", never "channel dead" — the link is already proven.
            try:
                if sync is not None:
                    confirmed = bool(sync())
                else:
                    from . import verdict as verdict_mod
                    result = verdict_mod.sync()
                    confirmed = bool(result.ok and result.context.lockdown)
            except (OSError, ValueError, RuntimeError):
                confirmed = False

        state["pin"] = {"profile": candidate.id, "since": now_text,
                        "reason": decision.rule,
                        "lockdown_active": confirmed}
        state["last_switch_ts"] = now_text
        state["last_switch_mono_ns"] = now_mono
        stamps = state.get("switches") if isinstance(state.get("switches"), list) else []
        state["switches"] = _trim_switches(list(stamps) + [now_text], now_wall)
        state["candidate_cooldown"] = {
            key: _iso(stamp) for key, stamp in cooldowns.items()}
        state["backoff"] = {"count": 0, "until": None}
        state["updated"] = now_text
        _save_state(state, state_path, config_root)

        relay_failed = any(item.candidate == "relay"
                           and item.result in (RESULT_DEAD_ENTRY, RESULT_DEAD_EXIT)
                           for item in attempts)
        notified = _notify_success(notifier, profile=profile, to=candidate.id,
                                   rule=decision.rule, attempts=attempts,
                                   latency_ms=latency_ms,
                                   relay_failed=relay_failed,
                                   lockdown=confirmed)
        # SPEC §8.3: a successful post_flight proves the channel — deliver
        # whatever was queued while it was down.
        if notifier is not None:
            try:
                from . import notify as notify_mod
                notified["flushed"] = notify_mod.flush_pending_alerts(notifier)
            except (OSError, ValueError, RuntimeError):
                pass  # queue flush is best-effort, never a fault
        _history(history_path, _history_record(
            "switch", now_text, from_=profile, to=candidate.id,
            trigger=decision.rule, result=RESULT_OK, latency_ms=latency_ms,
            note=f"attempts: {_attempts_summary(attempts)}",
            attempts=[item.as_dict() for item in attempts]))
        return FailoverOutcome("switched", decision, attempts,
                               switched_to=candidate.id,
                               latency_ms=latency_ms,
                               lockdown_confirmed=confirmed,
                               notified=notified,
                               switch_calls=[a.candidate for a in attempts
                                             if a.result != RESULT_SKIPPED
                                             and a.result != RESULT_DEAD_ENTRY])

    # --- total blackout -----------------------------------------------------
    steps = cfg.get("backoff_sec") or [120, 300, 900]
    count = int((backoff.get("count") or 0)) + 1
    delay = int(steps[min(count - 1, len(steps) - 1)])
    state["backoff"] = {"count": count, "until": _iso(now_wall + delay)}
    state["candidate_cooldown"] = {
        key: _iso(stamp) for key, stamp in cooldowns.items()}
    state["updated"] = now_text
    _save_state(state, state_path, config_root)

    if not cfg.get("kill_switch"):
        try:
            runner_fn(["vpn", "down"])  # return to the standard route
        except (OSError, ValueError, RuntimeError):
            pass
    kill_note = ("kill_switch включён — маршрут оставлен как есть (fail-closed)"
                 if cfg.get("kill_switch") else "возврат на штатный маршрут (vpn down)")
    text = (f"🚨 VPN Guard: ВСЕ резервные каналы недоступны!\n"
            f"Проверены: {_attempts_summary(attempts)}. "
            f"Локальный интернет: ЕСТЬ. Повтор через {delay} с.\n{kill_note}")
    notified = _notify(notifier, title="🚨 VPN Guard: ВСЕ резервные каналы недоступны!",
                       body=f"Проверены: {_attempts_summary(attempts)}. "
                            f"Локальный интернет: ЕСТЬ. Повтор через {delay} с.",
                       urgency="critical", telegram_text=text,
                       event="total_blackout")
    _history(history_path, _history_record(
        "blackout", now_text, from_=profile, trigger=decision.rule,
        result=RESULT_DEAD_EXIT, note=f"backoff={delay}s; {kill_note}",
        attempts=[item.as_dict() for item in attempts]))
    return FailoverOutcome("blackout", decision, attempts,
                           backoff_sec=delay, notified=notified,
                           switch_calls=[a.candidate for a in attempts
                                         if a.result not in (RESULT_SKIPPED,
                                                             RESULT_DEAD_ENTRY)])


def _notify_success(notifier: Any, *, profile: Optional[str], to: str,
                    rule: Optional[str], attempts: Sequence[Attempt],
                    latency_ms: Optional[float], relay_failed: bool,
                    lockdown: bool) -> Dict[str, Any]:
    """SPEC §8.1 templates + the §8.2 client report."""
    hostname = _hostname()
    source = profile or "?"
    if to == "relay":
        title = "✅ VPN переключён на relay"
        body = (f"Канал {source} закрыт защитой сервера (lockdown). "
                f"Связь через relay активна.")
        note = "штатный переход на relay"
    elif relay_failed:
        reason = next((item.result for item in attempts
                       if item.candidate == "relay"), "dead_entry")
        title = f"🟠 VPN переключён на {to} (relay недоступен)"
        body = (f"Канал {source} закрыт. Резерв relay пропущен ({reason}). "
                f"Связь восстановлена через {to}.")
        note = ("⚠️ ВНИМАНИЕ: узел relay недоступен (dead_entry/dead_exit), "
                "проверьте релей!")
    else:
        title = f"✅ VPN переключён на {to}"
        body = f"Канал {source} закрыт. Связь восстановлена через {to}."
        note = "штатный переход"

    chain = _attempts_summary(attempts)
    latency = f"{latency_ms} ms" if latency_ms is not None else "n/a"
    lockdown_status = "lockdown подтверждён" if lockdown else "lockdown не подтверждён"
    telegram_text = "\n".join([
        f"🔄 [client: {hostname}] Авто-переключение VPN",
        f"• Было: {source} (причина: {rule or '?'} / {lockdown_status})",
        f"• Стало: {to} — ONLINE ({latency})",
        f"• Цепочка попыток: {chain}",
        f"• Примечание: {note}",
    ])
    return _notify(notifier, title=title, body=body, urgency="normal",
                   telegram_text=telegram_text, event="failover_switch")


def _hostname() -> str:
    try:
        return socket.gethostname() or "client"
    except OSError:
        return "client"


# --- status ----------------------------------------------------------------------

def status(*, config_root: Optional[Path] = None,
           state_path: Optional[Path] = None,
           verdict_path: Optional[Path] = None,
           wall_ts: Callable[[], float] = time.time) -> Dict[str, Any]:
    """Read-only view for ``vpn guard failover status``."""
    cfg = store.failover_settings(config_root)
    state = store.read_failover_state(state_path)
    now_wall = float(wall_ts())
    backoff = state.get("backoff") if isinstance(state.get("backoff"), Mapping) else {}
    until = _parse_iso(backoff.get("until"))
    return {
        "enabled": bool(cfg.get("enabled")),
        "trigger_profiles": list(cfg.get("trigger_profiles") or []),
        "ladder": list(cfg.get("ladder") or []),
        "pin": dict(state.get("pin") or {}),
        "backoff": {"count": int(backoff.get("count") or 0),
                    "until": backoff.get("until"),
                    "remaining_sec": int(max(0, until - now_wall))
                    if until is not None else 0},
        "last_switch_ts": state.get("last_switch_ts"),
        "switches_last_hour": switches_last_hour(state, now_wall=now_wall),
        "cooldown_sec": cfg.get("cooldown_sec"),
        "lockdown_now": fresh_lockdown(verdict_path, now_wall=now_wall),
        "candidate_cooldown": dict(state.get("candidate_cooldown") or {}),
    }


def reset(*, config_root: Optional[Path] = None,
          state_path: Optional[Path] = None,
          wall_ts: Callable[[], float] = time.time) -> Dict[str, Any]:
    """``vpn guard failover reset``: clear pin, cooldown, backoff (§5.2)."""
    state = store.empty_failover_state()
    state["updated"] = _iso(float(wall_ts()))
    store.write_failover_state(state, state_path, config_root=config_root)
    return state


__all__ = [
    "Attempt",
    "Candidate",
    "FailoverOutcome",
    "LADDER",
    "LADDER_BY_ID",
    "TriggerDecision",
    "fresh_lockdown",
    "incident_from_findings",
    "local_net_check",
    "post_flight",
    "pre_flight",
    "profile_endpoints",
    "reset",
    "run_once",
    "seconds_since_last_switch",
    "should_trigger",
    "status",
    "switches_last_hour",
]
