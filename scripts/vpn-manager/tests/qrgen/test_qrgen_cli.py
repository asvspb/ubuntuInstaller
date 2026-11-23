"""WP-QR3: CLI ``vpn qr`` через ``typer.testing.CliRunner`` (коды §6)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.cli import app  # noqa: E402
from vpnm.qrgen import render  # noqa: E402


def run(*args: str):
    return CliRunner().invoke(app, ["qr", *args])


def _out(result) -> str:
    return getattr(result, "stdout", "") or result.output or ""


def _err(result) -> str:
    return getattr(result, "stderr", "") or ""


def _all(result) -> str:
    return _out(result) + _err(result)


@pytest.fixture()
def env(live_env):
    live_env.live_store()
    live_env.live_awg()
    return live_env


# --- код 0: успешные ветки ----------------------------------------------------

def test_direct_awg_android_prints_safe_header(env):
    result = run("--direct", "--awg", "--android")
    assert result.exit_code == 0, _all(result)
    text = _out(result)
    assert "profile=laptop" in text
    assert "route=direct | proto=awg | os=android | client=amneziawg" in text
    assert "WARNING: используется конфиг активного пира 'laptop'" in text
    assert "PrivateKey" not in text
    assert "vless://" not in text
    assert "hysteria2://" not in text


def test_relay_reality_windows(env):
    result = run("--relay", "--reality", "--windows")
    assert result.exit_code == 0, _all(result)
    text = _out(result)
    assert "profile=relay-reality" in text
    assert "client=v2rayn" in text
    assert "WARNING: профиль 'relay-reality' классифицирован как relay по имени" in text


def test_direct_hy_linux(env):
    result = run("--direct", "--hy", "--linux")
    assert result.exit_code == 0, _all(result)
    assert "profile=asv2-hy2" in _out(result)


def test_uri_format_prints_raw_link(env):
    result = run("--direct", "--reality", "--android", "--format", "uri")
    assert result.exit_code == 0, _all(result)
    assert _out(result).startswith("vless://11111111-2222-3333-4444-000000000001@")


def test_json_has_no_payload_by_default(env):
    result = run("--relay", "--reality", "--windows", "--format", "json")
    assert result.exit_code == 0, _all(result)
    payload = json.loads(_out(result))
    assert payload["schema"] == "vpnm.qr/1"
    assert payload["payload"] is None
    assert payload["profile"] == "relay-reality"
    assert payload["proto"] == "reality"


def test_json_show_uri_reveals_payload(env):
    result = run("--direct", "--reality", "--android", "--format", "json",
                 "--show-uri")
    assert result.exit_code == 0, _all(result)
    payload = json.loads(_out(result))
    assert payload["payload"].startswith("vless://")


def test_png_writes_0600_file(env, tmp_path):
    out = tmp_path / "qr.png"
    result = run("--direct", "--awg", "--android", "--format", "png",
                 "--out", str(out))
    assert result.exit_code == 0, _all(result)
    assert out.read_bytes() == b"IMAGE-BYTES"
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert "записано:" in _out(result)


def test_json_out_writes_file(env, tmp_path):
    out = tmp_path / "qr.json"
    result = run("--direct", "--reality", "--android", "--format", "json",
                 "--out", str(out))
    assert result.exit_code == 0, _all(result)
    payload = json.loads(out.read_text())
    assert payload["payload"] is None
    assert payload["out_path"] == str(out)


def test_show_uri_prints_payload_under_qr(env):
    result = run("--direct", "--reality", "--android", "--show-uri")
    assert result.exit_code == 0, _all(result)
    assert "vless://11111111-2222-3333-4444-000000000001@" in _out(result)


# --- код 2: грамматика флагов -------------------------------------------------

@pytest.mark.parametrize("args", [
    (),                                     # нет ни одной группы
    ("--direct",),                          # нет протокола и ОС
    ("--direct", "--relay", "--awg", "--android"),   # два маршрута
    ("--direct", "--awg", "--reality", "--android"),  # два протокола
    ("--direct", "--awg", "--android", "--ios"),      # две ОС
    ("--direct", "--awg", "--android", "--format", "pdf"),
    ("--direct", "--awg", "--android", "--format", "png"),
    ("--direct", "--awg", "--android", "--out", "/tmp/x"),
])
def test_usage_errors_return_2(env, args):
    assert run(*args).exit_code == 2


@pytest.mark.parametrize("bad_profile", ["..", "../etc/passwd", ".hidden",
                                         "laptop.conf", "-bad"])
def test_bad_profile_returns_2(env, bad_profile):
    result = run("--direct", "--reality", "--android", "--profile", bad_profile)
    assert result.exit_code == 2, _all(result)


# --- код 3: неподдерживаемые комбинации ---------------------------------------

@pytest.mark.parametrize("args", [
    ("--relay", "--xhttp", "--android"),
    ("--relay", "--hy", "--linux"),
    ("--direct", "--reality", "--android", "--client", "amneziavpn"),
    ("--direct", "--awg", "--android", "--client", "v2rayng"),
    ("--direct", "--reality", "--android", "--client", "amneziawg"),
    ("--direct", "--reality", "--android", "--client", "warp"),
])
def test_unsupported_combos_return_3_or_2(env, args):
    code = run(*args).exit_code
    expected = 2 if args[-1] == "warp" else 3
    assert code == expected


# --- код 4: нет валидного источника ------------------------------------------

def test_missing_profile_for_direct_xhttp(env):
    # в store нет xhttp-профиля → fail-closed
    result = run("--direct", "--xhttp", "--android")
    assert result.exit_code == 4, _all(result)
    assert "xhttp" in _all(result)


def test_unknown_profile_name(env):
    result = run("--direct", "--reality", "--android", "--profile", "nope")
    assert result.exit_code == 4, _all(result)


def test_profile_route_mismatch(env):
    result = run("--direct", "--reality", "--android", "--profile", "relay-reality")
    assert result.exit_code == 4, _all(result)


def test_direct_awg_without_any_direct_config(env, monkeypatch):
    # убираем активный пир из «/etc» → приоритеты 1 и 2 пусты
    (env.etc_dir / "laptop.conf").unlink()
    result = run("--direct", "--awg", "--android")
    assert result.exit_code == 4, _all(result)
    assert "add-client" in _all(result)


# --- код 5: рендер ------------------------------------------------------------

def test_existing_out_file_without_force_is_5(env, tmp_path):
    out = tmp_path / "qr.png"
    out.write_bytes(b"old")
    result = run("--direct", "--awg", "--android", "--format", "png",
                 "--out", str(out))
    assert result.exit_code == 5, _all(result)
    assert out.read_bytes() == b"old"


def test_missing_qrencode_is_5(env, monkeypatch):
    monkeypatch.setattr(render, "shutil", SimpleNamespace(
        which=lambda name: None,
        get_terminal_size=lambda fallback=(80, 24): os.terminal_size((120, 40))))
    result = run("--direct", "--reality", "--android")
    assert result.exit_code == 5, _all(result)
    assert "qrencode" in _all(result)


# --- секреты не утекают в ошибках --------------------------------------------

def test_error_text_has_no_secrets(env):
    result = run("--direct", "--reality", "--android", "--profile", "nope")
    text = _all(result)
    assert "11111111-2222-3333-4444-000000000001" not in text
    assert "FIXTURE_PBK_DIRECT" not in text


# --- v1.1 (WP-QR6): vpn conf, авто-сохранение, sync/archive (§9, AC-QR-8..12) ---

FIXTURE_DIRECT_LINK = (
    "vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT&sid="
    "&flow=xtls-rprx-vision&fp=chrome#srv-reality")


def run_conf(*args: str):
    return CliRunner().invoke(app, ["conf", *args])


def run_raw(*args: str):
    return CliRunner().invoke(app, list(args))


def _artifacts() -> Path:
    from vpnm.qrgen.render import artifacts_dir
    return artifacts_dir()


def test_vpn_conf_direct_awg_saves_0600_and_masks(env):
    # AC-QR-9a/9c + WP-QR6 AC-1: .conf 0600, путь в шапке, тело masked
    result = run_conf("--direct", "--awg", "--android")
    assert result.exit_code == 0, _all(result)
    out = _out(result)
    conf_path = _artifacts() / "direct-awg-android-laptop.conf"
    assert conf_path.is_file()
    assert oct(conf_path.stat().st_mode & 0o777) == "0o600"
    assert "Table = off" not in conf_path.read_text()
    assert f"Conf: {conf_path} (0600)" in out
    assert "[vpn conf] route=direct | proto=awg | os=android" in out
    assert "origin=system" in out  # исходник живёт в «/etc» стенда
    assert "PrivateKey = <redacted>" in out
    assert "FIXTURE_PW_CLIENT_AWG" not in _all(result)
    assert render.REVEAL_HINT in out


def test_vpn_conf_artifacts_dir_mode_0700(env):
    assert run_conf("--direct", "--awg", "--android").exit_code == 0
    assert oct(_artifacts().stat().st_mode & 0o777) == "0o700"


def test_vpn_conf_qr_prints_qr_and_saves_png(env):
    # AC-QR-12 + WP-QR6 AC-2: --qr добавляет QR и сохраняет .png (путь в шапке)
    result = run_conf("--direct", "--awg", "--android", "--qr")
    assert result.exit_code == 0, _all(result)
    out = _out(result)
    png_path = _artifacts() / "direct-awg-android-laptop.png"
    assert png_path.is_file()
    assert png_path.read_bytes() == b"IMAGE-BYTES"
    assert oct(png_path.stat().st_mode & 0o777) == "0o600"
    assert f"QR PNG: {png_path} (0600)" in out
    assert "████" in out
    assert "PrivateKey = <redacted>" in out


def test_vpn_conf_uri_protocol_saves_uri(env):
    result = run_conf("--direct", "--reality", "--android")
    assert result.exit_code == 0, _all(result)
    out = _out(result)
    uri_path = _artifacts() / "direct-reality-android-asv2-reality.uri"
    assert uri_path.is_file()
    assert oct(uri_path.stat().st_mode & 0o777) == "0o600"
    assert uri_path.read_text().startswith("vless://")
    assert f"Conf: {uri_path} (0600)" in out
    assert "vless://11111111" not in out  # masked: URI целиком
    assert "<redacted>" in out


def test_vpn_conf_no_save_writes_nothing(env):
    result = run_conf("--direct", "--awg", "--android", "--no-save")
    assert result.exit_code == 0, _all(result)
    assert list(_artifacts().glob("*")) == []
    assert "Conf:" not in _out(result)


def test_vpn_conf_reveal_prints_raw_and_warns(env):
    # WP-QR6 AC-4 / AC-QR-9b: сырой текст в stdout + WARNING в stderr
    result = run_conf("--direct", "--awg", "--android", "--reveal")
    assert result.exit_code == 0, _all(result)
    assert "PrivateKey = FIXTURE_PW_CLIENT_AWG" in _out(result)
    assert "вывод содержит секреты" in _err(result)


def test_vpn_conf_out_world_readable_warns(env, tmp_path):
    target = tmp_path / "public"
    target.mkdir()
    target.chmod(0o755)
    out = target / "mobile.conf"
    result = run_conf("--direct", "--awg", "--android",
                      "--out", str(out), "--force")
    assert result.exit_code == 0, _all(result)
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    assert "world-readable" in _err(result)
    assert "WARNING" in _err(result)


def test_vpn_conf_out_conflicts_no_save(env):
    assert run_conf("--direct", "--awg", "--android", "--no-save",
                    "--out", "/tmp/x.conf").exit_code == 2


def test_vpn_qr_autosaves_png_with_path_in_header(env):
    # WP-QR6 AC-3 / AC-QR-12: дефолтный qr сохраняет PNG (0600) + путь в шапке
    result = run("--relay", "--awg", "--android")
    assert result.exit_code == 0, _all(result)
    out = _out(result)
    png_path = _artifacts() / "relay-awg-android-android.png"
    assert png_path.is_file()
    assert png_path.read_bytes() == b"IMAGE-BYTES"
    assert oct(png_path.stat().st_mode & 0o777) == "0o600"
    assert f"Saved PNG: {png_path} (0600)" in out
    assert "Source conf: " in out  # исходный relay-конфиг показан в шапке
    assert "FIXTURE_PW" not in out  # шапка без секретов


def test_vpn_qr_no_save_restores_v1_behavior(env):
    result = run("--relay", "--awg", "--android", "--no-save")
    assert result.exit_code == 0, _all(result)
    assert list(_artifacts().glob("*")) == []
    assert "Saved PNG:" not in _out(result)


def test_vpn_qr_autosave_overwrites_on_change_0600(env):
    first = run("--relay", "--awg", "--android")
    assert first.exit_code == 0, _all(first)
    png = _artifacts() / "relay-awg-android-android.png"
    png.chmod(0o644)
    second = run("--relay", "--awg", "--android")
    assert second.exit_code == 0, _all(second)
    assert oct(png.stat().st_mode & 0o777) == "0o600"


def test_vpn_qr_reveal_is_show_uri_alias(env):
    result = run("--direct", "--reality", "--android", "--reveal")
    assert result.exit_code == 0, _all(result)
    assert "vless://11111111-2222-3333-4444-000000000001@" in _out(result)
    assert "вывод содержит секреты" in _err(result)


def test_vpn_conf_sync_pulls_and_renders_offline(env, monkeypatch):
    # AC-QR-8: --sync вызывает только read-only ssh/ls/scp через run-мок
    from vpnm import amneziawg, settings, sync as sync_mod
    settings_path = env.root / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", settings_path)
    settings.set_host("203.0.113.10")
    calls: list = []

    def fake_run(cmd):
        calls.append(list(cmd))
        joined = " ".join(cmd)
        if cmd[0] == "ssh" and "gateway links" in joined:
            return 0, f"srv-reality: {FIXTURE_DIRECT_LINK}\n", ""
        if cmd[0] == "ssh" and "-1" in cmd:
            return 0, "laptop.conf\n", ""
        if cmd[0] == "scp":
            Path(cmd[-1]).write_text(
                "[Interface]\nAddress = 10.10.0.9/32\n"
                "PrivateKey = FIXTURE_PW_CLIENT_AWG\n\n[Peer]\n"
                "PublicKey = FIXTURE_PW_SERVER_AWG\n"
                "Endpoint = 203.0.113.10:51825\nAllowedIPs = 0.0.0.0/0\n",
                encoding="utf-8")
            return 0, "", ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(sync_mod, "run_cmd", fake_run)
    result = run_conf("--direct", "--awg", "--android", "--sync")
    assert result.exit_code == 0, _all(result)
    assert all(cmd[0] in ("ssh", "scp") for cmd in calls)  # read-only only
    assert "conf imported: laptop" in _out(result)
    assert "link imported: srv-reality" in _out(result)
    # подтянутый файл лег в namespace direct/
    assert (env.awg_dir / "direct" / "laptop.conf").is_file()
    assert oct((env.awg_dir / "direct" / "laptop.conf").stat().st_mode
               & 0o777) == "0o600"


def test_vpn_conf_sync_failure_does_not_kill_offline_render(env, monkeypatch):
    from vpnm import settings, sync as sync_mod
    monkeypatch.setattr(settings, "SETTINGS_PATH", env.root / "settings.json")
    result = run_conf("--direct", "--awg", "--android", "--sync")
    assert result.exit_code == 0, _all(result)
    assert "no host for route 'direct'" in _err(result)


def test_vpn_sync_cli_direct_dry_run(env, monkeypatch):
    from vpnm import profiles, settings, sync as sync_mod
    monkeypatch.setattr(settings, "SETTINGS_PATH", env.root / "settings.json")
    monkeypatch.setattr(profiles, "STORE_PATH", env.root / "profiles.json")

    def fake_run(cmd):
        joined = " ".join(cmd)
        if cmd[0] == "ssh" and "gateway links" in joined:
            return 0, f"srv-reality: {FIXTURE_DIRECT_LINK}\n", ""
        if cmd[0] == "ssh" and "-1" in cmd:
            return 0, "", ""
        raise AssertionError(f"unexpected command in dry-run sync: {cmd}")

    monkeypatch.setattr(sync_mod, "run_cmd", fake_run)
    result = run_raw("sync", "--direct", "--exit-host", "203.0.113.10",
                     "--dry-run")
    assert result.exit_code == 0, _all(result)
    out = _out(result)
    assert "direct (203.0.113.10) [dry-run]" in out
    # dry-run: локальный кэш не записан
    assert not (env.awg_dir / "direct").exists()
    # и сервер не мутирован: в отчёте только импорт/чтение (мок read-only)


def test_vpn_sync_cli_conflicting_routes_is_2(env):
    assert run_raw("sync", "--direct", "--relay").exit_code == 2


def test_vpn_profile_archive_dry_run_then_apply(env):
    # AC-QR-11: dry-run по умолчанию, --apply переносит и помечает
    env.add_profile("dead", FIXTURE_DIRECT_LINK, "xray-reality")
    direct_dir = env.awg_dir / "direct"
    direct_dir.mkdir(exist_ok=True)
    (direct_dir / "dead.conf").write_text("stub", encoding="utf-8")

    dry = run_raw("profile", "archive", "dead", "--reason", "dead-pool")
    assert dry.exit_code == 0, _all(dry)
    assert "dry-run" in _out(dry)
    assert (direct_dir / "dead.conf").is_file()
    assert not (env.awg_dir / "archive").exists()

    applied = run_raw("profile", "archive", "dead", "--reason", "dead-pool",
                      "--apply")
    assert applied.exit_code == 0, _all(applied)
    assert not (direct_dir / "dead.conf").exists()
    moved = env.awg_dir / "archive" / "direct-dead.conf"
    assert moved.is_file()
    assert oct(moved.stat().st_mode & 0o777) == "0o600"
    from vpnm import profiles as profiles_mod
    record = profiles_mod.get("dead")
    assert record["status"] == "archived"
    assert record["archived_reason"] == "dead-pool"


def test_vpn_profile_archive_requires_reason_and_existing_profile(env):
    missing_reason = run_raw("profile", "archive", "dead")
    assert missing_reason.exit_code == 2
    unknown = run_raw("profile", "archive", "no-such", "--reason", "r")
    assert unknown.exit_code == 1
    assert "no-such" in _all(unknown)
