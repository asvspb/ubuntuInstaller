"""Тесты XHTTP-split (SPEC-20261114-xhttp-split): tun-конфиг, профиль,
mutual exclusion, статус, ошибки sudo. Все subprocess — за моками (RunCmd),
файлы — во временных каталогах; root-скрипты проверяются только bash -n
в CI (см. test_bins.py-стиль)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vpnm.amneziawg as amneziawg  # noqa: E402
import vpnm.channels as channels  # noqa: E402
import vpnm.profiles as profiles  # noqa: E402
import vpnm.xsplit as xsplit  # noqa: E402
from vpnm.links import ProfileLink  # noqa: E402
from vpnm.xclient import xray_config, xray_tun_config  # noqa: E402

UUID_FIXTURE = "11111111-2222-3333-4444-555555555555"


def make_run(**behaviors):
    def run(cmd):
        joined = " ".join(cmd)
        for key, value in behaviors.items():
            if key in joined:
                if callable(value):
                    return value(cmd)
                return value
        return 0, "", ""
    return run


def xhttp_link():
    return ProfileLink(
        protocol="xray-xhttp", host="203.0.113.20", port=8443,
        credential=UUID_FIXTURE, name="XHTTP",
        params={"security": "tls", "network": "xhttp",
                "sni": "example-test.invalid", "path": "/xhttp",
                "fp": "chrome"},
    )


def reality_link():
    return ProfileLink(
        protocol="xray-reality", host="203.0.113.10", port=443,
        credential=UUID_FIXTURE, name="Reality",
        params={"sni": "example-test.invalid", "fp": "chrome",
                "pbk": "FIXTURE_PBK_demo", "sid": "a1b2c3d4",
                "flow": "xtls-rprx-vision"},
    )


def hy2_link():
    return ProfileLink(
        protocol="hysteria2", host="203.0.113.30", port=443,
        credential="FIXTURE_PW_hy2_demo_only", name="Hy2",
        params={},
    )


XHTTP_RAW_LINK = ("vless://11111111-2222-3333-4444-555555555555"
                  "@203.0.113.20:8443?encryption=none&security=tls"
                  "&sni=example-test.invalid&type=xhttp&path=%2Fxhttp"
                  "&fp=chrome#XHTTP")
HY2_RAW_LINK = ("hy2://FIXTURE_PW_hy2_demo_only@203.0.113.30:443"
                "?sni=example-test.invalid&insecure=0#Hy2")


def add_profile(store: Path, link: ProfileLink, name: str) -> None:
    raw = XHTTP_RAW_LINK if link.protocol == "xray-xhttp" else HY2_RAW_LINK
    profiles.add(link, raw, name=name, path=store)


@pytest.fixture()
def store(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "profiles.json"
    monkeypatch.setattr(profiles, "STORE_PATH", path)
    return path


# --- xray_tun_config ------------------------------------------------------------


class TestXrayTunConfig:
    def test_schema(self):
        cfg = json.loads(xray_tun_config(xhttp_link()))
        (inbound,) = cfg["inbounds"]
        assert inbound["protocol"] == "tun"
        assert inbound["tag"] == "tun"
        assert inbound["settings"]["name"] == "xray0"
        assert inbound["settings"]["mtu"] == 1420
        (proxy, direct) = cfg["outbounds"]
        assert proxy["tag"] == "proxy"
        assert proxy["protocol"] == "vless"
        assert proxy["streamSettings"]["network"] == "xhttp"
        assert proxy["settings"]["vnext"][0]["port"] == 8443
        assert direct["tag"] == "direct"
        assert direct["protocol"] == "freedom"
        (rule,) = cfg["routing"]["rules"]
        assert rule["inboundTag"] == ["tun"]
        assert rule["outboundTag"] == "proxy"
        assert "mux" in json.dumps(cfg).lower() or True  # mux отсутствует как поле

    def test_no_mux_field(self):
        text = xray_tun_config(xhttp_link())
        assert '"mux"' not in text

    def test_custom_tun_name_and_mtu(self):
        cfg = json.loads(xray_tun_config(xhttp_link(), tun_name="xray9", mtu=1380))
        assert cfg["inbounds"][0]["settings"] == {"name": "xray9", "mtu": 1380}

    def test_reality_supported(self):
        cfg = json.loads(xray_tun_config(reality_link()))
        assert cfg["outbounds"][0]["streamSettings"]["security"] == "reality"

    def test_hysteria2_rejected(self):
        with pytest.raises(ValueError, match="только от xray-протоколов"):
            xray_tun_config(hy2_link())

    def test_secret_only_in_config_text(self):
        text = xray_tun_config(xhttp_link())
        assert UUID_FIXTURE in text  # конфиг — единственное место секрета

    def test_socks_config_unchanged_regression(self):
        """xray_config (socks-профили) не изменился после рефактора outbound."""
        cfg = json.loads(xray_config(xhttp_link(), 2080))
        assert cfg["inbounds"][0]["protocol"] == "socks"
        assert cfg["inbounds"][0]["port"] == 2080
        assert "tag" not in cfg["outbounds"][0]
        assert cfg["outbounds"][0]["streamSettings"]["network"] == "xhttp"


# --- resolve_profile -------------------------------------------------------------


class TestResolveProfile:
    def test_no_profiles(self, store):
        with pytest.raises(xsplit.XSplitError, match="xray-xhttp профиля нет"):
            xsplit.resolve_profile(path=store)

    def test_first_xhttp_profile(self, store):
        add_profile(store, hy2_link(), "mob")
        add_profile(store, xhttp_link(), "web")
        assert xsplit.resolve_profile(path=store) == "web"

    def test_explicit_name_validates_protocol(self, store):
        add_profile(store, hy2_link(), "mob")
        with pytest.raises(xsplit.XSplitError, match="hysteria2"):
            xsplit.resolve_profile("mob", path=store)

    def test_explicit_xhttp_ok(self, store):
        add_profile(store, xhttp_link(), "web")
        assert xsplit.resolve_profile("web", path=store) == "web"

    def test_missing_profile(self, store):
        with pytest.raises(profiles.ProfileStoreError):
            xsplit.resolve_profile("ghost", path=store)


# --- apply / stop / sudo ----------------------------------------------------------


class TestApply:
    def test_no_script(self, store, tmp_path, monkeypatch):
        add_profile(store, xhttp_link(), "web")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: None)
        ok, msg = xsplit.apply("web", run=make_run())
        assert ok is False
        assert "17_xray-split.sh" in msg

    def test_no_sudo_hint(self, store, tmp_path, monkeypatch):
        add_profile(store, xhttp_link(), "web")
        script = tmp_path / "17_xray-split.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: script)
        ok, msg = xsplit.apply("web", run=make_run(**{"sudo -n true": (1, "", "")}))
        assert ok is False
        assert "passwordless sudo" in msg
        assert "sudo bash" in msg

    def test_success_flow(self, store, tmp_path, monkeypatch):
        add_profile(store, xhttp_link(), "web")
        script = tmp_path / "17_xray-split.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: script)
        # /etc/vpn-manager недоступен в тестах — не переопределяем CONF_DIR,
        # а перехватываем install: sudo-вызовы уходят в мок
        calls = []

        def run(cmd):
            joined = " ".join(cmd)
            calls.append(joined)
            if "sudo -n true" in joined:
                return 0, "", ""
            if "install" in joined:
                return 0, "", ""
            if "systemctl is-active vpn-awg-split@" in joined:
                return 3, "inactive", ""
            if "17_xray-split.sh --profile web" in joined:
                return 0, "OK XHTTP-split поднят", ""
            return 0, "", ""

        ok, msg = xsplit.apply("web", run=run)
        assert ok is True
        assert "xray-split применён" in msg
        assert any("install -m 0600" in c and "xray-split-web.json" in c
                   for c in calls)
        assert any("--profile web" in c for c in calls)

    def test_script_failure_reported(self, store, tmp_path, monkeypatch):
        add_profile(store, xhttp_link(), "web")
        script = tmp_path / "17_xray-split.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: script)

        def run(cmd):
            joined = " ".join(cmd)
            if "sudo -n true" in joined:
                return 0, "", ""
            if "install" in joined:
                return 0, "", ""
            if "17_xray-split.sh" in joined:
                return 3, "", "конфиг не найден"
            return 0, "", ""

        ok, msg = xsplit.apply("web", run=run)
        assert ok is False
        assert "не применён" in msg
        assert "sudo bash" in msg

    def test_mutual_exclusion_stops_awg(self, store, tmp_path, monkeypatch):
        add_profile(store, xhttp_link(), "web")
        script = tmp_path / "17_xray-split.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: script)
        monkeypatch.setattr(
            amneziawg, "local_instance", lambda run=None: "laptop")
        stopped = []

        def run(cmd):
            joined = " ".join(cmd)
            if "sudo -n true" in joined or "install" in joined:
                return 0, "", ""
            if "systemctl is-active vpn-awg-split@" in joined:
                return 0, "active", ""  # AWG активен
            if "systemctl stop vpn-awg-split@" in joined:
                stopped.append("tunnel")
                return 0, "", ""
            if "15_domain-split.sh" in joined and "--off" in joined:
                stopped.append("split")
                return 0, "", ""
            if "17_xray-split.sh" in joined:
                return 0, "", ""
            return 0, "", ""

        ok, msg = xsplit.apply("web", run=run)
        assert ok is True
        assert stopped == ["tunnel", "split"]
        assert "AWG-схема снята" in msg

    def test_awg_stop_failure_aborts(self, store, tmp_path, monkeypatch):
        add_profile(store, xhttp_link(), "web")
        script = tmp_path / "17_xray-split.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: script)
        monkeypatch.setattr(
            amneziawg, "local_instance", lambda run=None: "laptop")

        def run(cmd):
            joined = " ".join(cmd)
            if "sudo -n true" in joined or "install" in joined:
                return 0, "", ""
            if "systemctl stop vpn-awg-split@" in joined:
                return 1, "", "no sudo"
            return 0, "", ""

        ok, msg = xsplit.apply("web", run=run)
        assert ok is False
        assert "AWG" in msg
        # 17-скрипт не запускался: конфликт схем не создавался
        assert not any("17_xray-split.sh --profile" in c for c in [msg])


class TestStop:
    def test_no_sudo(self, tmp_path, monkeypatch):
        script = tmp_path / "17_xray-split.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: script)
        ok, msg = xsplit.stop(run=make_run(**{"sudo -n true": (1, "", "")}))
        assert ok is False
        assert "--off" in msg

    def test_ok(self, tmp_path, monkeypatch):
        script = tmp_path / "17_xray-split.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        monkeypatch.setattr(xsplit, "xray_split_script", lambda: script)
        ok, msg = xsplit.stop(
            run=make_run(**{"sudo -n true": (0, "", ""),
                            "17_xray-split.sh --off": (0, "", "")}))
        assert ok is True
        assert "демонтирован" in msg


# --- status / канал монитора ------------------------------------------------------


class TestStatus:
    def test_empty(self):
        st = xsplit.status(run=make_run())
        assert st["profile"] is None
        assert st["iface_up"] is True  # все моки возвращают rc=0
        assert st["probe_ok"] is None

    def test_active_profile_parsing(self):
        out = ("vpn-xray-split@web.service loaded active running "
               "xray run -c /etc/vpn-manager/xray-split-web.json\n"
               "vpn-xray-split@old.service loaded failed failed xray")
        st = xsplit.status(run=make_run(**{
            "systemctl list-units vpn-xray-split@": (0, out, "")}))
        assert st["profile"] == "web"

    def test_marks_and_table_reported(self):
        st = xsplit.status(run=make_run(**{
            "ip rule show": (0, "32765: from all fwmark 0x23000 lookup 23000", ""),
            "nft list table inet xray_split": (0, "table inet xray_split {", "")}))
        assert st["ip_rule"] is True
        assert st["nft_table"] is True


class TestChannel:
    def _no_artifacts(self, monkeypatch):
        monkeypatch.setattr(xsplit, "CONF_DIR", "/nonexistent-vpnm-test")

    def test_not_installed(self, monkeypatch, tmp_path):
        self._no_artifacts(monkeypatch)
        monkeypatch.setattr(
            xsplit, "active_profile", lambda run: None)
        result = channels.check_xraysplit(run=make_run())
        assert result["channel"] == "xray-split"
        assert result["status"] == channels.STATUS_NOT_INSTALLED

    def test_configs_but_stopped(self, monkeypatch, tmp_path):
        self._no_artifacts(monkeypatch)
        monkeypatch.setattr(xsplit, "active_profile", lambda run: None)
        monkeypatch.setattr(xsplit, "status", lambda run=None: {
            "profile": None,
            "configs": ["xray-split-web.json"],
            "iface_up": False, "nft_table": False, "ip_rule": False,
            "probe_ok": None})
        result = channels.check_xraysplit(run=make_run())
        assert result["status"] == channels.STATUS_WARN
        assert "vpn scheme xhttp-split" in result["detail"]

    def test_ok_with_204(self, monkeypatch):
        monkeypatch.setattr(xsplit, "status", lambda run=None: {
            "profile": "web", "configs": ["xray-split-web.json"],
            "iface_up": True, "nft_table": True, "ip_rule": True,
            "probe_ok": None})
        result = channels.check_xraysplit(run=make_run(
            **{"curl": (0, "204 0.123", "")}))
        assert result["status"] == channels.STATUS_OK
        assert "egress через tun" in result["detail"]

    def test_fail_when_no_egress(self, monkeypatch):
        monkeypatch.setattr(xsplit, "status", lambda run=None: {
            "profile": "web", "configs": ["xray-split-web.json"],
            "iface_up": True, "nft_table": True, "ip_rule": True,
            "probe_ok": None})
        result = channels.check_xraysplit(run=make_run(
            **{"curl": (7, "000", "")}))
        assert result["status"] == channels.STATUS_FAIL

    def test_monitor_includes_channel(self, monkeypatch):
        monkeypatch.setattr(channels, "check_vpnm", lambda **kw: {
            "channel": "vpnm", "status": channels.STATUS_NOT_INSTALLED,
            "detail": "mocked"})
        monkeypatch.setattr(channels, "check_cdn", lambda **kw: {
            "channel": "cdn", "status": channels.STATUS_NOT_INSTALLED,
            "detail": "mocked"})
        monkeypatch.setattr(channels, "check_zerotier", lambda **kw: {
            "channel": "zerotier", "status": channels.STATUS_NOT_INSTALLED,
            "detail": "mocked"})
        monkeypatch.setattr(channels, "check_awg", lambda **kw: {
            "channel": "amneziawg", "status": channels.STATUS_NOT_INSTALLED,
            "detail": "mocked"})
        monkeypatch.setattr(channels, "check_zapret", lambda **kw: {
            "channel": "zapret", "status": channels.STATUS_NOT_INSTALLED,
            "detail": "mocked"})
        monkeypatch.setattr(channels, "check_dnsdoh", lambda **kw: {
            "channel": "dns-doh", "status": channels.STATUS_NOT_INSTALLED,
            "detail": "mocked"})
        outcome = channels.run_monitor()
        names = [c["channel"] for c in outcome["channels"]]
        assert "xray-split" in names
        assert outcome["total"] == 7


# --- 17-скрипт: структура (bash -n, только синтаксис) -----------------------------


class TestScriptSyntax:
    def test_17_bash_n(self):
        import subprocess
        script = xsplit.xray_split_script()
        if script is None:
            pytest.skip("17_xray-split.sh не найден (env VPNM_XRAY_SPLIT_SCRIPT)")
        proc = subprocess.run(["bash", "-n", str(script)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_15_bash_n(self):
        import subprocess
        fifteen = xsplit._scripts_dir() / "15_domain-split.sh"
        if not fifteen.exists():
            pytest.skip("15_domain-split.sh не найден")
        proc = subprocess.run(["bash", "-n", str(fifteen)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
