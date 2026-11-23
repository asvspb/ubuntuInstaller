"""WP-QR5: vpn sync (read-only pull) — SPEC-20261118-vpn-qr v1.1 §9.1/§9.3.

Everything network-related is mocked (``run`` stub, RunCmd signature);
``~/.awg``, the profiles store and settings.json point into ``tmp_path``.
AC-QR-11: vpn sync performs only read-only ssh/ls/scp, never deletes local
files, and reports imported/updated/skipped/stale.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import amneziawg, profiles, pull, settings, sync  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

EXIT_HOST = "203.0.113.10"          # RFC 5737 (exit)
RELAY_HOST = "198.51.100.10"        # RFC 5737 (relay)

FIXTURE_CONF_V1 = (
    "[Interface]\n"
    "Address = 10.10.0.2/32\n"
    "PrivateKey = FIXTURE_PW_CLIENT_AWG\n"
    "\n"
    "[Peer]\n"
    "PublicKey = FIXTURE_PW_SERVER_AWG\n"
    "Endpoint = 203.0.113.10:51820\n"
    "AllowedIPs = 0.0.0.0/0\n"
)
FIXTURE_CONF_V2 = FIXTURE_CONF_V1.replace("Address = 10.10.0.2/32",
                                          "Address = 10.10.0.3/32")

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#srv-reality"
)
LINKS_OUTPUT = f"srv-reality: {FIXTURE_VLESS}\n"


def make_run(remote: Dict[str, object]) -> Tuple[object, List[List[str]]]:
    """Run stub: ssh ls / ssh vds gateway links / scp — all read-only on server."""
    calls: List[List[str]] = []

    def run(cmd: List[str]) -> Tuple[int, str, str]:
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if cmd[0] == "ssh" and "gateway links" in joined:
            return 0, str(remote.get("links", "")), ""
        if cmd[0] == "ssh" and "ls" in joined and "-1" in cmd:
            for key, listing in remote.get("ls", {}).items():
                if key in cmd:
                    return 0, str(listing), ""
            return 0, "", ""
        if cmd[0] == "scp":
            src = cmd[-2]
            dest = Path(cmd[-1])
            body = ""
            for key, files in remote.get("files", {}).items():
                if key in src:
                    body = str(files)
                    break
            dest.write_text(body, encoding="utf-8")
            return 0, "", ""
        raise AssertionError(f"unexpected command in sync: {cmd}")

    return run, calls


@pytest.fixture
def env(tmp_path, monkeypatch):
    awg_dir = tmp_path / "awg"
    awg_dir.mkdir()
    monkeypatch.setattr(amneziawg, "LOCAL_CONF_DIR", awg_dir)
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    return tmp_path


# --- settings: relay_ssh_host -------------------------------------------------

def test_relay_host_roundtrip_and_backward_compatibility(env):
    assert settings.get_host() is None
    assert settings.get_relay_host() is None
    settings.set_host(EXIT_HOST)
    settings.set_relay_host(RELAY_HOST)
    assert settings.get_host() == EXIT_HOST
    assert settings.get_relay_host() == RELAY_HOST
    raw = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert raw["ssh_host"] == EXIT_HOST
    assert raw["relay_ssh_host"] == RELAY_HOST
    assert (os.stat(settings.SETTINGS_PATH).st_mode & 0o777) == 0o600


# --- list_remote_confs ---------------------------------------------------------

def test_list_remote_confs_parses_ls_output(env):
    run, calls = make_run({"ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\nzz.conf\n"}})
    assert sync.list_remote_confs(EXIT_HOST, sync.AWG_CLIENTS_DIR, run) == [
        "mobile", "zz"]
    assert len(calls) == 1
    assert calls[0] == ["ssh", EXIT_HOST, "ls", "-1", sync.AWG_CLIENTS_DIR]


def test_list_remote_confs_failure_raises_sync_error(env):
    run, _ = make_run({})
    assert sync.list_remote_confs(EXIT_HOST, sync.AWG_CLIENTS_DIR, run) == []
    with pytest.raises(sync.SyncError):
        sync.list_remote_confs(RELAY_HOST, sync.RELAY_CLIENTS_DIR,
                               lambda cmd: (255, "", "unreachable"))


def test_list_remote_confs_rejects_path_like_entries(env):
    with pytest.raises(sync.SyncError):
        sync.list_remote_confs(
            EXIT_HOST, sync.AWG_CLIENTS_DIR,
            lambda cmd: (0, "mobile.conf\n../evil.conf\n", ""))


# --- sync_confs ----------------------------------------------------------------

def test_sync_confs_imports_new_file_0600(env):
    run, calls = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V1},
    })
    report = sync.sync_confs("direct", EXIT_HOST, sync.AWG_CLIENTS_DIR,
                             run=run)
    assert report["imported"] == ["mobile"]
    dest = env / "awg" / "direct" / "mobile.conf"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == FIXTURE_CONF_V1
    assert (os.stat(dest).st_mode & 0o777) == 0o600
    assert (os.stat(env / "awg" / "direct").st_mode & 0o777) == 0o700
    for cmd in calls:
        assert cmd[0] in ("ssh", "scp")


def test_sync_confs_second_run_skips_and_update_flow(env):
    run, _ = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V1},
    })
    assert sync.sync_confs("direct", EXIT_HOST, sync.AWG_CLIENTS_DIR,
                           run=run)["imported"] == ["mobile"]
    # идентичный контент на сервере → skipped
    assert sync.sync_confs("direct", EXIT_HOST, sync.AWG_CLIENTS_DIR,
                           run=run)["skipped"] == ["mobile"]
    run2, _ = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V2},
    })
    report = sync.sync_confs("direct", EXIT_HOST, sync.AWG_CLIENTS_DIR,
                             run=run2)
    assert report["updated"] == ["mobile"]
    dest = env / "awg" / "direct" / "mobile.conf"
    assert dest.read_text(encoding="utf-8") == FIXTURE_CONF_V2
    assert (os.stat(dest).st_mode & 0o777) == 0o600


def test_sync_confs_dry_run_writes_nothing(env):
    run, _ = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V1},
    })
    report = sync.sync_confs("direct", EXIT_HOST, sync.AWG_CLIENTS_DIR,
                             run=run, dry_run=True)
    assert report["imported"] == ["mobile"]
    assert not (env / "awg" / "direct").exists()


def test_sync_confs_stale_reported_and_never_deleted(env):
    dest_dir = env / "awg" / "direct"
    dest_dir.mkdir()
    orphan = dest_dir / "dead.conf"
    orphan.write_text(FIXTURE_CONF_V1, encoding="utf-8")
    run, _ = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V1},
    })
    report = sync.sync_confs("direct", EXIT_HOST, sync.AWG_CLIENTS_DIR,
                             run=run)
    assert report["stale"] == ["dead"]
    assert orphan.is_file()  # vpn sync never deletes local files
    # updated + stale in one report; archived header files are not stale
    (dest_dir / "arch.conf").write_text(
        "# vpnm:status=archived reason=manual\n" + FIXTURE_CONF_V1,
        encoding="utf-8")
    report2 = sync.sync_confs("direct", EXIT_HOST, sync.AWG_CLIENTS_DIR,
                              run=run)
    assert report2["stale"] == ["dead"]


def test_sync_confs_relay_route_writes_relay_dir(env):
    run, _ = make_run({
        "ls": {sync.RELAY_CLIENTS_DIR: "android.conf\n"},
        "files": {sync.RELAY_CLIENTS_DIR: FIXTURE_CONF_V1},
    })
    report = sync.sync_confs("relay", RELAY_HOST, sync.RELAY_CLIENTS_DIR,
                             run=run)
    assert report["imported"] == ["android"]
    assert (env / "awg" / "relay" / "android.conf").is_file()


# --- sync_links ----------------------------------------------------------------

def test_sync_links_imports_with_v11_metadata(env):
    run, calls = make_run({"links": LINKS_OUTPUT})
    actions, stale = sync.sync_links("direct", EXIT_HOST, run=run)
    assert actions == [("srv-reality", "imported")]
    assert stale == []
    record = profiles.get("srv-reality")
    assert record["route"] == "direct"
    assert record["source_host"] == EXIT_HOST
    assert record["synced_at"]
    for cmd in calls:
        assert cmd[:2] == ["ssh", EXIT_HOST]
        assert cmd[2] == "vds gateway links"


def test_sync_links_dry_run_does_not_mutate_store(env):
    run, _ = make_run({"links": LINKS_OUTPUT})
    actions, _stale = sync.sync_links("direct", EXIT_HOST, run=run,
                                      dry_run=True)
    assert actions == [("srv-reality", "imported")]
    assert profiles.list_profiles() == []


def test_sync_links_stale_never_removes(env):
    # на сервере остался только другой пир — srv-reality отсутствует
    other = FIXTURE_VLESS.replace("#srv-reality", "#other-srv")
    run, _ = make_run({"links": f"other-srv: {other}\n"})
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                 name="srv-reality")
    profiles.update_fields("srv-reality", {"source_host": EXIT_HOST,
                                           "route": "direct"})
    actions, stale = sync.sync_links("direct", EXIT_HOST, run=run)
    assert actions == [("other-srv", "imported")]
    assert stale == ["srv-reality"]
    assert profiles.get("srv-reality")["name"] == "srv-reality"


def test_sync_links_empty_server_output_raises(env):
    run, _ = make_run({"links": ""})
    with pytest.raises(pull.PullError):
        sync.sync_links("direct", EXIT_HOST, run=run)


def test_sync_links_updated_replaces_and_restamps_metadata(env):
    run, _ = make_run({"links": LINKS_OUTPUT})
    sync.sync_links("direct", EXIT_HOST, run=run)
    changed = FIXTURE_VLESS.replace("sid=ab", "sid=cd")
    run2, _ = make_run({"links": "srv-reality: " + changed + "\n"})
    actions, _ = sync.sync_links("direct", EXIT_HOST, run=run2)
    assert actions == [("srv-reality", "updated")]
    record = profiles.get("srv-reality")
    assert record["link"] == changed
    assert record["source_host"] == EXIT_HOST
    assert record["route"] == "direct"


# --- run_sync ------------------------------------------------------------------

def test_run_sync_readonly_only_and_reports(env):
    settings.set_host(EXIT_HOST)
    settings.set_relay_host(RELAY_HOST)
    run, calls = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n",
               sync.RELAY_CLIENTS_DIR: "android.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V1,
                  sync.RELAY_CLIENTS_DIR: FIXTURE_CONF_V1},
        "links": LINKS_OUTPUT,
    })
    result = sync.run_sync(run=run)
    assert set(result) == {"direct", "relay"}
    assert result["direct"]["confs"]["imported"] == ["mobile"]
    assert result["relay"]["confs"]["imported"] == ["android"]
    for cmd in calls:
        assert cmd[0] in ("ssh", "scp"), f"non read-only command: {cmd}"
        assert "rm" not in " ".join(cmd)
    # no deletions anywhere
    assert (env / "awg" / "direct" / "mobile.conf").is_file()
    assert (env / "awg" / "relay" / "android.conf").is_file()


def test_run_sync_missing_relay_host_skips_route(env):
    settings.set_host(EXIT_HOST)
    run, _ = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V1},
        "links": LINKS_OUTPUT,
    })
    result = sync.run_sync(routes=("direct", "relay"), run=run)
    assert result["direct"]["host"] == EXIT_HOST
    assert "error" in result["relay"]
    assert "no host" in result["relay"]["error"]


def test_run_sync_explicit_hosts_win_over_settings(env):
    run, calls = make_run({
        "ls": {sync.AWG_CLIENTS_DIR: "mobile.conf\n"},
        "files": {sync.AWG_CLIENTS_DIR: FIXTURE_CONF_V1},
        "links": LINKS_OUTPUT,
    })
    result = sync.run_sync(exit_host="192.0.2.9", routes=("direct",),
                           run=run)
    assert result["direct"]["host"] == "192.0.2.9"
    assert any("192.0.2.9" in cmd for cmd in calls)


# --- profiles.archive() (v1.1 D3; CLI-обвязка — WP-QR6) ------------------------

def test_profile_archive_dry_run_plans_but_writes_nothing(env):
    dest_dir = env / "awg" / "direct"
    dest_dir.mkdir()
    (dest_dir / "dead.conf").write_text(FIXTURE_CONF_V1, encoding="utf-8")
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                 name="dead")
    plan = profiles.archive("dead", "dead-pool", apply=False)
    assert plan["applied"] is False
    assert plan["store_fields"]["status"] == "archived"
    assert plan["conf_moves"][0]["from"].endswith("direct/dead.conf")
    assert plan["conf_moves"][0]["to"].endswith("archive/direct-dead.conf")
    # ничего не изменилось
    assert profiles.get("dead").get("status") is None
    assert (dest_dir / "dead.conf").is_file()
    assert not (env / "awg" / "archive").exists()


def test_profile_archive_apply_moves_conf_and_marks_store(env):
    dest_dir = env / "awg" / "direct"
    dest_dir.mkdir()
    (dest_dir / "dead.conf").write_text(FIXTURE_CONF_V1, encoding="utf-8")
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                 name="dead")
    result = profiles.archive("dead", "dead-pool", apply=True)
    assert result["applied"] is True
    record = profiles.get("dead")
    assert record["status"] == "archived"
    assert record["archived_reason"] == "dead-pool"
    assert not (dest_dir / "dead.conf").exists()
    moved = env / "awg" / "archive" / "direct-dead.conf"
    assert moved.is_file()
    assert (os.stat(moved).st_mode & 0o777) == 0o600
    # повторная архивация — ошибка, а не тихий no-op
    with pytest.raises(profiles.ProfileStoreError):
        profiles.archive("dead", "again", apply=True)


def test_resolver_never_picks_moved_archive_file(env):
    dest_dir = env / "awg" / "direct"
    dest_dir.mkdir()
    (dest_dir / "dead.conf").write_text(FIXTURE_CONF_V1, encoding="utf-8")
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                 name="dead")
    profiles.archive("dead", "dead-pool", apply=True)
    # после archive/ миграции авто-резолвер не находит файл (код 4)
    from vpnm.qrgen import models as qr_models, resolver as qr_resolver

    def req_dead():
        return qr_models.QrRequest(
            route=qr_models.Route("direct"), proto=qr_models.Proto("awg"),
            target_os=qr_models.TargetOS("android"))
    with pytest.raises(qr_models.QrConfigError):
        qr_resolver.resolve(req_dead())


# --- WP-QR8 (review P1/P2 fixes) ------------------------------------------------

def test_archive_awg_only_profile(env):
    """P1-1: archive на AWG-only профиле (записи в profiles.json нет)."""
    (env / "awg" / "mobile-awg.conf").write_text(FIXTURE_CONF_V1,
                                                 encoding="utf-8")
    result = profiles.archive("mobile-awg", "asv1 выключен", apply=True)
    assert result["applied"] is True
    moved = env / "awg" / "archive" / "mobile-awg.conf"
    assert moved.is_file()
    assert (os.stat(moved).st_mode & 0o777) == 0o600
    assert not (env / "awg" / "mobile-awg.conf").exists()
    # пустой store остаётся пустым — поле store обновлять нечему
    assert profiles.list_profiles() == []
    # name валидируется до построения путей: ../escape отклоняется
    with pytest.raises(profiles.ProfileStoreError):
        profiles.archive("../escape", "bad name", apply=True)
    # одноимённые direct/ и relay/ конфиги архивируются рядом, без затирания
    (env / "awg" / "direct").mkdir()
    (env / "awg" / "relay").mkdir()
    (env / "awg" / "direct" / "x.conf").write_text(FIXTURE_CONF_V1,
                                                   encoding="utf-8")
    (env / "awg" / "relay" / "x.conf").write_text(
        FIXTURE_CONF_V2, encoding="utf-8")
    plan = profiles.archive("x", "both nodes dead", apply=True)
    assert len(plan["conf_moves"]) == 2
    assert plan["conf_moves"][0]["to"].endswith("archive/direct-x.conf")
    assert plan["conf_moves"][1]["to"].endswith("archive/relay-x.conf")
    assert (env / "awg" / "archive" / "direct-x.conf").is_file()
    assert (env / "awg" / "archive" / "relay-x.conf").is_file()
    assert ((env / "awg" / "archive" / "direct-x.conf").read_text(
        encoding="utf-8") == FIXTURE_CONF_V1)
    assert ((env / "awg" / "archive" / "relay-x.conf").read_text(
        encoding="utf-8") == FIXTURE_CONF_V2)


def test_sync_relay_without_links_and_extra_remote_files(env):
    """P1-2: PullError и посторонние файлы не роняют sync, хосты сохраняются."""
    run, _calls = make_run({
        "ls": {sync.RELAY_CLIENTS_DIR: "android.conf\nold.conf.bak\nREADME\n"},
        "files": {sync.RELAY_CLIENTS_DIR: FIXTURE_CONF_V1},
        "links": "",
    })
    report = sync.run_sync(relay_host=RELAY_HOST, routes=("relay",), run=run)
    section = report["relay"]
    assert section["confs"]["imported"] == ["android"]
    assert section["confs"]["skipped_remote"] == ["old.conf.bak", "README"]
    assert "links_error" in section
    assert section["links"] == []
    # прогон не упал; confs лежат на месте
    assert (env / "awg" / "relay" / "android.conf").is_file()
    # явный --relay-host сохранён в settings.json при dry_run=False
    assert settings.get_relay_host() == RELAY_HOST
    # явные хосты не сохраняются в dry-run
    run2, _ = make_run({
        "ls": {sync.RELAY_CLIENTS_DIR: "android.conf\n"},
        "files": {sync.RELAY_CLIENTS_DIR: FIXTURE_CONF_V1},
        "links": LINKS_OUTPUT,
    })
    sync.run_sync(relay_host="192.0.2.9", routes=("relay",), run=run2,
                  dry_run=True)
    assert settings.get_relay_host() == RELAY_HOST


def test_sync_links_skipped_backfills_v11_metadata(env):
    """P2-1: skipped-запись без route/source_host добирает метаданные."""
    profiles.add(parse_link(FIXTURE_VLESS), raw_link=FIXTURE_VLESS,
                 name="srv-reality")
    run, _ = make_run({"links": LINKS_OUTPUT})
    actions, _stale = sync.sync_links("direct", EXIT_HOST, run=run)
    assert actions == [("srv-reality", "skipped")]
    record = profiles.get("srv-reality")
    assert record["route"] == "direct"
    assert record["source_host"] == EXIT_HOST
    assert record["synced_at"]
    # у записи с уже полными метаданными synced_at не перетирается
    sync.sync_links("direct", EXIT_HOST, run=run)
    assert profiles.get("srv-reality")["source_host"] == EXIT_HOST
