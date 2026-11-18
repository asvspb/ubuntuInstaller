"""WP-FAILOVER: движок авто-переключения (SPEC-20261118, AC-F1…AC-F11).

Determinism contract of SPEC §14: no ``sleep``, no real sockets.  Clock
(``mono_ns``/``wall_ts``), network (``local_net``, ``tcp_probe``,
``udp_probe``, ``egress``, ``awg_rx``), the switch and the Telegram HTTP are
all injected.  Secret fixtures use the reserved ``FIXTURE_TG_TOKEN_`` prefix.
"""

from __future__ import annotations

import ast
import json
import stat
import sys
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm import runners  # noqa: E402
from vpnm.guard import events, failover, install, notify, redact  # noqa: E402
from vpnm.guard import schema, store  # noqa: E402

GUARD_SRC = Path(__file__).resolve().parents[2] / "vpnm" / "guard"

#: RFC 5737 addresses only (AGENTS §6).
REALITY_HOST = "203.0.113.10"
HY2_HOST = "203.0.113.20"
CDN_HOST = "203.0.113.30"
EGRESS_IP = "198.51.100.7"

#: Reserved fixture — a token *shape* is irrelevant; the FIXTURE_ prefix is
#: what makes it legitimate and detectable.
TG_FIXTURE = "FIXTURE_TG_TOKEN_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_MANAGER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VPN_MANAGER_GUARD_DIR", str(tmp_path / "guard"))
    monkeypatch.setenv("VPN_MANAGER_CONFIG_DIR", str(tmp_path / "config"))


class Clock:
    """Monotonic + wall clock under test control (no sleeps)."""

    def __init__(self, wall: float = 1_800_000_000.0) -> None:
        self.wall = wall
        self.mono_ns = 5_000_000_000

    def wall_ts(self) -> float:
        return self.wall

    def mono(self) -> int:
        return self.mono_ns

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono_ns += int(seconds * 1e9)


class FakeNotifier:
    """Records every channel call; delivery is scripted per channel."""

    def __init__(self, *, desktop: bool = True, telegram: bool = True) -> None:
        self.desktop_ok = desktop
        self.telegram_ok = telegram
        self.desktop: List[Tuple[str, str, str]] = []
        self.telegram: List[str] = []
        self.last_reason: Optional[str] = None

    def send_failover(self, title: str, body: str, *,
                      urgency: str = "normal") -> bool:
        self.desktop.append((title, body, urgency))
        return self.desktop_ok

    def send_telegram(self, text: str) -> Tuple[bool, str]:
        self.telegram.append(text)
        return (self.telegram_ok, "" if self.telegram_ok else "telegram_off")


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def write_config(tmp_path: Path, *, failover_cfg: Optional[Dict[str, Any]] = None,
                 notify_cfg: Optional[Dict[str, Any]] = None) -> Path:
    root = tmp_path / "config"
    root.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {}
    if failover_cfg is not None:
        payload["failover"] = failover_cfg
    if notify_cfg is not None:
        payload["notify"] = notify_cfg
    (root / "guard.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


#: Reality link needs pbk/sid; the key material uses the reserved fixture
#: prefix (AGENTS §6) — nothing here is a plausible real secret.
FIXTURE_PBK = "FIXTURE_PBK_" + "A" * 31
FIXTURE_UUID = "11111111-2222-3333-4444-555555555555"


def _reality_link(host: str, port: int, sni: str) -> str:
    return (f"vless://{FIXTURE_UUID}@{host}:{port}?security=reality"
            f"&sni={sni}&pbk={FIXTURE_PBK}&sid=0123abcd&fp=chrome&type=tcp")


def write_profiles(tmp_path: Path) -> Path:
    """Two socks profiles the ladder knows how to reach."""
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"profiles": [
        {"name": "asv2-hy2", "socks_port": 1081,
         "link": _reality_link(HY2_HOST, 8443, "h.test")},
        {"name": "asv2-cdn", "socks_port": 1082,
         "link": _reality_link(CDN_HOST, 2053, "c.test")},
    ]}), encoding="utf-8")
    return path


def write_verdict(tmp_path: Path, *, active: bool, age_s: int = 30) -> Path:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    path = data / "server-verdict.json"
    path.write_text(json.dumps({
        "schema": "guard.server_verdict/1",
        "fetched_at": _iso(time.time() - age_s),
        "source": "fixture",
        "verdict": "ok",
        "threat_level": "high" if active else "quiet",
        "lockdown": {"active": active, "ttl_until": "", "reason": ""},
    }), encoding="utf-8")
    return path


def finding(rule: str, severity: str, now: float) -> Dict[str, Any]:
    return {"rule": rule, "severity": severity,
            "window": {"first": _iso(now - 30), "last": _iso(now)}}


def enabled_cfg(**overrides: Any) -> Dict[str, Any]:
    cfg = store.failover_settings()
    cfg.update({"enabled": True})
    cfg.update(overrides)
    return cfg


def _run(tmp_path: Path, clock: Clock, *, cfg: Dict[str, Any],
         findings: List[Dict[str, Any]],
         switch: Optional[Dict[str, int]] = None,
         pre: Optional[Dict[str, bool]] = None,
         egress: Any = None,
         local_net: Any = None,
         sync: Any = None,
         notifier: Any = None,
         verdict: Optional[Path] = None,
         profiles: Optional[Path] = None,
         state_path: Optional[Path] = None,
         active_profile: Optional[str] = "asv2-reality",
         xsplit_active: Any = None,
         teardown_xsplit: Any = None,
         order: Optional[List[str]] = None) -> failover.FailoverOutcome:
    def no_shell(cmd: List[str]) -> Tuple[int, str, str]:
        return 0, "", ""  # no real commands in tests

    config_root = write_config(tmp_path, failover_cfg=cfg,
                               notify_cfg={"failover_desktop": True})
    switch_script = switch or {}
    calls: List[str] = []

    def switch_fn(candidate: failover.Candidate) -> int:
        calls.append(candidate.id)
        if order is not None:
            order.append(candidate.id)
        return switch_script.get(candidate.id, 0)

    def teardown_fn() -> Tuple[bool, str]:
        if order is not None:
            order.append("teardown:xsplit")
        if teardown_xsplit is None:
            return True, "xray-split демонтирован (fixture)"
        return teardown_xsplit()

    ok_map = pre or {}

    def tcp_probe(host: str, port: int, timeout: float) -> bool:
        return ok_map.get(host, True)

    def udp_probe(host: str, port: int, timeout: float) -> bool:
        return ok_map.get("udp", True)

    outcome = failover.run_once(
        config_root=config_root,
        state_path=state_path or (tmp_path / "guard" / "failover_state.json"),
        history_path=tmp_path / "guard" / "failover_history.jsonl",
        findings=findings,
        active_profile=active_profile,
        profiles_path=profiles if profiles is not None else write_profiles(tmp_path),
        mono_ns=clock.mono,
        wall_ts=clock.wall_ts,
        local_net=local_net or (lambda: True),
        switch_fn=switch_fn,
        tcp_probe=tcp_probe,
        udp_probe=udp_probe,
        awg_show=lambda: (0, "awg0", ""),
        egress=egress or (lambda port, budget: EGRESS_IP),
        awg_rx=lambda: 100,
        sync=sync,
        verdict_path=verdict,
        notifier=notifier,
        xsplit_active=xsplit_active,
        teardown_xsplit_fn=teardown_fn,
        runner=no_shell,
    )
    assert outcome.switch_calls == calls  # bookkeeping agrees with the mock
    return outcome


# --- AC-F1: guard core stays read-only ---------------------------------------

FORBIDDEN_MODULES = {"socket", "urllib", "http", "requests", "failover"}
FORBIDDEN_CALLS = {"sudo", "systemctl"}


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            names.update(alias.name for alias in node.names)
    return names


def _forbidden_literals_or_calls(tree: ast.AST) -> List[str]:
    hits: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if text in FORBIDDEN_CALLS:
                hits.append(text)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                hits.append(node.func.id)
    return hits


def test_ac_f1_guard_core_is_read_only() -> None:
    """AC-F1: no network/sudo/systemctl/failover in the read-only core.

    ``subprocess`` is checked everywhere **except** ``reader.py``: it spawns
    ``journalctl -f`` by design (SPEC §10.4) and predates this WP; the WP
    invariant is "no *new* network/privileged call", which the module and
    literal checks cover.
    """
    strict = ["parse.py", "window.py", "rules.py", "correlate.py"]
    for name in strict + ["reader.py"]:
        tree = ast.parse((GUARD_SRC / name).read_text(encoding="utf-8"))
        modules = _imported_modules(tree)
        assert not (modules & FORBIDDEN_MODULES), (name, modules)
        assert not _forbidden_literals_or_calls(tree), name
        if name in strict:
            assert "subprocess" not in modules, name


# --- AC-F2: happy path via relay ---------------------------------------------

def test_ac_f2_happy_path_relay(tmp_path: Path) -> None:
    clock = Clock()
    notifier = FakeNotifier()
    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)],
                   sync=lambda: True, notifier=notifier,
                   verdict=write_verdict(tmp_path, active=True))

    assert outcome.status == "switched"
    assert outcome.switched_to == "relay"
    assert outcome.lockdown_confirmed is True
    assert outcome.switch_calls == ["relay"]
    assert outcome.trigger.rule == "G-01"
    # desktop + telegram both delivered
    assert notifier.desktop and notifier.desktop[0][2] == "normal"
    assert notifier.telegram and "[client:" in notifier.telegram[0]
    # state: pin + one switch in the hour
    state = store.read_failover_state(tmp_path / "guard" / "failover_state.json")
    assert state["pin"]["profile"] == "relay"
    assert state["last_switch_ts"]
    history = (tmp_path / "guard" / "failover_history.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert any(json.loads(line)["event"] == "switch" for line in history)


# --- AC-F3: dead_entry skips the switch --------------------------------------

def test_ac_f3_dead_entry_never_switches(tmp_path: Path) -> None:
    clock = Clock()
    notifier = FakeNotifier()
    profiles = write_profiles(tmp_path)
    # relay UDP route dead; hy2 TCP alive
    check: Dict[str, Any] = {"udp": False}

    config_root = write_config(tmp_path, failover_cfg=enabled_cfg())
    calls: List[str] = []

    def switch_fn(candidate: failover.Candidate) -> int:
        calls.append(candidate.id)
        return 0

    outcome = failover.run_once(
        config_root=config_root,
        state_path=tmp_path / "guard" / "failover_state.json",
        history_path=tmp_path / "guard" / "failover_history.jsonl",
        findings=[finding("G-01", "high", clock.wall)],
        active_profile="asv2-reality",
        profiles_path=profiles,
        mono_ns=clock.mono, wall_ts=clock.wall_ts,
        local_net=lambda: True,
        switch_fn=switch_fn,
        tcp_probe=lambda host, port, timeout: True,
        udp_probe=lambda host, port, timeout: check["udp"],
        awg_show=lambda: (0, "", ""),
        egress=lambda port, budget: EGRESS_IP,
        awg_rx=lambda: 1,
        notifier=notifier,
        runner=lambda cmd: (0, "", ""),
    )
    assert outcome.status == "switched"
    assert outcome.switched_to == "asv2-hy2"
    assert "relay" not in calls  # pre_flight failed → switch not called
    relay_attempt = next(item for item in outcome.attempts
                         if item.candidate == "relay")
    assert relay_attempt.result == "dead_entry"
    # the report explicitly warns about the dead relay
    assert any("relay недоступен" in text for text in notifier.telegram)


# --- AC-F4: dead_exit moves on immediately -----------------------------------

def test_ac_f4_dead_exit_moves_to_next(tmp_path: Path) -> None:
    clock = Clock()
    notifier = FakeNotifier()
    answers = iter([None, EGRESS_IP])  # relay: no egress, hy2: ok

    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-09", "medium", clock.wall)],
                   egress=lambda port, budget: next(answers),
                   notifier=notifier)

    assert outcome.status == "switched"
    assert outcome.switched_to == "asv2-hy2"
    assert outcome.switch_calls == ["relay", "asv2-hy2"]
    relay = next(item for item in outcome.attempts if item.candidate == "relay")
    assert relay.result == "dead_exit"
    history = [json.loads(line) for line in
               (tmp_path / "guard" / "failover_history.jsonl")
               .read_text(encoding="utf-8").splitlines()]
    assert any(item["event"] == "attempt" and item["result"] == "dead_exit"
               for item in history)
    # candidate cooldown recorded for the relay
    state = store.read_failover_state(tmp_path / "guard" / "failover_state.json")
    assert "relay" in state["candidate_cooldown"]


# --- AC-F5: total blackout ---------------------------------------------------

def test_ac_f5_blackout_single_pass_backoff(tmp_path: Path) -> None:
    clock = Clock()
    notifier = FakeNotifier(telegram=False)  # forces the queue path
    profiles = write_profiles(tmp_path)
    config_root = write_config(tmp_path, failover_cfg=enabled_cfg())
    calls: List[str] = []

    outcome = failover.run_once(
        config_root=config_root,
        state_path=tmp_path / "guard" / "failover_state.json",
        history_path=tmp_path / "guard" / "failover_history.jsonl",
        findings=[finding("G-01", "high", clock.wall)],
        active_profile="asv2-reality",
        profiles_path=profiles,
        mono_ns=clock.mono, wall_ts=clock.wall_ts,
        local_net=lambda: True,
        switch_fn=lambda candidate: calls.append(candidate.id) or 0,
        tcp_probe=lambda host, port, timeout: False,
        udp_probe=lambda host, port, timeout: False,
        awg_show=lambda: (1, "", "error"),
        egress=lambda port, budget: None,
        awg_rx=lambda: 1,
        notifier=notifier,
        runner=lambda cmd: (0, "", ""),
    )
    assert outcome.status == "blackout"
    assert outcome.backoff_sec == 120
    assert calls == []  # exactly one pass, no switches at all
    assert any(item[2] == "critical" for item in notifier.desktop)
    # Telegram failed → queued record, no leak of anything
    pending = store.read_pending_alerts(tmp_path / "guard" / "pending_alerts.jsonl")
    assert len(pending) == 1 and pending[0]["event"] == "total_blackout"
    state = store.read_failover_state(tmp_path / "guard" / "failover_state.json")
    assert state["backoff"]["count"] == 1
    assert state["backoff"]["until"]


# --- AC-F6: the queue is flushed and cleared ---------------------------------

def test_ac_f6_pending_queue_flushed(tmp_path: Path) -> None:
    path = tmp_path / "guard" / "pending_alerts.jsonl"
    stamp = _iso(time.time() - 60)
    store.PendingAlerts(path).enqueue({
        "schema": events.PENDING_ALERTS_SCHEMA, "ts": stamp,
        "event": "total_blackout", "text": "старое уведомление"})

    notifier = FakeNotifier(telegram=True)
    result = notify.flush_pending_alerts(notifier, path=path)
    assert result == {"sent": 1, "remaining": 0}
    assert notifier.telegram[0].startswith("[Отложенное уведомление от ")
    assert store.read_pending_alerts(path) == []


def test_ac_f6_failed_flush_keeps_record(tmp_path: Path) -> None:
    path = tmp_path / "guard" / "pending_alerts.jsonl"
    store.enqueue_pending_alert("не доставлено", path=path)
    notifier = FakeNotifier(telegram=False)
    result = notify.flush_pending_alerts(notifier, path=path)
    assert result == {"sent": 0, "remaining": 1}
    assert len(store.read_pending_alerts(path)) == 1


# --- AC-F7: local internet down suppresses everything ------------------------

def test_ac_f7_local_net_down_suppresses(tmp_path: Path) -> None:
    clock = Clock()
    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)],
                   local_net=lambda: False)
    assert outcome.status == "suppressed_local_net_down"
    assert outcome.switch_calls == []
    assert outcome.attempts == []


# --- AC-F8: default off + manual stop ----------------------------------------

def test_ac_f8_disabled_by_default(tmp_path: Path) -> None:
    clock = Clock()
    cfg = store.failover_settings()
    assert cfg["enabled"] is False  # invariant I-6
    outcome = _run(tmp_path, clock, cfg=cfg,
                   findings=[finding("G-01", "high", clock.wall)])
    assert outcome.status == "disabled"
    assert outcome.switch_calls == []


def test_ac_f8_activity_down_suppresses(tmp_path: Path) -> None:
    clock = Clock()
    # the marker is a no-op without the guard directory (install creates it)
    (tmp_path / "guard").mkdir(parents=True, exist_ok=True)
    assert store.mark_activity("down", now=clock.wall) is True
    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)])
    assert outcome.status == "no_trigger"
    assert outcome.trigger.reason == "activity_down"
    assert outcome.switch_calls == []


# --- AC-F9: cooldown + lockdown_off without auto-rollback --------------------

def test_ac_f9_cooldown_blocks_repeat(tmp_path: Path) -> None:
    clock = Clock()
    state_path = tmp_path / "guard" / "failover_state.json"
    state = store.empty_failover_state()
    state["last_switch_ts"] = _iso(clock.wall - 60)  # 60 s ago < 900
    state["last_switch_mono_ns"] = None  # simulate a reboot
    store.write_failover_state(state, state_path)

    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)],
                   state_path=state_path)
    assert outcome.status == "no_trigger"
    assert outcome.trigger.reason == "cooldown"
    assert outcome.switch_calls == []


def test_ac_f9_lockdown_off_no_auto_rollback(tmp_path: Path) -> None:
    clock = Clock()
    state_path = tmp_path / "guard" / "failover_state.json"
    state = store.empty_failover_state()
    state["pin"] = {"profile": "relay", "since": _iso(clock.wall - 3600),
                    "reason": "expected_lockdown", "lockdown_active": True}
    store.write_failover_state(state, state_path)
    notifier = FakeNotifier()

    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[],  # no channel event at all
                   verdict=write_verdict(tmp_path, active=False),
                   notifier=notifier, state_path=state_path)

    assert outcome.status == "lockdown_off"
    assert outcome.switch_calls == []  # §6: never goes back by itself
    assert any("lockdown на 443/tcp снят" in text for text in notifier.telegram)
    saved = store.read_failover_state(state_path)
    assert saved["pin"]["lockdown_active"] is False
    assert saved["pin"]["profile"] == "relay"  # stays on the current profile


# --- AC-F10: token never leaks ------------------------------------------------

def _token_file(tmp_path: Path, mode: int = 0o600) -> Path:
    path = tmp_path / "alert-tg.token"
    path.write_text(TG_FIXTURE + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_ac_f10_telegram_error_scrubbed(tmp_path: Path) -> None:
    clock = Clock()
    token_file = _token_file(tmp_path)
    config_root = write_config(tmp_path, notify_cfg={
        "failover_desktop": False,
        "telegram": {"enabled": True, "chat_id": "42",
                     "bot_token_file": str(token_file), "timeout_sec": 4.0},
    })

    seen: Dict[str, Any] = {}

    def poster(url: str, form: bytes, timeout: float) -> Tuple[int, str]:
        seen["url"] = url
        seen["form"] = form.decode("utf-8")
        seen["timeout"] = timeout
        raise urllib.error.URLError(f"cannot reach {url}")

    instance = notify.Notifier(config_root=config_root, http_post=poster)
    ok, reason = instance.send_telegram("текст")
    assert ok is False
    assert TG_FIXTURE not in str(reason)
    assert TG_FIXTURE not in str(instance.last_reason)
    # request contract: chat_id, preview off, configured timeout
    assert "sendMessage" in seen["url"]
    assert "chat_id=42" in seen["form"]
    assert "disable_web_page_preview=true" in seen["form"]
    assert seen["timeout"] == 4.0


def test_ac_f10_queued_text_and_export_are_clean(tmp_path: Path) -> None:
    # a queued line is redacted before it hits the disk
    record = store.enqueue_pending_alert(
        f"token {TG_FIXTURE} в тексте", path=tmp_path / "q.jsonl")
    assert TG_FIXTURE not in record["text"]
    # the export post-scanner catches a fixture token in a finding
    dirty = {"schema": "guard.finding/1", "hypothesis": f"bot {TG_FIXTURE}"}
    assert redact.scan_secrets(dirty)
    code, hits = redact.export_gate([dirty], None)
    assert code == 2 and hits
    # the canonical bot-token shape is masked too
    shaped = "1234567890:" + "A" * 35
    masked, hits_count = redact.redact_raw(f"url {shaped}")
    assert shaped not in masked and hits_count >= 1


def test_ac_f10_token_file_mode_warns_but_reads(tmp_path: Path) -> None:
    token_file = _token_file(tmp_path, mode=0o644)
    config_root = write_config(tmp_path, notify_cfg={
        "telegram": {"enabled": True, "chat_id": "42",
                     "bot_token_file": str(token_file)}})
    instance = notify.Notifier(
        config_root=config_root,
        http_post=lambda url, form, timeout: (200, '{"ok":true}'))
    ok, warning = instance.send_telegram("hi")
    assert ok is True and warning == "token_perms_wide"


# --- AC-F11: units and the vpnm-* sweep --------------------------------------

def test_ac_f11_units_rendered_and_named(tmp_path: Path) -> None:
    service = install.render_failover_service("/usr/bin/python3")
    path_unit = install.render_failover_path(tmp_path / "findings.jsonl")
    assert "ExecStart=/usr/bin/python3 -m vpnm guard failover run --once" in service
    assert "PathChanged=" + str(tmp_path / "findings.jsonl") in path_unit
    assert install.FAILOVER_SERVICE.startswith("vpn-")
    assert not install.FAILOVER_SERVICE.startswith("vpnm-")
    assert not install.FAILOVER_PATH.startswith("vpnm-")

    plan = install.install_failover(apply=True, python_exe="/usr/bin/python3",
                                    unit_root=tmp_path / "units",
                                    data_dir=tmp_path / "data",
                                    run=lambda cmd: (0, "", ""))
    assert (tmp_path / "units" / install.FAILOVER_SERVICE).exists()
    assert (tmp_path / "units" / install.FAILOVER_PATH).exists()
    assert "enable --now vpn-failover.path" in plan["commands"][1]


def test_ac_f11_runners_ignore_vpn_failover() -> None:
    listing = ("vpn-failover.service    enabled\n"
               "vpn-failover.path       enabled\n"
               "vpnm-asv2-hy2.service   enabled\n")

    def fake_run(cmd: List[str]) -> Tuple[int, str, str]:
        return 0, listing, ""

    names = runners.enabled_vpnm_units(fake_run)
    assert names == ["asv2-hy2"]  # the emergency agent is not a profile


def test_ac_f11_selftest_green_with_new_schemas() -> None:
    from vpnm.guard import cli

    ok, checks = cli.run_selftest()
    assert ok is True, checks
    schemas = next(item for item in checks if item["check"] == "schemas")
    assert f"{len(events.SCHEMA_FILES)}/{len(events.SCHEMA_FILES)}" in schemas["detail"]
    hashes = schema.schema_hashes()
    assert set(hashes) >= {events.FAILOVER_STATE_SCHEMA,
                           events.FAILOVER_HISTORY_SCHEMA,
                           events.PENDING_ALERTS_SCHEMA}


# --- config / artifact contract ----------------------------------------------

def test_config_defaults_and_validation(tmp_path: Path) -> None:
    root = write_config(tmp_path, failover_cfg={
        "enabled": True, "cooldown_sec": 5, "max_switches_per_hour": 99,
        "ladder": ["relay", "does-not-exist"], "backoff_sec": [300, 120],
        "pre_flight_timeout_sec": 42,
    })
    cfg = store.failover_settings(root)
    assert cfg["enabled"] is True
    assert cfg["cooldown_sec"] == 900          # 5 is out of range → default
    assert cfg["max_switches_per_hour"] == 3   # 99 out of range → default
    assert cfg["ladder"] == store.DEFAULT_CONFIG["failover"]["ladder"]
    assert cfg["backoff_sec"] == [120, 300, 900]  # non-increasing → default
    assert cfg["pre_flight_timeout_sec"] == 2.0
    # notify.failover_desktop is independent of notify.desktop
    assert store.failover_desktop_enabled(root) is False


def test_artifacts_are_0600_and_bounded(tmp_path: Path) -> None:
    guard = tmp_path / "guard"
    state_path = guard / "failover_state.json"
    store.write_failover_state(store.empty_failover_state(), state_path)
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert state_path.stat().st_size <= store.FAILOVER_STATE_MAX_BYTES

    history = store.FailoverHistory(guard / "failover_history.jsonl")
    history.append({"schema": events.FAILOVER_HISTORY_SCHEMA,
                    "ts": _iso(time.time()), "event": "attempt",
                    "result": "dead_entry"})
    assert stat.S_IMODE((guard / "failover_history.jsonl").stat().st_mode) == 0o600

    queue = store.PendingAlerts(guard / "pending_alerts.jsonl", max_records=3)
    for index in range(5):
        queue.enqueue({"schema": events.PENDING_ALERTS_SCHEMA,
                       "ts": _iso(time.time()), "text": f"m{index}"})
    assert len(queue.records()) == 3
    assert queue.records()[0]["text"] == "m2"  # FIFO: oldest dropped
    assert stat.S_IMODE((guard / "pending_alerts.jsonl").stat().st_mode) == 0o600


# --- regression: a real `vpn down` must KEEP the suppression marker ----------
#
# AC-F8 only used `store.mark_activity` directly, so it could not catch the
# bug where the CLI closed its own marker on a clean exit
# (`mark_activity_end` writes `until = now`) — the 5-minute suppression of
# SPEC §3.2 silently disappeared the moment `vpn down` finished.

def test_cli_down_keeps_activity_window(tmp_path: Path, monkeypatch) -> None:
    from vpnm import amneziawg, cli as cli_mod, mode, runners
    from vpnm.guard import store as gstore

    marker = tmp_path / "guard" / "activity.json"
    marker.parent.mkdir(parents=True)
    monkeypatch.setattr(gstore, "activity_path", lambda: marker)

    # Neutralise the real work: no systemd, no awg, no mode file.
    monkeypatch.setattr(runners, "stop_all_profiles", lambda: ([], []))
    monkeypatch.setattr(cli_mod, "_stop_relay_contour", lambda: (True, "relay off"))
    monkeypatch.setattr(amneziawg, "instance_active", lambda name: False)
    monkeypatch.setattr(mode, "clear_mode", lambda: None)

    cli_mod.down(None)

    payload = json.loads(marker.read_text(encoding="utf-8"))
    started = datetime.fromisoformat(payload["started"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(payload["until"].replace("Z", "+00:00"))
    window = (until - started).total_seconds()
    # `activity_active` uses `until >= now`, so a closed marker (until == now)
    # still reads "active" for that instant — the window length is what proves
    # the suppression survived the command.
    assert window >= 250, (
        f"a clean `vpn down` closed its own marker (window {window:.0f}s) — "
        "failover would re-raise the tunnel the owner just stopped")


# --- xray-split (tun) as the guarded channel ---------------------------------
#
# `vpn start reality` never writes state.json["active"], so a source-only
# check left the agent in profile_not_trigger exactly when the real direct
# channel died.  These tests cover detection and the teardown that must
# precede any socks rung (a dead xray0 must not stay over a live reserve).

def test_xsplit_active_profile_triggers_and_switches(tmp_path: Path) -> None:
    clock = Clock()
    notifier = FakeNotifier()
    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)],
                   active_profile=None,
                   xsplit_active=lambda: "asv2-reality",
                   sync=lambda: True, notifier=notifier,
                   verdict=write_verdict(tmp_path, active=True))

    assert outcome.status == "switched"
    assert outcome.switched_to == "relay"
    # relay is a scheme rung: `vpn start relay` does its own direct teardown
    assert outcome.switch_calls == ["relay"]


def test_no_channel_at_all_is_profile_not_trigger(tmp_path: Path) -> None:
    clock = Clock()
    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)],
                   active_profile=None, xsplit_active=lambda: None)

    assert outcome.status == "no_trigger"
    assert outcome.trigger.reason == "profile_not_trigger"
    assert outcome.switch_calls == []


def test_xsplit_torn_down_before_socks_rung(tmp_path: Path) -> None:
    clock = Clock()
    notifier = FakeNotifier()
    order: List[str] = []
    # relay UDP route dead -> the first live rung is the asv2-hy2 socks profile
    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)],
                   pre={"udp": False},
                   active_profile=None,
                   xsplit_active=lambda: "asv2-reality",
                   sync=lambda: True, notifier=notifier,
                   verdict=write_verdict(tmp_path, active=True),
                   order=order)

    assert outcome.status == "switched"
    assert outcome.switched_to == "asv2-hy2"
    assert order == ["teardown:xsplit", "asv2-hy2"], (
        "the tun scheme must be gone BEFORE the socks profile is stacked on it")
    history = (tmp_path / "guard" / "failover_history.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert any(json.loads(line)["result"] == "xsplit_stopped" for line in history)


def test_xsplit_teardown_failure_aborts_switch(tmp_path: Path) -> None:
    clock = Clock()
    notifier = FakeNotifier()
    order: List[str] = []
    outcome = _run(tmp_path, clock, cfg=enabled_cfg(),
                   findings=[finding("G-01", "high", clock.wall)],
                   pre={"udp": False},
                   active_profile=None,
                   xsplit_active=lambda: "asv2-reality",
                   teardown_xsplit=lambda: (False, "sudo_denied"),
                   sync=lambda: True, notifier=notifier,
                   verdict=write_verdict(tmp_path, active=True),
                   order=order)

    # No second contour may be stacked on a live tun route.
    assert outcome.status == "teardown_failed"
    assert outcome.switch_calls == []
    assert order == ["teardown:xsplit"]
