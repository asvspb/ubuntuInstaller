"""WP-QR3: рендер (qrencode-аргументы, TTY guard, атомарная запись 0600, JSON).

Внешний ``qrencode`` — заглушка; файловая система — только ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.qrgen import builders, models, render  # noqa: E402

REALITY_LINK = (
    "vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT&sid="
    "&flow=xtls-rprx-vision&fp=chrome#asv2-reality")


def _resolved(proto: models.Proto = models.Proto.REALITY,
              os_name: str = "android",
              raw_awg_ini: str | None = None) -> models.ResolvedSource:
    return models.ResolvedSource(
        profile_name="asv2-reality", route=models.Route.DIRECT, proto=proto,
        target_os=models.TargetOS(os_name), client="v2rayng",
        endpoint_host="203.0.113.10", endpoint_port=443, source="profile",
        raw_profile={"name": "asv2-reality", "link": REALITY_LINK},
        raw_awg_ini=raw_awg_ini)


@pytest.fixture()
def built() -> builders.BuiltPayload:
    return builders.build_payload(_resolved())


@pytest.fixture()
def qrencode(monkeypatch):
    calls = []

    def fake_run(cmd, input=None, text=None, capture_output=True,
                 check=False, **kwargs):  # noqa: ANN001
        calls.append({"cmd": list(cmd), "input": input})
        kind = cmd[cmd.index("-t") + 1]
        if kind in ("PNG", "SVG"):
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"IMAGE-BYTES")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0,
                               stdout="\x1b[40;37;1m████\x1b[0m\n"
                                      "\x1b[40;37;1m████\x1b[0m\n",
                               stderr="")

    monkeypatch.setattr(render, "subprocess", SimpleNamespace(run=fake_run))
    monkeypatch.setattr(render, "shutil", SimpleNamespace(
        which=lambda name: "/usr/bin/qrencode",
        get_terminal_size=lambda fallback=(80, 24): os.terminal_size((120, 40))))
    return calls


# --- аргументы qrencode (§5.1, AC-QR-6) ---------------------------------------

@pytest.mark.parametrize("ascii_mode,invert,expected", [
    (False, False, "ANSIUTF8"),
    (False, True, "ANSIUTF8i"),
    (True, False, "ASCII"),
    (True, True, "ASCIIi"),
])
def test_terminal_args_pick_exact_type_and_never_pass_i(
        built, ascii_mode, invert, expected):
    args = render.build_args(built, ascii_mode=ascii_mode, invert=invert)
    assert args[:4] == ["qrencode", "-t", expected, "-m"]
    assert args[args.index("-m") + 1] == "4"
    assert args[args.index("-l") + 1] == built.qr_ecc
    assert args[-2:] == ["-r", "/dev/stdin"]
    assert "-i" not in args  # -i = --ignorecase, не инверсия
    assert args[args.index("-o") + 1] == "-"


def test_png_args_use_scale_8_and_output_file(built, tmp_path):
    out = tmp_path / "qr.png"
    args = render.build_args(built, image="png", out=out)
    assert args[args.index("-t") + 1] == "PNG"
    assert args[args.index("-s") + 1] == "8"
    assert args[args.index("-o") + 1] == str(out)
    assert args[-2:] == ["-r", "/dev/stdin"]


def test_svg_args_have_no_scale(built, tmp_path):
    args = render.build_args(built, image="svg", out=tmp_path / "qr.svg")
    assert args[args.index("-t") + 1] == "SVG"
    assert "-s" not in args
    assert args[-2:] == ["-r", "/dev/stdin"]


def test_payload_is_passed_via_stdin(qrencode, built):
    text = render.render_qr_text(built)
    assert text.startswith("\x1b[")
    assert qrencode[0]["input"] == built.payload
    # секрета нет в argv
    assert all(built.payload not in token for token in qrencode[0]["cmd"])


# --- TTY guard (§5.2) ---------------------------------------------------------

def test_visible_width_strips_ansi():
    text = "\x1b[40;37;1m████████\x1b[0m\n\x1b[40;37;1m██\x1b[0m\n"
    assert render.visible_qr_width(text) == 8


def test_narrow_terminal_raises_and_no_tty_check_disables(monkeypatch):
    monkeypatch.setattr(render, "sys", SimpleNamespace(
        stdout=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setattr(render, "shutil", SimpleNamespace(
        get_terminal_size=lambda fallback=(80, 24): os.terminal_size((30, 10))))

    with pytest.raises(models.QrRenderError) as excinfo:
        render.ensure_terminal_width("X" * 42, no_tty_check=False)
    assert excinfo.value.exit_code == models.EXIT_RENDER_ERROR
    assert "42" in str(excinfo.value)

    render.ensure_terminal_width("X" * 42, no_tty_check=True)  # не падает


def test_wide_terminal_passes(monkeypatch):
    monkeypatch.setattr(render, "sys", SimpleNamespace(
        stdout=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setattr(render, "shutil", SimpleNamespace(
        get_terminal_size=lambda fallback=(80, 24): os.terminal_size((200, 50))))
    render.ensure_terminal_width("X" * 42, no_tty_check=False)


# --- запись файлов (§1.3) -----------------------------------------------------

def test_write_text_is_atomic_and_0600(tmp_path):
    path = tmp_path / "sub" / "profile.conf"
    written = render.write_text(path, "payload\n")
    assert written == path
    assert path.read_text() == "payload\n"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert list(path.parent.glob("*.tmp")) == []


def test_write_text_refuses_overwrite_without_force(tmp_path):
    path = tmp_path / "out.txt"
    render.write_text(path, "one")
    with pytest.raises(models.QrRenderError) as excinfo:
        render.write_text(path, "two")
    assert excinfo.value.exit_code == models.EXIT_RENDER_ERROR
    assert path.read_text() == "one"


def test_force_overwrite_restores_0600(tmp_path):
    path = tmp_path / "out.txt"
    path.write_text("old")
    os.chmod(path, 0o644)
    render.write_text(path, "new", force=True)
    assert path.read_text() == "new"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_write_image_creates_0600_file(qrencode, built, tmp_path):
    path = tmp_path / "qr.png"
    render.write_image(built, path, image="png")
    assert path.read_bytes() == b"IMAGE-BYTES"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_missing_qrencode_is_render_error(monkeypatch, built):
    monkeypatch.setattr(render, "shutil", SimpleNamespace(which=lambda name: None))
    with pytest.raises(models.QrRenderError) as excinfo:
        render.render_qr_text(built)
    assert excinfo.value.exit_code == models.EXIT_RENDER_ERROR
    assert "qrencode" in str(excinfo.value)


# --- JSON (B4) ----------------------------------------------------------------

def test_json_payload_is_null_by_default(built):
    payload = render.json_payload(_resolved(), built)
    assert payload["schema"] == "vpnm.qr/1"
    assert payload["payload"] is None
    assert payload["payload_bytes"] == built.payload_bytes
    assert payload["payload_sha256"] == built.payload_sha256
    assert payload["qr_ecc"] == built.qr_ecc
    assert payload["out_path"] is None


def test_json_payload_filled_only_with_show_uri(built):
    payload = render.json_payload(_resolved(), built, show_uri=True)
    assert payload["payload"] == built.payload


def test_json_out_path_is_reported(built, tmp_path):
    out = tmp_path / "qr.json"
    payload = render.json_payload(_resolved(), built, out_path=out)
    assert payload["out_path"] == str(out)
    parsed = json.loads(render.json_text(_resolved(), built, out_path=out))
    assert parsed["payload"] is None


# --- терминальный вывод (B4) --------------------------------------------------

def test_format_terminal_hides_payload_by_default(qrencode, built):
    text = render.format_terminal(_resolved(), built, "QR-ART\n")
    assert "QR-ART" in text
    assert "vless://" not in text
    assert "11111111-2222" not in text
    assert built.payload not in text


def test_format_terminal_show_uri_appends_payload(built):
    text = render.format_terminal(_resolved(), built, "QR-ART\n", show_uri=True)
    assert built.payload in text


def test_format_terminal_prints_warnings(built):
    resolved = _resolved()
    resolved.warnings.append("профиль 'x' классифицирован как relay по имени")
    text = render.format_terminal(resolved, built, "QR\n")
    assert "WARNING: профиль 'x' классифицирован" in text


def test_emit_uri_without_out_returns_payload(qrencode, built):
    request = models.QrRequest(route=models.Route.DIRECT,
                               proto=models.Proto.REALITY,
                               target_os=models.TargetOS.ANDROID,
                               output_format=models.OutputFormat.URI)
    text = render.emit(_resolved(), built, request)
    assert text == built.payload + "\n"


def test_emit_uri_with_out_writes_file(qrencode, built, tmp_path):
    out = tmp_path / "p.conf"
    request = models.QrRequest(route=models.Route.DIRECT,
                               proto=models.Proto.REALITY,
                               target_os=models.TargetOS.ANDROID,
                               output_format=models.OutputFormat.URI,
                               out_path=out)
    text = render.emit(_resolved(), built, request)
    assert text.startswith("записано:")
    assert out.read_text() == built.payload
    assert oct(out.stat().st_mode & 0o777) == "0o600"


def test_emit_qr_returns_header_and_matrix(qrencode, built):
    request = models.QrRequest(route=models.Route.DIRECT,
                               proto=models.Proto.REALITY,
                               target_os=models.TargetOS.ANDROID)
    text = render.emit(_resolved(), built, request)
    assert "[vpn qr] profile=asv2-reality" in text
    assert "ECC:" in text
    assert "████" in text


# --- v1.1 (WP-QR6): артефакты, masked-preview (§9.2, AC-QR-9/12) --------------

FIXTURE_AWG_INI = (
    "[Interface]\n"
    "Address = 10.10.0.2/32\n"
    "PrivateKey = FIXTURE_PW_CLIENT_AWG\n"
    "\n"
    "[Peer]\n"
    "PublicKey = FIXTURE_PW_SERVER_AWG\n"
    "Endpoint = 203.0.113.10:51825\n"
    "AllowedIPs = 0.0.0.0/0\n"
)


def _resolved_awg() -> models.ResolvedSource:
    return models.ResolvedSource(
        profile_name="mobile", route=models.Route.DIRECT, proto=models.Proto.AWG,
        target_os=models.TargetOS.ANDROID, client="amneziawg",
        endpoint_host="203.0.113.10", endpoint_port=51825, source="awg",
        raw_awg_ini=FIXTURE_AWG_INI)


def _awg_built() -> builders.BuiltPayload:
    return builders.build_payload(_resolved_awg())


def test_artifacts_dir_env_override_and_default(monkeypatch):
    monkeypatch.setenv("VPN_MANAGER_ARTIFACTS_DIR", "/tmp/vpnm-art")
    assert render.artifacts_dir() == Path("/tmp/vpnm-art")
    monkeypatch.delenv("VPN_MANAGER_ARTIFACTS_DIR")
    assert render.artifacts_dir() == (Path.home() / ".local" / "share" /
                                      "vpn-manager" / "artifacts")


def test_ensure_artifacts_dir_is_0700(tmp_path):
    target = tmp_path / "nested" / "artifacts"
    render.ensure_artifacts_dir(target)
    assert oct(target.stat().st_mode & 0o777) == "0o700"
    render.ensure_artifacts_dir(target)  # идемпотентно
    assert oct(target.stat().st_mode & 0o777) == "0o700"


def test_canonical_path_naming(tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_MANAGER_ARTIFACTS_DIR", str(tmp_path))
    path = render.canonical_path(_resolved_awg(), "png")
    assert path == tmp_path / "direct-awg-android-mobile.png"


def test_save_payload_auto_conf_0600_and_replaces_on_change(
        tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_MANAGER_ARTIFACTS_DIR", str(tmp_path))
    resolved = _resolved_awg()
    path = render.save_payload_auto(resolved, _awg_built())
    assert path.name == "direct-awg-android-mobile.conf"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert "Table = off" not in path.read_text()
    # смена payload_sha256 → атомарная перезапись, права не сбрасываются
    path.chmod(0o644)
    second = render.save_payload_auto(resolved, _awg_built())
    assert second == path
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_save_png_auto_0600_overwrites(qrencode, tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_MANAGER_ARTIFACTS_DIR", str(tmp_path))
    target = tmp_path / "qr.png"
    target.write_bytes(b"old")
    path = render.save_png_auto(builders.build_payload(_resolved()), target)
    assert path == target
    assert path.read_bytes() == b"IMAGE-BYTES"
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_parent_world_readable(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    private.chmod(0o700)
    public = tmp_path / "public"
    public.mkdir()
    public.chmod(0o755)
    assert render.parent_world_readable(private / "x.conf") is False
    assert render.parent_world_readable(public / "x.conf") is True


def test_conf_header_lines_origin_and_paths(tmp_path):
    built = _awg_built()
    lines = render.conf_header_lines(
        _resolved_awg(), built, conf_path=tmp_path / "a.conf",
        png_path=tmp_path / "a.png", origin="synced 2026-11-24T10:00:00+00:00")
    joined = "\n".join(lines)
    assert "[vpn conf] route=direct | proto=awg | os=android" in joined
    assert "client=amneziawg | profile=mobile" in joined
    assert "endpoint=203.0.113.10:51825" in joined
    assert built.payload_sha256.split(":", 1)[-1][:4] in joined
    assert "origin=synced 2026-11-24T10:00:00+00:00" in joined
    assert f"Conf: {tmp_path / 'a.conf'} (0600)" in joined
    assert f"QR PNG: {tmp_path / 'a.png'} (0600)" in joined
    assert "FIXTURE" not in joined


def test_conf_preview_masks_secrets_and_hints_reveal():
    built = _awg_built()
    text = render.conf_preview(_resolved_awg(), built)
    assert "PrivateKey = <redacted>" in text
    assert "FIXTURE_PW_CLIENT_AWG" not in text
    # PublicKey — не секрет: остаётся в masked-выводе
    assert "PublicKey = FIXTURE_PW_SERVER_AWG" in text
    assert render.REVEAL_HINT in text
    assert built.payload not in text


def test_conf_preview_reveal_prints_raw_payload():
    built = _awg_built()
    text = render.conf_preview(_resolved_awg(), built, reveal=True)
    assert "PrivateKey = FIXTURE_PW_CLIENT_AWG" in text
    assert built.payload in text
    assert render.REVEAL_HINT not in text


def test_emit_qr_save_png_cb_after_guard(qrencode, tmp_path, monkeypatch):
    monkeypatch.setenv("VPN_MANAGER_ARTIFACTS_DIR", str(tmp_path))
    saved = tmp_path / "saved.png"
    request = models.QrRequest(route=models.Route.DIRECT,
                               proto=models.Proto.REALITY,
                               target_os=models.TargetOS.ANDROID)
    built = builders.build_payload(_resolved())
    text = render.emit(_resolved(), built, request,
                       save_png_cb=lambda: render.save_png_auto(built, saved))
    assert f"Saved PNG: {saved} (0600)" in text
    assert saved.read_bytes() == b"IMAGE-BYTES"


def test_json_payload_reports_saved_png(tmp_path):
    built = builders.build_payload(_resolved())
    payload = render.json_payload(_resolved(), built,
                                  saved_png=tmp_path / "p.png")
    assert payload["png_path"] == str(tmp_path / "p.png")
    payload2 = render.json_payload(_resolved(), built)
    assert payload2["png_path"] is None
    assert payload2["schema"] == "vpnm.qr/1"
