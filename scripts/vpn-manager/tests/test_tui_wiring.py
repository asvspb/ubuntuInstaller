"""Tests that TUI menu actions call the same CLI-layer functions.

The interactive questionary select itself is not tested; we monkeypatch it to
return a fixed choice and stub the underlying operations to record calls.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import profiles, runners, settings, tui  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#fixture-reality"
)


class FakeQuestionary:
    """Minimal stand-in: select returns queued answers; text likewise."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.errors = type(sys)("types")  # unused, only attribute presence

    class errors:  # namespace mirroring questionary.errors
        class Abort(Exception):
            pass

    def select(self, *args, **kwargs):
        return self._Ask(self.answers)

    def text(self, *args, **kwargs):
        return self._Ask(self.answers)

    class _Ask:
        def __init__(self, answers):
            self.answers = answers

        def ask(self):
            return self.answers.pop(0) if self.answers else None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(runners, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    return tmp_path


def test_menu_connect_invokes_runners_up(env, monkeypatch):
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS, name="p1")
    calls = []
    monkeypatch.setattr(runners, "up", lambda name: calls.append(name))
    monkeypatch.setattr(tui, "questionary", FakeQuestionary(["p1"]))
    tui.action_connect()
    assert calls == ["p1"]


def test_menu_disconnect_invokes_runners_down(env, monkeypatch):
    calls = []
    monkeypatch.setattr(runners, "down", lambda name=None: calls.append(name))
    tui.action_disconnect()
    assert calls == [None]


def test_menu_sysproxy_routes_to_sysproxy_module(env, monkeypatch):
    calls = []
    monkeypatch.setattr(tui.sysproxy, "on",
                        lambda port: calls.append(("on", port)))
    monkeypatch.setattr(tui, "questionary", FakeQuestionary(["on"]))
    runners.set_active("p1")
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                 name="p1", socks_port=2083)
    tui.action_sysproxy()
    assert calls == [("on", 2083)]


def test_menu_doctor_invokes_run_doctor(env, monkeypatch):
    calls = []
    monkeypatch.setattr(tui.doctor, "run_doctor",
                        lambda host: calls.append(host) or ([], "ok"))
    tui.action_doctor()
    assert calls == [None]


def test_menu_pull_invokes_pull_and_saves_host(env, monkeypatch):
    calls = []
    monkeypatch.setattr(tui, "settings", settings)
    from vpnm import pull as pull_mod
    monkeypatch.setattr(pull_mod, "pull",
                        lambda host: calls.append(host) or [("a", "imported")])
    monkeypatch.setattr(tui, "questionary", FakeQuestionary(["root@asv1"]))
    tui.action_profile_pull()
    assert calls == ["root@asv1"]
    assert settings.get_host() == "root@asv1"
