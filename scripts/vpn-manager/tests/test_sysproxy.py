"""Tests for sysproxy: gsettings calls are recorded, never executed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import sysproxy  # noqa: E402


def recorder():
    """A run stub that records gsettings commands and serves canned gets."""
    commands = []

    def run(cmd):
        commands.append(list(cmd))
        if cmd[0] == "gsettings" and cmd[1] == "get":
            schema, key = cmd[2], cmd[3]
            canned = {(sysproxy.SCHEMA, "mode"): "manual",
                      (sysproxy.SCHEMA_SOCKS, "host"): "127.0.0.1",
                      (sysproxy.SCHEMA_SOCKS, "port"): "2080"}
            return 0, canned.get((schema, key), "") + "\n", ""
        return 0, "", ""

    run.commands = commands  # type: ignore[attr-defined]
    return run


def sets(rec, schema, key):
    return [c for c in rec.commands if c[:4] == ["gsettings", "set", schema, key]]


def test_on_sets_mode_host_and_port():
    rec = recorder()
    sysproxy.on(2083, run=rec)
    assert sets(rec, sysproxy.SCHEMA, "mode")[-1][4] == "manual"
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "host")[-1][4] == "127.0.0.1"
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "port")[-1][4] == "2083"


def test_off_sets_mode_none_only():
    rec = recorder()
    sysproxy.off(run=rec)
    assert sets(rec, sysproxy.SCHEMA, "mode")[-1][4] == "none"
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "host") == []
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "port") == []


def test_status_reads_three_values():
    rec = recorder()
    mode, host, port = sysproxy.status(run=rec)
    assert (mode, host, port) == ("manual", "127.0.0.1", "2080")
    assert len([c for c in rec.commands if c[1] == "get"]) == 3


def test_missing_gsettings_raises_readable_error():
    def run(cmd):
        return 127, "", "FileNotFoundError: gsettings"

    with pytest.raises(sysproxy.SysproxyError, match="not look like GNOME"):
        sysproxy.off(run=run)


def test_failing_gsettings_raises():
    def run(cmd):
        return 1, "", "some dconf error"

    with pytest.raises(sysproxy.SysproxyError, match="dconf error"):
        sysproxy.on(2080, run=run)
