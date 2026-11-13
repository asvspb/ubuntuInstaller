"""Tests for vpnm/speed.py (live speed test): parsing, endpoints, verdict."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import profiles, speed  # noqa: E402
from vpnm.links import parse_link  # noqa: E402


def test_parse_curl_speed_valid():
    # 10 MB за 2 с = 40 Мбит/с
    assert speed.parse_curl_speed("10000000 2.0") == pytest.approx(40.0)


def test_parse_curl_speed_rejects_bad():
    assert speed.parse_curl_speed("") is None
    assert speed.parse_curl_speed("0 1.0") is None      # 0 байт
    assert speed.parse_curl_speed("1000 0") is None     # 0 времени
    assert speed.parse_curl_speed("abc def") is None


class FakeRun:
    """run_cmd stub: отвечает по URL-подстроке."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        for needle, result in self.answers.items():
            if needle in joined:
                return result
        return 0, "", ""


def test_measure_bulk_falls_back_to_second_endpoint():
    run = FakeRun({
        "proof.ovh.net": (28, "", "timeout"),
        "speed.cloudflare.com": (0, "3000000 2.0", ""),
    })
    mbps, label = speed.measure_bulk(
        None, 1, 30, run,
        endpoints=[("ovh", "https://proof.ovh.net/files/10Mb.dat"),
                   ("cf", "https://speed.cloudflare.com/__down?bytes=3000000")])
    assert label == "cf"
    assert mbps == pytest.approx(12.0)


def test_measure_bulk_sums_flows():
    run = FakeRun({"speed.cloudflare.com": (0, "3000000 2.0", "")})
    mbps, _ = speed.measure_bulk(
        None, 2, 30, run,
        endpoints=[("cf", "https://speed.cloudflare.com/__down?bytes=3000000")])
    assert mbps == pytest.approx(24.0)


def test_measure_bulk_none_when_all_fail():
    run = FakeRun({"": (28, "", "")})
    mbps, label = speed.measure_bulk(
        None, 1, 30, run, endpoints=[("x", "https://x.invalid/f")])
    assert mbps is None and label == ""


def test_measure_latency_parses_last_line():
    run = FakeRun({"generate_204": (0, "0.123456\n", "")})
    assert speed.measure_latency(None, 10, run) == pytest.approx(0.123456)


def test_measure_egress_returns_ip():
    run = FakeRun({"api.ipify.org": (0, "203.0.113.7\n", "")})
    assert speed.measure_egress(None, run) == "203.0.113.7"


def test_verdict_picks_fastest_and_stable():
    channels = [
        {"name": "a", "speed_mbps": 10.0, "speed_samples": [10.0, 10.1],
         "error": ""},
        {"name": "b", "speed_mbps": 40.0, "speed_samples": [5.0, 75.0],
         "error": ""},
    ]
    v = speed.verdict(channels)
    assert v["fastest"] == "b"
    assert v["most_stable"] == "a"
    assert any("нестабильная" in n for n in v["notes"])


def test_verdict_empty_when_all_failed():
    v = speed.verdict([{"name": "a", "speed_mbps": None, "error": "x"}])
    assert v["fastest"] is None and v["notes"] == ["a: x"]


class TestMeasureChannel:
    """События и заполнение канала без сети/root."""

    def _channel(self, kind="socks"):
        return speed.Channel(name="phone", kind=kind, protocol="xray-reality",
                             socks_port=2080)

    def test_socks_channel_events_and_values(self, monkeypatch):
        events = []
        calls = []
        monkeypatch.setattr(speed.runners, "up", lambda name: calls.append(("up", name)))
        monkeypatch.setattr(speed.runners, "down", lambda name: calls.append(("down", name)))
        monkeypatch.setattr(speed, "measure_egress", lambda port, run: "203.0.113.7")
        monkeypatch.setattr(speed, "measure_latency", lambda port, t, run: 0.25)
        monkeypatch.setattr(speed, "measure_bulk", lambda port, flows, t, run: (42.0, "ovh10"))
        # ждать реальный socks не нужно — подменяем ожидание
        monkeypatch.setattr(speed, "wait_socks", lambda port, timeout=30: True)
        monkeypatch.setattr(speed, "SAMPLE_PAUSE", 0)

        ch = self._channel()
        speed.measure_channel(ch, samples=2, flows=1, timeout=5,
                              run=lambda cmd: (0, "", ""),
                              on_event=events.append)
        assert ch.speed_mbps == pytest.approx(42.0)
        assert ch.endpoint == "ovh10"
        assert ch.latency_s == pytest.approx(0.25)
        assert ch.status == "готово"
        kinds = [e["type"] for e in events]
        assert kinds.count("latency") == 2
        assert kinds.count("speed") == 2
        assert calls == [("up", "phone"), ("down", "phone")]

    def test_awg_channel_starts_and_stops(self, monkeypatch):
        started = {"start": 0, "stop": 0}
        monkeypatch.setattr(speed, "_awg_start", lambda: started.update(start=started["start"] + 1))
        monkeypatch.setattr(speed, "_awg_stop", lambda: started.update(stop=started["stop"] + 1))
        monkeypatch.setattr(speed, "measure_egress", lambda port, run: "203.0.113.7")
        monkeypatch.setattr(speed, "measure_latency", lambda port, t, run: 0.3)
        monkeypatch.setattr(speed, "measure_bulk", lambda port, flows, t, run: (60.0, "ovh10"))
        monkeypatch.setattr(speed, "SAMPLE_PAUSE", 0)

        ch = speed.Channel(name="awg", kind="awg", protocol="amneziawg-split")
        speed.measure_channel(ch, samples=1, flows=1, timeout=5,
                              run=lambda cmd: (0, "", ""), on_event=lambda e: None)
        assert started == {"start": 1, "stop": 1}
        assert ch.speed_mbps == pytest.approx(60.0)


# --- CDN-профили по умолчанию пропускаются ------------------------------------

VLESS_REALITY = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=01#phone"
)
VLESS_CDN = (
    "vless://11111111-2222-3333-4444-666666666666@cdn.example.invalid:2053"
    "?encryption=none&security=tls&sni=cdn.example.invalid&type=ws"
    "&host=cdn.example.invalid&path=%2Fcdnws#cdn-home"
)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    profiles.add(parse_link(VLESS_REALITY), raw_link=VLESS_REALITY,
                 name="phone", socks_port=2080)
    profiles.add(parse_link(VLESS_CDN), raw_link=VLESS_CDN,
                 name="cdn-home", socks_port=2081)
    return tmp_path


class TestCdnSkipped:
    """CDN через CF не отдаёт bulk — по умолчанию не меряем."""

    def test_collect_skips_cdn_by_default(self, store):
        channels = speed.collect_channels(["phone", "cdn-home"], include_awg=False)
        assert [c.name for c in channels] == ["phone"]

    def test_collect_includes_cdn_on_request(self, store):
        channels = speed.collect_channels(
            ["phone", "cdn-home"], include_awg=False, include_cdn=True)
        assert [c.name for c in channels] == ["phone", "cdn-home"]

    def test_run_reports_skipped(self, store, monkeypatch):
        monkeypatch.setattr(
            speed, "measure_channel",
            lambda ch, samples, flows, timeout, run, on_event: ch)
        report = speed.run_speed(["phone", "cdn-home"], include_awg=False,
                                 run=lambda cmd: (0, "", ""))
        assert report["skipped"] == ["cdn-home"]
        assert [c["name"] for c in report["channels"]] == ["phone"]
