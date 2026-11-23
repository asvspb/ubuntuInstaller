"""WP-QR3: e2e PNG → zbarimg (AC-QR-8).

Реальные ``qrencode`` и ``zbarimg``; если любого нет — тест пропускается.
Никакой сети и системных мутаций: только временный каталог.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.qrgen import builders, models, render  # noqa: E402

REALITY_LINK = (
    "vless://11111111-2222-3333-4444-000000000001@203.0.113.10:443"
    "?security=reality&sni=www.google.com&pbk=FIXTURE_PBK_DIRECT&sid="
    "&flow=xtls-rprx-vision&fp=chrome#asv2-reality")
AWG_RAW = "\n".join([
    "[Interface]",
    "Address = 10.9.0.2/32",
    "DNS = 1.1.1.1",
    "PrivateKey = FIXTURE_PW_CLIENT_AWG",
    "MTU = 1280",
    "Jc = 4",
    "S1 = 85",
    "H1 = 2093687166",
    "",
    "[Peer]",
    "PublicKey = FIXTURE_PW_SERVER_AWG",
    "PresharedKey = FIXTURE_PW_PSK_AWG",
    "Endpoint = 192.0.2.119:51825",
    "AllowedIPs = 0.0.0.0/0",
    "PersistentKeepalive = 25",
]) + "\n"

pytestmark = pytest.mark.skipif(
    shutil.which("qrencode") is None or shutil.which("zbarimg") is None,
    reason="requires qrencode and zbarimg")


def _decode(path: Path) -> str:
    proc = subprocess.run(["zbarimg", "--raw", "-q", str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.rstrip("\n")


@pytest.mark.parametrize("profile_name,link,raw_awg,proto", [
    ("asv2-reality", REALITY_LINK, None, models.Proto.REALITY),
    ("laptop", "", AWG_RAW, models.Proto.AWG),
])
def test_png_qr_decodes_to_exact_payload(tmp_path, profile_name, link,
                                         raw_awg, proto):
    resolved = models.ResolvedSource(
        profile_name=profile_name, route=models.Route.DIRECT, proto=proto,
        target_os=models.TargetOS.ANDROID, client="v2rayng",
        endpoint_host="203.0.113.10", endpoint_port=443,
        source="awg" if raw_awg else "profile",
        raw_profile={"name": profile_name, "link": link} if link else None,
        raw_awg_ini=raw_awg)
    built = builders.build_payload(resolved)

    out = tmp_path / "qr.png"
    render.write_image(built, out, image="png")

    assert out.stat().st_size > 0
    # zbarimg --raw добавляет свой \n, а данные AWG INI сами кончаются на \n,
    # поэтому хвостовые переводы строки сравниваем без них: содержимое —
    # байт-в-байт, включая пробелы внутри строк (это и есть проверка §5).
    assert _decode(out).rstrip("\n") == built.payload.rstrip("\n")
