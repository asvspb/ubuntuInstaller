"""Tests for bins: mirror order, idempotent skip, zip extraction, version check.

No network: ``fetch`` and ``version_ok`` are injected fakes / monkeypatched.
"""

import io
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import bins  # noqa: E402


def make_zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buf.getvalue()


def test_mirror_urls_order():
    urls = bins.mirror_urls("https://github.com/x/y/releases/download/v1/f")
    assert urls == [
        "https://github.com/x/y/releases/download/v1/f",
        "https://ghproxy.net/https://github.com/x/y/releases/download/v1/f",
        "https://ghfast.top/https://github.com/x/y/releases/download/v1/f",
    ]


def test_fetch_falls_through_to_next_mirror(monkeypatch):
    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    opened = []

    def fake_urlopen(request, timeout):
        opened.append(request.full_url)
        if len(opened) == 1:
            raise OSError("mirror down")
        return FakeResp(b"data")

    monkeypatch.setattr(bins.urllib.request, "urlopen", fake_urlopen)
    data = bins._fetch(bins.mirror_urls("https://github.com/a/b"))
    assert data == b"data"
    assert len(opened) == 2


def test_fetch_all_fail_raises(monkeypatch):
    def fake_urlopen(request, timeout):
        raise OSError("unreachable")

    monkeypatch.setattr(bins.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(bins.BinError, match="all download mirrors failed"):
        bins._fetch(bins.mirror_urls("https://github.com/a/b"))


def test_install_xray_extracts_all_members(tmp_path):
    blob = make_zip({"xray": b"payload-bin",
                     "geoip.dat": b"payload-geoip",
                     "geosite.dat": b"payload-geosite"})
    calls = []

    def fake_fetch(urls):
        calls.append(urls)
        return blob

    result = bins.install_xray(tmp_path, fetch=fake_fetch,
                               version_ok=lambda p: True)
    assert result == "installed"
    assert len(calls) == 1
    assert calls[0][0] == bins.XRAY_URL
    assert calls[0][1].startswith("https://ghproxy.net/")
    assert calls[0][2].startswith("https://ghfast.top/")
    for member in ("xray", "geoip.dat", "geosite.dat"):
        assert (tmp_path / member).exists()
    assert os.stat(tmp_path / "xray").st_mode & 0o777 == 0o755


def test_install_xray_missing_member_raises(tmp_path):
    blob = make_zip({"xray": b"bin"})
    with pytest.raises(bins.BinError, match="missing members"):
        bins.install_xray(tmp_path, fetch=lambda urls: blob,
                          version_ok=lambda p: True)


def test_install_skip_when_version_matches(tmp_path):
    target = tmp_path / "xray"
    target.write_bytes(b"old-but-correct")
    target.chmod(0o755)

    def no_download(urls):
        raise AssertionError("must not download when version matches")

    result = bins.install_xray(tmp_path, fetch=no_download,
                               version_ok=lambda p: True)
    assert result == "skipped"
    assert target.read_bytes() == b"old-but-correct"


def test_install_hysteria_skip_and_install(tmp_path):
    target = tmp_path / "hysteria"
    target.write_bytes(b"v2.13.0-fake")
    target.chmod(0o755)

    def no_download(urls):
        raise AssertionError("no download expected")

    result = bins.install_hysteria(tmp_path, fetch=no_download,
                                   version_ok=lambda p: True)
    assert result == "skipped"

    target.unlink()
    result = bins.install_hysteria(tmp_path, fetch=lambda u: b"new-binary",
                                   version_ok=lambda p: True)
    assert result == "installed"
    assert target.read_bytes() == b"new-binary"
    assert target.stat().st_mode & 0o777 == 0o755


def test_install_rejects_bad_version_after_download(tmp_path):
    blob = make_zip({"xray": b"bin", "geoip.dat": b"", "geosite.dat": b""})
    with pytest.raises(bins.BinError, match="failed version check"):
        bins.install_xray(tmp_path, fetch=lambda urls: blob,
                          version_ok=lambda p: False)


def test_version_check_output_parsing(tmp_path):
    fake = tmp_path / "xray"
    fake.write_text("#!/bin/sh\n")
    outputs = {"text": "Xray 25.1.1 (Xray, Penetrates All.)"}

    def fake_run(cmd, capture_output, text, check, timeout):
        class Proc:
            stdout = outputs["text"]
            stderr = ""
        return Proc()

    monkeypatch_run = fake_run
    orig = bins.subprocess.run
    bins.subprocess.run = monkeypatch_run  # type: ignore[assignment]
    try:
        assert bins.xray_version_ok(fake) is True
        outputs["text"] = "Xray 24.9.30"
        assert bins.xray_version_ok(fake) is False
        assert bins.hysteria_version_ok(fake) is False
        outputs["text"] = "Version: v2.13.0"
        assert bins.hysteria_version_ok(fake) is True
    finally:
        bins.subprocess.run = orig


def test_install_all_reports_both(tmp_path):
    blob = make_zip({"xray": b"bin", "geoip.dat": b"", "geosite.dat": b""})
    payloads = iter([blob, b"hy"])

    def fake_fetch(urls):
        return next(payloads)

    results = bins.install_all(tmp_path, fetch=fake_fetch,
                               xray_ok=lambda p: True,
                               hysteria_ok=lambda p: True)
    assert dict(results) == {"xray": "installed", "hysteria": "installed"}
