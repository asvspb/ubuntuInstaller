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


def resets(rec, schema, key):
    return [c for c in rec.commands if c[:4] == ["gsettings", "reset", schema, key]]


def test_on_sets_mode_host_and_port():
    rec = recorder()
    sysproxy.on(2083, run=rec)
    assert sets(rec, sysproxy.SCHEMA, "mode")[-1][4] == "manual"
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "host")[-1][4] == "127.0.0.1"
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "port")[-1][4] == "2083"


def test_on_resets_http_https_ftp_before_setting_socks():
    rec = recorder()
    sysproxy.on(2083, run=rec)
    for schema in ("org.gnome.system.proxy.http",
                   "org.gnome.system.proxy.https",
                   "org.gnome.system.proxy.ftp"):
        assert len(resets(rec, schema, "host")) == 1, schema
        assert len(resets(rec, schema, "port")) == 1, schema
    # socks keys themselves are set, never reset
    assert len(sets(rec, sysproxy.SCHEMA_SOCKS, "host")) == 1
    assert len(sets(rec, sysproxy.SCHEMA_SOCKS, "port")) == 1


def test_on_reset_failure_is_warn_not_fatal():
    rec = recorder()
    warned = []

    def run(cmd):
        rc, out, err = rec(cmd)
        if cmd[1] == "reset":
            return 1, "", "reset boom"
        return rc, out, err

    sysproxy.on(2083, run=run, warn=warned.append)
    assert warned == [f"gsettings reset {s}.{k} skipped: reset boom"
                      for s in ("org.gnome.system.proxy.http",
                                "org.gnome.system.proxy.https",
                                "org.gnome.system.proxy.ftp")
                      for k in ("host", "port")]
    # the socks-only configuration is still fully applied
    assert sets(rec, sysproxy.SCHEMA, "mode")[-1][4] == "manual"
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "port")[-1][4] == "2083"


def test_off_sets_mode_none_and_resets_http_https_ftp():
    rec = recorder()
    sysproxy.off(run=rec)
    assert sets(rec, sysproxy.SCHEMA, "mode")[-1][4] == "none"
    for schema in ("org.gnome.system.proxy.http",
                   "org.gnome.system.proxy.https",
                   "org.gnome.system.proxy.ftp"):
        assert len(resets(rec, schema, "host")) == 1, schema
        assert len(resets(rec, schema, "port")) == 1, schema
    # socks keys are never touched (existing invariant)
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "host") == []
    assert sets(rec, sysproxy.SCHEMA_SOCKS, "port") == []


def test_off_reset_failure_is_warn_not_fatal():
    warned = []

    def run(cmd):
        if cmd[1] == "reset":
            return 1, "", "reset boom"
        return 0, "", ""

    sysproxy.off(run=run, warn=warned.append)
    # 6 http/https/ftp keys + 2 socks keys (host, port)
    assert len(warned) == 8


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


def test_off_resets_socks_keys_too():
    rec = recorder()
    """Regression 2026-11-11: off() left socks host/port set -> half-configured
    proxy (values present, mode none) that looked enabled but routed direct."""
    import vpnm.sysproxy as sp
    sp.off(run=rec)
    assert len([c for c in rec.commands
                if c[:2] == ["gsettings", "reset"] and c[2] == sp.SCHEMA_SOCKS
                and c[3] in ("host", "port")]) == 2
    assert rec.commands[-1][:4] == ["gsettings", "set", sp.SCHEMA, "mode"]


def test_on_merges_canonical_ignore_hosts():
    """sysproxy.on(): GLM-кластер и RU попадают в ignore-hosts идемпотентно,
    пользовательские записи сохраняются, повторный вызов ничего не меняет."""
    calls = []
    def run(cmd):
        calls.append(cmd)
        joined = " ".join(cmd)
        if joined == "gsettings get org.gnome.system.proxy ignore-hosts":
            return 0, "['localhost', 'mycustom.example']", ""
        if joined.startswith("gsettings set org.gnome.system.proxy ignore-hosts"):
            return 0, "", ""
        return 0, "", ""
    sysproxy.on(2083, run=run)
    set_cmds = [c for c in calls
                if "ignore-hosts" in c and c[:2] == ["gsettings", "set"]]
    assert len(set_cmds) == 1
    rendered = set_cmds[0][-1]
    assert "'z.ai'" in rendered and "'bigmodel.cn'" in rendered
    assert "'pypi.org'" in rendered and "'ya.ru'" in rendered
    assert "'mycustom.example'" in rendered  # пользовательское сохранено
    # идемпотентность: второй on() не пишет ignore-hosts
    calls.clear()
    def run2(cmd):
        joined = " ".join(cmd)
        if joined == "gsettings get org.gnome.system.proxy ignore-hosts":
            cur = sysproxy.IGNORE_HOSTS_CANONICAL + ["mycustom.example"]
            return 0, "[" + ", ".join(f"'{d}'" for d in cur) + "]", ""
        return 0, "", ""
    sysproxy.on(2083, run=run2)
    assert not [c for c in calls
                if "ignore-hosts" in c and c[:2] == ["gsettings", "set"]]
