"""WP-G1 install tests: unit profiles, AC-17 fallback, notify-send, name safety.

``systemctl``/``loginctl``/``notify-send`` never run: :class:`FakeRun` records
argument lists and serves scripted responses.  Nothing touches the real unit
directory or the real XDG state — every path is a ``tmp_path``.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm import runners  # noqa: E402
from vpnm.guard import install  # noqa: E402


class FakeRun:
    """Scripted ``(rc, out, err)`` per command; records every call."""

    def __init__(self, script: Optional[Dict[str, Tuple[int, str, str]]] = None,
                 default: Tuple[int, str, str] = (0, "", "")) -> None:
        self.script = script or {}
        self.default = default
        self.calls: List[List[str]] = []

    def __call__(self, cmd: List[str]) -> Tuple[int, str, str]:
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if joined in self.script:
            return self.script[joined]
        return self.default


def _key(*parts: str) -> str:
    return " ".join(parts)


# --- unit rendering ----------------------------------------------------------

def test_hardened_unit_has_full_sandbox() -> None:
    text = install.render_unit("hardened", python_exe="/usr/bin/python3")
    for line in ("ProtectSystem=strict", "SystemCallFilter=@system-service",
                 "SystemCallArchitectures=native",
                 "NoNewPrivileges=yes", "PrivateTmp=yes",
                 "RestrictAddressFamilies=AF_UNIX", "MemoryMax=128M",
                 "CPUQuota=10%", "UMask=0077"):
        assert line in text
    assert "AF_NETLINK" not in text
    assert ("ExecStart=/usr/bin/python3 -m vpnm guard run --foreground --quiet"
            in text)
    assert "WantedBy=default.target" in text


def test_compat_unit_drops_namespace_trio_only() -> None:
    text = install.render_unit("compat", python_exe="/usr/bin/python3")
    assert "ProtectSystem" not in text
    assert "SystemCallFilter" not in text
    assert "SystemCallArchitectures" not in text
    # the safe subset stays
    for line in ("NoNewPrivileges=yes", "PrivateTmp=yes",
                 "RestrictAddressFamilies=AF_UNIX", "MemoryMax=128M",
                 "CPUQuota=10%", "UMask=0077"):
        assert line in text


def test_netlink_adds_af_netlink_only() -> None:
    text = install.render_unit("hardened", netlink=True)
    assert "RestrictAddressFamilies=AF_UNIX AF_NETLINK" in text


def test_unknown_profile_rejected() -> None:
    with pytest.raises(ValueError):
        install.render_unit("relaxed")


def test_unit_name_is_not_a_profile_prefix() -> None:
    # SPEC §10.4 / RSK-11: ``vpnm-*`` would be treated as a profile.
    assert install.UNIT_NAME == "vpn-guard.service"
    assert not install.UNIT_NAME.startswith("vpnm-")


def test_sync_timer_is_hourly() -> None:
    timer = install.render_sync_timer()
    assert "OnUnitActiveSec=1h" in timer
    assert "RandomizedDelaySec=5min" in timer


# --- dry run ----------------------------------------------------------------

def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    runner = FakeRun()
    plan = install.install(apply=False, unit_root=tmp_path / "units",
                           data_dir=tmp_path / "data", run=runner)
    assert plan["apply"] is False
    assert plan["unit_profile"] == "hardened"
    assert not (tmp_path / "units" / install.UNIT_NAME).exists()
    assert runner.calls == []  # no systemctl in dry-run


# --- apply: hardened happy path ---------------------------------------------

def test_apply_records_hardened_profile(tmp_path: Path) -> None:
    runner = FakeRun({_key("systemctl", "--user", "is-active",
                           install.UNIT_NAME): (0, "active\n", "")})
    units, data = tmp_path / "units", tmp_path / "data"
    plan = install.install(apply=True, unit_root=units, data_dir=data,
                           run=runner)
    unit_file = units / install.UNIT_NAME
    assert unit_file.exists()
    assert stat.S_IMODE(unit_file.stat().st_mode) == 0o644
    assert "ProtectSystem=strict" in unit_file.read_text(encoding="utf-8")
    state_file = data / install.INSTALL_STATE
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["profile"] == "hardened"
    assert state["fallback_reason"] is None
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert plan["unit_profile"] == "hardened"
    assert install.load_install_state(data)["profile"] == "hardened"


# --- AC-17: observed 226/NAMESPACE → compat ----------------------------------

def test_namespace_failure_falls_back_to_compat(tmp_path: Path) -> None:
    runner = FakeRun({
        _key("systemctl", "--user", "is-active", install.UNIT_NAME):
            (3, "failed\n", ""),
        _key("systemctl", "--user", "status", install.UNIT_NAME,
             "--no-pager", "--lines=20"):
            (3, "vpn-guard.service: Failed with result 'exit-code'.\n"
                "vpn-guard.service: Main process exited, code=exited, "
                "status=226/NAMESPACE\n", ""),
        _key("systemctl", "--user", "restart", install.UNIT_NAME):
            (0, "", ""),
    })
    units, data = tmp_path / "units", tmp_path / "data"
    plan = install.install(apply=True, unit_root=units, data_dir=data,
                           run=runner)
    unit_file = units / install.UNIT_NAME
    text = unit_file.read_text(encoding="utf-8")
    assert "ProtectSystem" not in text  # compat profile written
    state = json.loads((data / install.INSTALL_STATE).read_text("utf-8"))
    assert state["profile"] == "compat"
    assert "226/namespace" in state["fallback_reason"]
    assert plan["unit_profile"] == "compat"
    # reload+restart attempted for the fallback unit
    assert any(cmd[1:] == ["--user", "restart", install.UNIT_NAME]
               for cmd in runner.calls)


def test_unit_profile_note_is_visible_for_sources() -> None:
    assert install.unit_profile_note(None) is None
    assert install.unit_profile_note({"profile": "hardened"}) == \
        "unit_profile=hardened"
    note = install.unit_profile_note({"profile": "compat",
                                      "fallback_reason":
                                          "hardened start failed: 226"})
    assert note is not None and note.startswith("unit_profile=compat")
    assert "226" in note


def test_start_failure_none_when_active() -> None:
    runner = FakeRun({_key("systemctl", "--user", "is-active",
                           install.UNIT_NAME): (0, "active\n", "")})
    assert install.start_failure(runner) is None


def test_start_failure_detects_exec_and_capabilities() -> None:
    for status_line in ("status=203/EXEC", "status=218/CAPABILITIES"):
        runner = FakeRun({
            _key("systemctl", "--user", "is-active", install.UNIT_NAME):
                (3, "failed\n", ""),
            _key("systemctl", "--user", "status", install.UNIT_NAME,
                 "--no-pager", "--lines=20"): (3, status_line + "\n", ""),
        })
        failure = install.start_failure(runner)
        assert failure is not None and "hardened start failed" in failure


# --- notify-send / D-Bus (SPEC §10.4 item 3) ---------------------------------

def test_notify_available_requires_binary_and_bus(tmp_path: Path,
                                                  monkeypatch) -> None:
    monkeypatch.setattr(install.shutil, "which",
                        lambda name: None if name == install.NOTIFY_BIN
                        else "/usr/bin/" + name)
    assert install.notify_available() == (False, "notify-send не найден")

    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    available, reason = install.notify_available()
    assert available is False and "session bus" in reason

    (tmp_path / "bus").write_text("", encoding="utf-8")
    assert install.notify_available() == (True, "ok")


def test_notify_probe_reports_delivery(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "bus").write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/" + name)

    ok = FakeRun()
    assert install.notify_probe(ok) == "ok"
    assert ok.calls[0][0] == install.NOTIFY_BIN

    failed = FakeRun(default=(1, "", "no server"))
    assert install.notify_probe(failed) == "failed"


def test_desktop_notify_default_off(tmp_path: Path) -> None:
    assert install.desktop_notify_enabled(tmp_path) is False
    (tmp_path / "guard.json").write_text(
        json.dumps({"schema": "guard.config/1",
                    "notify": {"desktop": True}}), encoding="utf-8")
    assert install.desktop_notify_enabled(tmp_path) is True


def test_apply_probes_notify_when_requested(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "bus").write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/" + name)
    config = tmp_path / "config"
    config.mkdir()
    (config / "guard.json").write_text(json.dumps({
        "schema": "guard.config/1",
        "sources": {"system_journal": False},
        "notify": {"desktop": True},
    }), encoding="utf-8")
    runner = FakeRun({_key("systemctl", "--user", "is-active",
                           install.UNIT_NAME): (0, "active\n", "")})
    plan = install.install(apply=True, unit_root=tmp_path / "units",
                           data_dir=tmp_path / "data", config_root=config,
                           run=runner)
    assert plan["desktop_notify"] is True
    assert plan["notify_delivery"] == "ok"
    state = json.loads((tmp_path / "data" / install.INSTALL_STATE)
                       .read_text("utf-8"))
    assert state["notify_delivery"] == "ok"


# --- status / remove --------------------------------------------------------

def test_status_reads_recorded_profile(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / install.INSTALL_STATE).write_text(json.dumps({
        "profile": "compat", "fallback_reason": "226/NAMESPACE",
        "linger": True, "sync_timer": False,
    }), encoding="utf-8")
    units = tmp_path / "units"
    units.mkdir()
    (units / install.UNIT_NAME).write_text("", encoding="utf-8")
    runner = FakeRun({
        _key("systemctl", "--user", "is-active", install.UNIT_NAME):
            (0, "active\n", ""),
        _key("systemctl", "--user", "is-enabled", install.UNIT_NAME):
            (0, "enabled\n", ""),
    })
    info = install.status(unit_root=units, data_dir=data, run=runner)
    assert info["installed"] is True and info["active"] is True
    assert info["profile"] == "compat"
    assert info["linger"] is True
    assert info["unit_profile_note"].startswith("unit_profile=compat")


def test_remove_is_idempotent_and_respects_purge(tmp_path: Path) -> None:
    units, data = tmp_path / "units", tmp_path / "data"
    units.mkdir()
    data.mkdir()
    (units / install.UNIT_NAME).write_text("", encoding="utf-8")
    (data / install.INSTALL_STATE).write_text("{}", encoding="utf-8")
    runner = FakeRun(default=(1, "", "not loaded"))
    plan = install.remove(apply=True, unit_root=units, data_dir=data,
                          run=runner)
    assert plan["warnings"]  # rc!=0 is a warning, not an error
    assert not (units / install.UNIT_NAME).exists()
    assert not (data / install.INSTALL_STATE).exists()
    assert data.exists()  # history kept without --purge

    data.mkdir(exist_ok=True)
    (data / "findings.jsonl").write_text("", encoding="utf-8")
    install.remove(apply=True, purge=True, unit_root=units, data_dir=data,
                   run=runner)
    assert not data.exists()


# --- HMAC key (redact.hash_ip material) -------------------------------------

def test_ensure_hmac_key_creates_0600_once(tmp_path: Path) -> None:
    from vpnm.guard import redact

    data = tmp_path / "data"
    path, created = install.ensure_hmac_key(data, key_text="a" * 64)
    assert created is True
    assert path == data / install.HMAC_KEY_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert redact.load_hmac_key(path) == ("a" * 64).encode()

    again, created_again = install.ensure_hmac_key(data)
    assert created_again is False
    assert again.read_text(encoding="utf-8").strip() == "a" * 64  # stable


def test_ensure_hmac_key_uses_32_random_bytes(tmp_path: Path) -> None:
    from vpnm.guard import redact

    path, created = install.ensure_hmac_key(tmp_path / "data")
    assert created is True
    material = redact.load_hmac_key(path)
    assert material is not None
    assert len(material) == 2 * install.HMAC_KEY_BYTES  # hex of 32 bytes
    int(material.decode(), 16)


def test_install_creates_hmac_key_on_apply(tmp_path: Path) -> None:
    from vpnm.guard import redact

    runner = FakeRun({_key("systemctl", "--user", "is-active",
                           install.UNIT_NAME): (0, "active\n", "")})
    data = tmp_path / "data"
    plan = install.install(apply=True, unit_root=tmp_path / "units",
                           data_dir=data, run=runner)
    key_path = data / install.HMAC_KEY_NAME
    assert key_path.exists()
    assert plan["hmac_key"] == str(key_path)
    assert plan["hmac_key_created"] is True
    assert redact.load_hmac_key(key_path) is not None
    # install-state was also written through the sanitizer
    state = json.loads((data / install.INSTALL_STATE).read_text("utf-8"))
    assert state["profile"] == "hardened"


def test_install_dry_run_does_not_create_hmac_key(tmp_path: Path) -> None:
    data = tmp_path / "data"
    plan = install.install(apply=False, unit_root=tmp_path / "units",
                           data_dir=data, run=FakeRun())
    assert plan["hmac_key_exists"] is False
    assert not (data / install.HMAC_KEY_NAME).exists()


# --- regression: enabled_vpnm_units() ignores the guard unit (RSK-11) --------

def test_enabled_vpnm_units_ignores_vpn_guard(monkeypatch) -> None:
    def run(cmd: List[str]) -> Tuple[int, str, str]:
        assert cmd[:3] == ["systemctl", "--user", "list-unit-files"]
        return 0, ("vpnm-office.service enabled enabled\n"
                   "vpn-guard.service enabled enabled\n"
                   "vpnm-mob.service disabled disabled\n"), ""

    monkeypatch.setattr(runners, "run_cmd", run)
    assert runners.enabled_vpnm_units() == ["office"]
