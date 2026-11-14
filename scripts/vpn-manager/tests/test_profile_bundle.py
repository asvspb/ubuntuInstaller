"""Сохранение/передача профилей: export/restore бандла (JSON, 0600)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import profiles  # noqa: E402
from vpnm.links import parse_link  # noqa: E402

REALITY_RAW = (
    "vless://11111111-2222-3333-4444-777777777777@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=01"
    "&flow=xtls-rprx-vision&fp=chrome#phone")
HY2_RAW = ("hysteria2://FIXTURE_PW_hy2_demo_only@203.0.113.30:443"
           "?sni=example-test.invalid&insecure=0#mob")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    monkeypatch.setattr(profiles, "STORE_PATH", path)
    return path


def _add(raw, name, store):
    profiles.add(parse_link(raw), raw, name=name, path=store)


def test_export_bundle_shape(store):
    _add(REALITY_RAW, "phone", store)
    payload = profiles.export_bundle()
    assert payload["format"] == profiles.BUNDLE_FORMAT
    assert payload["version"] == profiles.BUNDLE_VERSION
    assert [p["name"] for p in payload["profiles"]] == ["phone"]
    assert payload["profiles"][0]["link"].startswith("vless://")


def test_save_bundle_is_0600_and_roundtrips(tmp_path, store):
    _add(REALITY_RAW, "phone", store)
    target = tmp_path / "bundle.json"
    profiles.save_bundle(profiles.export_bundle(), target)
    assert target.stat().st_mode & 0o777 == 0o600
    loaded = profiles.load_bundle(target)
    assert loaded["profiles"][0]["name"] == "phone"


def test_import_merge_skip_and_replace(store):
    _add(REALITY_RAW, "phone", store)
    payload = {
        "format": profiles.BUNDLE_FORMAT, "version": profiles.BUNDLE_VERSION,
        "profiles": [
            {"name": "phone", "link": HY2_RAW},
            {"name": "mob", "link": HY2_RAW},
        ],
    }
    summary = profiles.import_bundle(payload, path=store)
    assert summary == {"added": ["mob"], "replaced": [], "skipped": ["phone"]}
    assert [p["name"] for p in profiles.list_profiles(path=store)] == [
        "phone", "mob"]
    # replace перезаписывает ссылку и протокол
    summary = profiles.import_bundle(payload, replace=True, path=store)
    assert summary["replaced"] == ["phone", "mob"]
    phone = profiles.get("phone", path=store)
    assert phone["protocol"] == "hysteria2"
    assert phone["link"] == HY2_RAW


def test_import_dry_run_does_not_write(store):
    _add(REALITY_RAW, "phone", store)
    before = store.read_text()
    payload = {
        "format": profiles.BUNDLE_FORMAT, "version": profiles.BUNDLE_VERSION,
        "profiles": [{"name": "mob", "link": HY2_RAW}],
    }
    summary = profiles.import_bundle(payload, dry_run=True, path=store)
    assert summary["added"] == ["mob"]
    assert store.read_text() == before


@pytest.mark.parametrize("payload,match", [
    ({"format": "other", "version": 1, "profiles": []}, "формат"),
    ({"format": profiles.BUNDLE_FORMAT, "version": 99,
      "profiles": [{"name": "x", "link": REALITY_RAW}]}, "версия"),
    ({"format": profiles.BUNDLE_FORMAT, "version": 1, "profiles": []},
     "нет профилей"),
    ({"format": profiles.BUNDLE_FORMAT, "version": 1,
      "profiles": [{"name": "x", "link": "not-a-link"}]}, "x"),
    ({"format": profiles.BUNDLE_FORMAT, "version": 1,
      "profiles": [{"name": "x", "link": REALITY_RAW},
                   {"name": "x", "link": HY2_RAW}]}, "дубль"),
])
def test_import_rejects_bad_bundles(store, payload, match):
    with pytest.raises(profiles.ProfileStoreError, match=match):
        profiles.import_bundle(payload, path=store)


# --- CLI ---------------------------------------------------------------------


def _runner():
    from typer.testing import CliRunner
    return CliRunner()


def test_cli_export_restore(store, tmp_path, monkeypatch):
    from vpnm.cli import app as cli_app
    _add(REALITY_RAW, "phone", store)
    target = tmp_path / "backup.json"
    result = _runner().invoke(cli_app, [
        "profile", "export", "-o", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert REALITY_RAW not in result.output  # секреты не печатаем
    # повтор без --force — отказ
    result = _runner().invoke(cli_app, [
        "profile", "export", "-o", str(target)])
    assert result.exit_code == 2
    # restore на пустой store
    fresh = tmp_path / "fresh.json"
    monkeypatch.setattr(profiles, "STORE_PATH", fresh)
    result = _runner().invoke(cli_app, [
        "profile", "restore", str(target), "-y"])
    assert result.exit_code == 0, result.output
    assert [p["name"] for p in profiles.list_profiles()] == ["phone"]


def test_cli_restore_dry_run(store, tmp_path):
    from vpnm.cli import app as cli_app
    _add(REALITY_RAW, "phone", store)
    target = tmp_path / "backup.json"
    profiles.save_bundle(profiles.export_bundle(), target)
    profiles.remove("phone", path=store)
    result = _runner().invoke(cli_app, [
        "profile", "restore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.output
    assert profiles.list_profiles(path=store) == []


def test_cli_show_link(store):
    from vpnm.cli import app as cli_app
    _add(REALITY_RAW, "phone", store)
    result = _runner().invoke(cli_app, ["profile", "show", "phone", "--link"])
    assert result.exit_code == 0, result.output
    assert REALITY_RAW in result.output
