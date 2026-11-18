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
