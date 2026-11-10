"""Tests for the protocol probe (vpnm/probe.py).

All curl/systemctl interactions are mocked (no network, no root). Fixtures
use FIXTURE_* credentials and RFC 5737 addresses per AGENTS.md.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import cli, profiles, probe, runners, tui  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?type=tcp&security=reality&sni=example-test.invalid"
    "&pbk=FIXTURE_PBK_x&sid=01&fp=chrome#fixture-reality"
)
HY2_LINK = "hysteria2://FIXTURE_PW_TestPassword@203.0.113.10:8443#fixture-hy2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    return tmp_path


def _add(name: str, link: str, port: int) -> None:
    profiles.add(parse_link(link), raw_link=link, name=name, socks_port=port)


# --- latency parsing -----------------------------------------------------------


def test_parse_time_total_accepts_number() -> None:
    assert probe.parse_time_total("0.123456\n", "", 0) == 0.123456


def test_parse_time_total_rejects_failure_outputs() -> None:
    assert probe.parse_time_total("", "", 28) is None  # timeout exit code
    assert probe.parse_time_total("000", "", 0) is None  # curl placeholder
    assert probe.parse_time_total("", "", 0) is None  # empty output
    assert probe.parse_time_total("not-a-number", "", 0) is None


def test_median_three_samples() -> None:
    assert probe.median([0.5, 0.1, 0.3]) == pytest.approx(0.3)


def test_median_even_length() -> None:
    assert probe.median([0.4, 0.2]) == pytest.approx(0.3)


class FakeCurl:
    """run_cmd stand-in returning queued curl results in order."""

    def __init__(self, results):
        # results: list of (rc, stdout) tuples consumed per call
        self.results = list(results)
        self.commands: list = []

    def __call__(self, cmd):
        self.commands.append(cmd)
        rc, out = self.results.pop(0) if self.results else (28, "")
        return rc, out, ""


# --- metric / fail semantics ----------------------------------------------------


def test_probe_profile_median_of_three(env, monkeypatch):
    _add("p1", FIXTURE_LINK, 2080)
    fake = FakeCurl([(0, "0.100"), (0, "0.300"), (0, "0.500")])
    monkeypatch.setattr(probe.runners, "is_active", lambda name, run=None: False)
    monkeypatch.setattr(probe.runners, "up", lambda name, run=None: None)
    monkeypatch.setattr(probe.runners, "port_in_use",
                        lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(probe.runners, "run_cmd", fake)
    row = probe.probe_profile("p1")
    assert row["ok"] is True
    assert row["latency_ms"] == pytest.approx(300.0)
    assert len(fake.commands) == 3
    for cmd in fake.commands:
        assert cmd[:2] == ["curl", "--socks5-hostname"]
        assert "127.0.0.1:2080" in cmd
        assert "--max-time" in cmd


def test_probe_profile_any_timeout_fails(env, monkeypatch):
    _add("p1", FIXTURE_LINK, 2080)
    monkeypatch.setattr(probe.runners, "is_active", lambda name, run=None: False)
    monkeypatch.setattr(probe.runners, "up", lambda name, run=None: None)
    monkeypatch.setattr(probe.runners, "port_in_use",
                        lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(probe.runners, "run_cmd",
                        FakeCurl([(0, "0.100"), (28, ""), (0, "0.200")]))
    row = probe.probe_profile("p1")
    assert row["ok"] is False
    assert row["latency_ms"] is None
    assert "timeout" in str(row["error"])


# --- traversal order -------------------------------------------------------------


class FakeRunners:
    """Records up/down/switch calls; mirrors real active-state tracking.

    ``latencies`` maps socks port -> (rc, stdout); default is a fast sample.
    """

    def __init__(self, active="mob", latencies=None):
        self.calls: list = []
        self.active: str = active
        self.units: set = {active} if active else set()
        self.latencies = latencies or {}
        self._current_port = 2080

    def wire(self, monkeypatch, module):
        monkeypatch.setattr(module.runners, "is_active",
                            lambda name, run=None: name in self.units)
        monkeypatch.setattr(module.runners, "get_active",
                            lambda: self.active)
        monkeypatch.setattr(module.runners, "set_active",
                            lambda name: setattr(self, "active", name))
        monkeypatch.setattr(module.runners, "port_in_use",
                            lambda port, host="127.0.0.1": True)

        def fake_up(name, run=None):
            self.calls.append(("up", name))
            record = profiles.get(name)
            self._current_port = int(record["socks_port"])
            self.active = name
            self.units.add(name)
            return Path("/x")

        def fake_down(name=None, run=None):
            self.calls.append(("down", name))
            self.active = None
            self.units.discard(name)
            return name

        def fake_switch(name, run=None):
            self.calls.append(("switch", name))
            self.active = name
            self.units.add(name)
            return Path("/x")

        def fake_run(cmd):
            # derive the port from the curl argument instead of tracking it,
            # so in-place measurements of the active profile work too
            port = 2080
            for arg in cmd:
                if str(arg).startswith("127.0.0.1:"):
                    port = int(str(arg).split(":", 1)[1])
                    break
            rc, out = self.latencies.get(port, (0, "0.150"))
            return rc, out, ""

        monkeypatch.setattr(module.runners, "up", fake_up)
        monkeypatch.setattr(module.runners, "down", fake_down)
        monkeypatch.setattr(module.runners, "switch", fake_switch)
        monkeypatch.setattr(module.runners, "run_cmd", fake_run)


def test_traversal_order_and_intermediate_down(env, monkeypatch):
    _add("phone", FIXTURE_LINK, 2080)
    _add("mob", HY2_LINK, 2083)
    _add("xhttp", FIXTURE_LINK, 2081)
    fake = FakeRunners()
    fake.wire(monkeypatch, probe)
    probe.run_probe(sleep_fn=lambda _s: None)
    order = [name for kind, name in fake.calls if kind == "up"]
    assert order == ["phone", "xhttp"]  # mob already active: measured in place
    downs = [name for kind, name in fake.calls if kind == "down"]
    assert "phone" in downs and "xhttp" in downs


def test_active_profile_measured_without_restart(env, monkeypatch):
    _add("mob", HY2_LINK, 2083)
    fake = FakeRunners()
    fake.active = "mob"
    fake.wire(monkeypatch, probe)
    outcome = probe.run_probe(sleep_fn=lambda _s: None)
    assert fake.calls == []  # no up/down/switch at all
    assert outcome["rows"][0]["ok"] is True
    assert outcome["rows"][0]["already_active"] is True


# --- fastest selection / restore -------------------------------------------------


def test_fastest_picked_from_ok_when_one_fails(env, monkeypatch):
    _add("phone", FIXTURE_LINK, 2080)
    _add("mob", HY2_LINK, 2083)
    responses = {
        2080: (0, "0.100"),   # phone: fast but every sample ok
        2083: (28, ""),       # mob: timeout -> fail
    }
    monkeypatch.setattr(probe.runners, "is_active", lambda n, run=None: False)
    monkeypatch.setattr(probe.runners, "get_active", lambda: None)

    seen = {}

    def fake_up(name, run=None):
        seen["port"] = int(profiles.get(name)["socks_port"])
        return Path("/x")

    monkeypatch.setattr(probe.runners, "up", fake_up)
    monkeypatch.setattr(probe.runners, "down", lambda n=None, run=None: n)
    monkeypatch.setattr(probe.runners, "switch",
                        lambda n, run=None: Path("/x"))

    def fake_run(cmd):
        port = seen.get("port")
        rc, out = responses.get(port, (0, "0.999"))
        return rc, out, ""

    monkeypatch.setattr(probe.runners, "run_cmd", fake_run)
    outcome = probe.run_probe(sleep_fn=lambda _s: None)
    assert outcome["fastest"] == "phone"
    assert outcome["switched_to"] == "phone"
    statuses = {r["name"]: r["ok"] for r in outcome["rows"]}
    assert statuses == {"phone": True, "mob": False}


def test_no_switch_restores_previous_active(env, monkeypatch):
    _add("phone", FIXTURE_LINK, 2080)
    _add("mob", HY2_LINK, 2083)
    fake = FakeRunners(active="mob", latencies={2083: (0, "0.900")})
    fake.wire(monkeypatch, probe)
    outcome = probe.run_probe(switch_best=False, sleep_fn=lambda _s: None)
    assert outcome["fastest"] == "phone"  # phone is fastest
    assert outcome["switched_to"] is None
    assert fake.active == "mob"  # state restored to the initially active one
    assert ("switch", "phone") not in fake.calls
    # phone was brought up only for measuring, then stopped
    assert ("up", "phone") in fake.calls and ("down", "phone") in fake.calls


def test_no_switch_restores_when_fastest_differs(env, monkeypatch):
    _add("phone", FIXTURE_LINK, 2080)
    _add("mob", HY2_LINK, 2083)
    fake = FakeRunners(active="mob", latencies={2083: (0, "0.900")})
    fake.wire(monkeypatch, probe)
    outcome = probe.run_probe(switch_best=False, sleep_fn=lambda _s: None)
    assert outcome["fastest"] == "phone"
    assert outcome["switched_to"] is None
    # mob's unit never stopped: restore only fixes the state.json tracking
    assert fake.units == {"mob"} and fake.active == "mob"


def test_all_fail_restores_previous_and_reports(env, monkeypatch):
    _add("phone", FIXTURE_LINK, 2080)
    fake = FakeRunners()
    fake.active = "phone"
    fake.wire(monkeypatch, probe)
    monkeypatch.setattr(probe.runners, "run_cmd",
                        lambda cmd: (28, "", ""))
    outcome = probe.run_probe(sleep_fn=lambda _s: None)
    assert outcome["fastest"] is None
    assert outcome["switched_to"] is None
    assert all(not r["ok"] for r in outcome["rows"])


def test_all_fail_cli_exit_code_one(env, monkeypatch):
    from typer.testing import CliRunner
    _add("phone", FIXTURE_LINK, 2080)
    fake = FakeRunners()
    fake.active = "phone"
    fake.wire(monkeypatch, probe)
    monkeypatch.setattr(probe.runners, "run_cmd",
                        lambda cmd: (28, "", ""))
    monkeypatch.setattr(cli, "_print_channels_section", lambda: None)
    result = CliRunner().invoke(cli.app, ["probe"])
    assert result.exit_code == 1


# --- shared-port conflicts -------------------------------------------------------


def test_shared_port_other_unit_stopped_between_profiles(env, monkeypatch):
    """phone and xhttp share port 2080; phone is the active profile.

    phone is measured in place (no restart), then before xhttp comes up
    phone's unit must be stopped exactly once — otherwise xhttp's up()
    would fail with 'port in use'.
    """
    _add("phone", FIXTURE_LINK, 2080)
    _add("xhttp", FIXTURE_LINK, 2080)
    fake = FakeRunners(active="phone", latencies={2080: (0, "0.100")})
    fake.wire(monkeypatch, probe)
    outcome = probe.run_probe(sleep_fn=lambda _s: None)
    downs = [name for kind, name in fake.calls if kind == "down"]
    ups = [name for kind, name in fake.calls if kind == "up"]
    assert ups == ["xhttp"]
    assert downs == ["phone", "xhttp"]
    # exactly one down between phone's measurement and xhttp's up: phone's
    # unit stopped, then xhttp brought up — no 'port in use' error
    assert fake.calls.index(("down", "phone")) < fake.calls.index(("up", "xhttp"))
    statuses = {r["name"]: (r["ok"], r["error"]) for r in outcome["rows"]}
    assert statuses == {"phone": (True, ""), "xhttp": (True, "")}


def test_fail_profile_leaves_no_unit_running(env, monkeypatch):
    """A profile that fails its measurements is still stopped afterwards."""
    _add("phone", FIXTURE_LINK, 2080)
    _add("xhttp", FIXTURE_LINK, 2080)
    fake = FakeRunners(active="phone",
                       latencies={2080: (28, "")})  # every curl sample times out
    fake.wire(monkeypatch, probe)
    outcome = probe.run_probe(sleep_fn=lambda _s: None)
    assert all(not r["ok"] for r in outcome["rows"])
    # phone measured in place, then stopped before xhttp's turn; xhttp
    # brought up for measuring, then stopped despite failing
    downs = [name for kind, name in fake.calls if kind == "down"]
    assert downs.count("phone") == 1
    assert "xhttp" in downs
    assert fake.units == set() or fake.units == {"phone"}  # no stray unit


# --- TUI wiring -------------------------------------------------------------------


class FakeQuestionary:
    def __init__(self, answers):
        self.answers = list(answers)

    def select(self, *args, **kwargs):
        return self

    def ask(self):
        return self.answers.pop(0) if self.answers else None


def test_tui_menu_has_probe_entry() -> None:
    assert "Опрос протоколов / выбрать быстрейший" in tui.MENU_ACTIONS
    assert callable(tui.MENU_ACTIONS["Опрос протоколов / выбрать быстрейший"])


def test_tui_probe_action_switches_on_yes(env, monkeypatch):
    _add("phone", FIXTURE_LINK, 2080)
    _add("mob", HY2_LINK, 2083)
    fake = FakeRunners(active="mob", latencies={2083: (0, "0.900")})
    fake.wire(monkeypatch, probe)  # probe and tui share the runners module
    monkeypatch.setattr(tui, "questionary", FakeQuestionary(["yes"]))
    monkeypatch.setattr(cli, "_print_channels_section", lambda: None)
    tui.action_probe()
    assert ("switch", "phone") in fake.calls


def test_tui_probe_action_no_restores(env, monkeypatch):
    _add("phone", FIXTURE_LINK, 2080)
    _add("mob", HY2_LINK, 2083)
    fake = FakeRunners(active="mob", latencies={2083: (0, "0.900")})
    fake.wire(monkeypatch, probe)
    monkeypatch.setattr(tui, "questionary", FakeQuestionary(["no"]))
    monkeypatch.setattr(cli, "_print_channels_section", lambda: None)
    tui.action_probe()
    assert ("switch", "phone") not in fake.calls
    # mob's unit never stopped: restore only fixes the tracking
    assert fake.units == {"mob"} and fake.active == "mob"

# --- channels section + AWG tun latency ----------------------------------------


def _stub_channels_section(monkeypatch, rows, latency):
    from vpnm import channels
    monkeypatch.setattr(channels, "check_all", lambda run=None: rows)
    monkeypatch.setattr(channels, "awg_tunnel_latency",
                        lambda run=None, samples=3: latency)


def test_channels_section_renders_awg_ok_line(env, monkeypatch, capsys):
    """probe prints the channels header + matrix + awg (tun) latency line."""
    rows = [
        {"channel": "vpnm", "status": "ok", "detail": "d1"},
        {"channel": "zerotier", "status": "not_installed", "detail": "d2"},
        {"channel": "amneziawg", "status": "ok", "detail": "awg0"},
        {"channel": "zapret", "status": "warn", "detail": "d3"},
    ]
    _stub_channels_section(monkeypatch, rows, latency=42.0)
    cli._print_channels_section()
    out = capsys.readouterr().out
    assert "── Каналы (vpn/zapret/zerotier/amneziawg) ──" in out
    assert "amneziawg" in out
    assert "awg (tun): 42.0 ms" in out


def test_channels_section_awg_down_reports_not_raised(env, monkeypatch, capsys):
    """When the amneziawg channel is not ok, the section says 'не поднят'."""
    rows = [
        {"channel": "vpnm", "status": "ok", "detail": "d1"},
        {"channel": "zerotier", "status": "not_installed", "detail": "d2"},
        {"channel": "amneziawg", "status": "not_installed", "detail": "d"},
        {"channel": "zapret", "status": "fail", "detail": "d3"},
    ]
    _stub_channels_section(monkeypatch, rows, latency=None)
    cli._print_channels_section()
    out = capsys.readouterr().out
    assert "awg (tun): не поднят" in out


def test_resolve_youtube_ip_parses_doh_answer(monkeypatch):
    import json as json_mod
    from vpnm import channels
    doh = json_mod.dumps({"Answer": [
        {"name": "www.youtube.com", "type": 5, "data": "youtube.com."},
        {"name": "youtube.com.", "type": 1, "data": "203.0.113.99"},
    ]})
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return 0, doh, ""

    assert channels.resolve_youtube_ip(fake_run) == "203.0.113.99"
    cmd = calls[0]
    assert cmd[0] == "curl" and "--noproxy" in cmd
    assert "dns.google" in " ".join(cmd)


def test_resolve_youtube_ip_none_on_failure(monkeypatch):
    import json as json_mod
    from vpnm import channels
    assert channels.resolve_youtube_ip(lambda cmd: (28, "", "")) is None
    assert channels.resolve_youtube_ip(lambda cmd: (0, "not-json", "")) is None
    empty = json_mod.dumps({})
    assert channels.resolve_youtube_ip(lambda cmd: (0, empty, "")) is None


def test_awg_tunnel_latency_median_of_three(monkeypatch):
    from vpnm import channels
    monkeypatch.setattr(channels, "check_awg", lambda run=None: {
        "channel": "amneziawg", "status": channels.STATUS_OK, "detail": ""})
    monkeypatch.setattr(channels, "resolve_youtube_ip",
                        lambda run=None: "203.0.113.99")
    samples = iter([(0, "0.100", ""), (0, "0.500", ""), (0, "0.300", "")])

    def fake_run(cmd):
        assert "--resolve" in cmd
        assert "www.youtube.com:443:203.0.113.99" in cmd
        assert "--noproxy" in cmd
        return next(samples)

    assert channels.awg_tunnel_latency(run=fake_run) == 300.0


def test_awg_tunnel_latency_iface_down_returns_none(monkeypatch):
    from vpnm import channels
    monkeypatch.setattr(channels, "check_awg", lambda run=None: {
        "channel": "amneziawg", "status": channels.STATUS_NOT_INSTALLED,
        "detail": ""})
    assert channels.awg_tunnel_latency(run=lambda cmd: (0, "", "")) is None


def test_awg_tunnel_latency_any_failed_sample_is_none(monkeypatch):
    from vpnm import channels
    monkeypatch.setattr(channels, "check_awg", lambda run=None: {
        "channel": "amneziawg", "status": channels.STATUS_OK, "detail": ""})
    monkeypatch.setattr(channels, "resolve_youtube_ip",
                        lambda run=None: "203.0.113.99")
    samples = iter([(0, "0.100", ""), (28, "", ""), (0, "0.300", "")])
    assert channels.awg_tunnel_latency(
        run=lambda cmd: next(samples)) is None


def test_probe_cli_includes_channels_section(env, monkeypatch):
    """vpn probe renders the channels matrix after the socks profile table."""
    from typer.testing import CliRunner
    from vpnm import channels
    _add("phone", FIXTURE_LINK, 2080)
    fake = FakeRunners(active="phone", latencies={2080: (0, "0.100")})
    fake.wire(monkeypatch, probe)
    monkeypatch.setattr(channels, "check_all", lambda run=None: [
        {"channel": "vpnm", "status": "ok", "detail": "d"},
        {"channel": "zerotier", "status": "not_installed", "detail": "d"},
        {"channel": "amneziawg", "status": "not_installed", "detail": "d"},
        {"channel": "zapret", "status": "ok", "detail": "d"},
    ])
    monkeypatch.setattr(channels, "awg_tunnel_latency",
                        lambda run=None, samples=3: None)
    result = CliRunner().invoke(cli.app, ["probe", "--no-switch"])
    assert result.exit_code == 0
    assert "── Каналы (vpn/zapret/zerotier/amneziawg) ──" in result.output
    assert "awg (tun): не поднят" in result.output

