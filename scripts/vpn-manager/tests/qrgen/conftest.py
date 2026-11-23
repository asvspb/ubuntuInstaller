"""Общие фикстуры для тестов ``vpn qr`` (WP-QR3).

Собирают «живую топологию» в ``tmp_path`` и подменяют всё внешнее:
пути store/mode/~/.awg//etc, ``sudo -n cat`` и сам ``qrencode``. Живая
машина не читается и не меняется ни одним тестом.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm import amneziawg, mode, profiles  # noqa: E402
from vpnm.qrgen import render, resolver  # noqa: E402

REALITY_DIRECT = (
    "vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT&sid="
    "&flow=xtls-rprx-vision&fp=chrome#asv2-reality")
REALITY_RELAY = (
    "vless://11111111-2222-3333-4444-000000000002@185.80.91.135:443"
    "?security=reality&sni=ya.ru&pbk=FIXTURE_PBK_RELAY&sid=02"
    "&flow=xtls-rprx-vision&fp=chrome#relay-reality")
HY2_LINK = (
    "hysteria2://FIXTURE_PW_HY2@203.0.113.20:443/?sni=asv2.example.invalid#asv2-hy2")
RELAY_DOMAIN = "relay.asvpersonal.ru"


def awg_text(endpoint: str, *, address: str = "10.9.0.2/32",
             dns: bool = True, keepalive: bool = True,
             table_off: bool = False) -> str:
    lines = ["[Interface]", f"Address = {address}"]
    if dns:
        lines.append("DNS = 1.1.1.1")
    if table_off:
        lines.append("Table = off")
    lines += [
        "PrivateKey = FIXTURE_PW_CLIENT_AWG",
        "MTU = 1280",
        "Jc = 4",
        "S1 = 85",
        "H1 = 2093687166",
        "",
        "[Peer]",
        "PublicKey = FIXTURE_PW_SERVER_AWG",
        "PresharedKey = FIXTURE_PW_PSK_AWG",
        f"Endpoint = {endpoint}",
        "AllowedIPs = 0.0.0.0/0",
    ]
    if keepalive:
        lines.append("PersistentKeepalive = 25")
    return "\n".join(lines) + "\n"


class LiveEnv:
    """Пути изолированного стенда + запись конфигов."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.store = tmp_path / "profiles.json"
        self.awg_dir = tmp_path / "awg"
        self.awg_dir.mkdir()
        self.etc_dir = tmp_path / "etc"
        self.etc_dir.mkdir()
        self.mode_payload = {
            "schema": "vpnm.mode/1", "mode": "direct", "proto": "amneziawg",
            "iface": "laptop", "unit": "vpn-awg-split@laptop.service",
        }
        self.records = []

    def add_profile(self, name: str, link: str, protocol: str) -> None:
        self.records.append({"name": name, "protocol": protocol, "link": link})
        self.store.write_text(
            json.dumps({"profiles": self.records}, indent=2) + "\n",
            encoding="utf-8")

    def live_store(self) -> None:
        self.add_profile("asv2-reality", REALITY_DIRECT, "xray-reality")
        self.add_profile("relay-reality", REALITY_RELAY, "xray-reality")
        self.add_profile("asv2-hy2", HY2_LINK, "hysteria2")

    def live_awg(self) -> None:
        relay = f"{RELAY_DOMAIN}:443"
        (self.awg_dir / "android.conf").write_text(
            awg_text(relay, address="10.10.0.4/32"), encoding="utf-8")
        (self.awg_dir / "windows.conf").write_text(
            awg_text(relay, address="10.10.0.5/32"), encoding="utf-8")
        (self.awg_dir / "relay.conf").write_text(
            awg_text(relay, address="10.10.0.2/32", dns=False,
                     keepalive=False), encoding="utf-8")
        (self.etc_dir / "laptop.conf").write_text(
            awg_text("213.108.23.119:51825", dns=False, keepalive=False,
                     table_off=True), encoding="utf-8")


def _install_sudo(monkeypatch, etc_dir: Path) -> list:
    calls = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001 - повторяет subprocess.run
        calls.append(list(cmd))
        target = Path(cmd[-1])
        if str(target).startswith(str(etc_dir)) and target.is_file():
            return SimpleNamespace(returncode=0,
                                   stdout=target.read_text(encoding="utf-8"),
                                   stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="No such file")

    monkeypatch.setattr(resolver, "subprocess", SimpleNamespace(run=fake_run))
    return calls


def _install_qrencode(monkeypatch) -> list:
    """Заглушка qrencode: терминал — ANSI-матрица, PNG/SVG — байты в -o."""
    calls = []

    def fake_run(cmd, input=None, text=None, capture_output=True,
                 check=False, **kwargs):  # noqa: ANN001 - повторяет subprocess.run
        calls.append(list(cmd))
        kind = cmd[cmd.index("-t") + 1] if "-t" in cmd else ""
        if kind in ("PNG", "SVG"):
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"IMAGE-BYTES")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        qr = "\x1b[40;37;1m" + "█" * 10 + "\x1b[0m\n" * 2
        return SimpleNamespace(returncode=0, stdout=qr, stderr="")

    monkeypatch.setattr(render, "subprocess", SimpleNamespace(run=fake_run))
    monkeypatch.setattr(render, "shutil", SimpleNamespace(
        which=lambda name: "/usr/bin/qrencode",
        get_terminal_size=lambda fallback=(80, 24): os.terminal_size((120, 40))))
    return calls


@pytest.fixture()
def live_env(tmp_path, monkeypatch):
    state = LiveEnv(tmp_path)
    monkeypatch.setattr(profiles, "STORE_PATH", state.store)
    monkeypatch.setattr(amneziawg, "LOCAL_CONF_DIR", state.awg_dir)
    monkeypatch.setattr(amneziawg, "AWG_SYSTEM_CONF_DIR", str(state.etc_dir))
    monkeypatch.setattr(mode, "read_mode",
                        lambda path=None: dict(state.mode_payload))
    state.sudo_calls = _install_sudo(monkeypatch, state.etc_dir)
    state.qrencode_calls = _install_qrencode(monkeypatch)
    return state


@pytest.fixture(autouse=True)
def _isolate_artifacts_dir(tmp_path, monkeypatch):
    """v1.1 (WP-QR6): авто-сохранение PNG не должно писать в живой HOME.

    ``render.artifacts_dir()`` читает env при вызове, поэтому переопределения
    достаточно для всех qrgen-тестов.
    """
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("VPN_MANAGER_ARTIFACTS_DIR", str(artifacts))
    return artifacts
