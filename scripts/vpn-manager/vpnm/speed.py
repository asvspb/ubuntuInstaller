"""Live-замер скорости по всем каналам: `vpn speed` (2026-11-13).

Зачем отдельная команда, если есть `vpn probe`:
- probe меряет ОДИН bulk-эндпоинт (speed.cloudflare) и потому врёт, когда
  эндпоинт сам зарезан провайдером (2026-11-13: 4–7 Мбит/с на всех
  протоколах превратились в 60–75 Мбит/с на OVH);
- probe не показывает процесс: пользователь видит только финальную таблицу.

Здесь: несколько эндпоинтов с фолбэком, серийные пробы (И26), и живая
таблица в терминале (rich.Live): статус, задержка, скорость по каждому
каналу обновляются по мере замера. Итог — таблица + рекомендация.

Меряется по одному каналу за раз (никогда параллельно разными протоколами):
профиль up -> latency x N -> bulk x N (flows параллельных потоков) -> down.
AWG (system split) поднимается и снимается автоматически, если не был активен.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import profiles, runners
from .links import PROTOCOL_XRAY_WS
from .probe import SAMPLE_PAUSE
from .runners import run_cmd

LATENCY_URL = "https://www.google.com/generate_204"
# TTFB меряем на лёгкой реальной странице: time_starttransfer включает
# полный стек (TCP+TLS+запрос), но не зависит от bulk-шейпинга провайдера —
# работает даже когда bulk-эндпоинты не отдают байты (CF-канал, 2026-11-13).
TTFB_URL = "https://example.com/"
# Порядок важен: OVH первым — он не шейпится из РФ так, как Cloudflare.
ENDPOINTS: List[Tuple[str, str]] = [
    ("ovh10", "https://proof.ovh.net/files/10Mb.dat"),
    ("cf3", "https://speed.cloudflare.com/__down?bytes=3000000"),
    ("gh-readme", "https://raw.githubusercontent.com/asvspb/my-first-vds/main/README.md"),
]
DEFAULT_SAMPLES = 2
DEFAULT_TTFB_SAMPLES = 3
DEFAULT_FLOWS = 1
DEFAULT_TIMEOUT = 35.0
REPORT_PATH = Path.home() / ".local" / "share" / "vpn-manager" / "last-speed.json"

RunCmd = Callable[[List[str]], Tuple[int, str, str]]
Event = Dict[str, object]
OnEvent = Callable[[Event], None]


@dataclass
class Channel:
    """Один канал замера (socks-профиль или AWG-split)."""

    name: str
    kind: str  # "socks" | "awg"
    protocol: str
    socks_port: Optional[int] = None
    status: str = "ожидание"
    latency_s: Optional[float] = None
    latency_samples: List[Optional[float]] = field(default_factory=list)
    ttfb_p50_ms: Optional[float] = None
    ttfb_p95_ms: Optional[float] = None
    ttfb_samples: List[Optional[float]] = field(default_factory=list)
    speed_mbps: Optional[float] = None
    speed_samples: List[Optional[float]] = field(default_factory=list)
    endpoint: str = ""
    egress: Optional[str] = None
    error: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "protocol": self.protocol,
            "socks_port": self.socks_port,
            "status": self.status,
            "latency_s": self.latency_s,
            "latency_samples": self.latency_samples,
            "ttfb_p50_ms": self.ttfb_p50_ms,
            "ttfb_p95_ms": self.ttfb_p95_ms,
            "ttfb_samples": self.ttfb_samples,
            "speed_mbps": self.speed_mbps,
            "speed_samples": self.speed_samples,
            "endpoint": self.endpoint,
            "egress": self.egress,
            "error": self.error,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Измерения (чистые функции с инъекцией run_cmd)
# ---------------------------------------------------------------------------

def parse_curl_speed(stdout: str) -> Optional[float]:
    """curl -w '%{size_download} %{time_total}' -> Мбит/с; None при сбое."""
    parts = stdout.split()
    if len(parts) != 2:
        return None
    try:
        size = float(parts[0])
        elapsed = float(parts[1])
    except ValueError:
        return None
    if size <= 0 or elapsed <= 0:
        return None
    return size * 8 / elapsed / 1_000_000


def _curl_bulk(url: str, socks_port: Optional[int], timeout: float,
               run: RunCmd) -> Optional[float]:
    cmd = ["curl", "-sL", "-o", "/dev/null", "--max-time", str(int(timeout)),
           "-w", "%{size_download} %{time_total}"]
    if socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    cmd.append(url)
    rc, out, _ = run(cmd)
    if rc != 0:
        return None
    return parse_curl_speed(out)


def measure_bulk(socks_port: Optional[int], flows: int, timeout: float,
                 run: RunCmd,
                 endpoints: List[Tuple[str, str]] = ENDPOINTS
                 ) -> Tuple[Optional[float], str]:
    """Агрегат flows параллельных потоков; первый рабочий эндпоинт.

    Возвращает (Мбит/с, метка эндпоинта). None — ни один эндпоинт не отдал
    байты (в отличие от «0 Мбит/с»: это разные вещи).
    """
    for label, url in endpoints:
        with ThreadPoolExecutor(max_workers=max(1, flows)) as pool:
            futures = [pool.submit(_curl_bulk, url, socks_port, timeout, run)
                       for _ in range(max(1, flows))]
            values = [f.result() for f in futures]
        ok = [v for v in values if v]
        if ok:
            return round(sum(values), 2), label
    return None, ""


def measure_latency(socks_port: Optional[int], timeout: float,
                    run: RunCmd) -> Optional[float]:
    cmd = ["curl", "-s", "-o", "/dev/null", "--max-time", str(int(timeout)),
           "-w", "%{time_total}"]
    if socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    cmd.append(LATENCY_URL)
    rc, out, _ = run(cmd)
    if rc != 0 or not out.strip():
        return None
    try:
        return float(out.splitlines()[-1])
    except ValueError:
        return None


def measure_ttfb(socks_port: Optional[int], timeout: float,
                 run: RunCmd) -> Optional[float]:
    """TTFB через socks: curl %{time_starttransfer}, секунды; None при сбое.

    В отличие от bulk работает даже когда ни один bulk-эндпоинт не отдаёт
    байты (CF-канал): лёгкая страница example.com не зависит от шейпинга.
    """
    cmd = ["curl", "-sL", "-o", "/dev/null", "--max-time", str(int(timeout)),
           "-w", "%{time_starttransfer}"]
    if socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    cmd.append(TTFB_URL)
    rc, out, _ = run(cmd)
    if rc != 0 or not out.strip():
        return None
    try:
        value = float(out.splitlines()[-1])
    except ValueError:
        return None
    return value if value > 0 else None


def percentile(sorted_values: List[float], q: float) -> float:
    """Ближайший-ранг перцентиль (nearest-rank) по отсортированному списку."""
    if not sorted_values:
        raise ValueError("percentile of empty list")
    index = max(0, min(len(sorted_values) - 1,
                       max(0, int(round(q * len(sorted_values) + 0.5)) - 1)))
    return sorted_values[index]


def measure_egress(socks_port: Optional[int], run: RunCmd) -> Optional[str]:
    cmd = ["curl", "-s", "--max-time", "10"]
    if socks_port:
        cmd += ["--socks5-hostname", f"127.0.0.1:{socks_port}"]
    cmd.append("https://api.ipify.org")
    rc, out, _ = run(cmd)
    return out.strip() if rc == 0 and out.strip() else None


# ---------------------------------------------------------------------------
# Оркестрация
# ---------------------------------------------------------------------------

def collect_channels(socks_profiles: List[str], include_awg: bool,
                     include_cdn: bool = False) -> List[Channel]:
    """Каналы для замера.

    CDN-профили (`xray-cdn-ws`) по умолчанию пропускаются: через Cloudflare
    не отдаётся bulk ни с одного источника, замер только тратит время и
    показывает «нет данных». Включаются явно `include_cdn=True`
    (`vpn speed --include-cdn`).
    """
    channels = []
    for name in socks_profiles:
        record = profiles.get(name)
        if not include_cdn and str(record["protocol"]) == PROTOCOL_XRAY_WS:
            continue
        channels.append(Channel(
            name=name,
            kind="socks",
            protocol=str(record["protocol"]),
            socks_port=int(str(record["socks_port"])),
        ))
    if include_awg:
        channels.append(Channel(name="awg", kind="awg",
                                protocol="amneziawg-split"))
    return channels


def wait_socks(port: int, timeout: float = 30.0) -> bool:
    """Дождаться, пока клиент забиндит socks-порт (тесты мокают функцию)."""
    import socket
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def _stop_socks(name: str) -> None:
    try:
        runners.down(name)
    except Exception:  # noqa: BLE001 - остановка не должна ломать отчёт
        pass


def measure_channel(ch: Channel, samples: int, flows: int, timeout: float,
                    run: RunCmd, on_event: OnEvent,
                    ttfb_samples: int = DEFAULT_TTFB_SAMPLES) -> Channel:
    """Замерить один канал: latency xN, egress, TTFB xN, bulk xN (серийно, И26)."""
    def emit(kind: str, **kw: object) -> None:
        on_event({"type": kind, "channel": ch.name, "state": ch.as_dict(), **kw})

    ch.status = "запуск"
    emit("status")
    try:
        if ch.kind == "socks":
            runners.up(ch.name)
            if not wait_socks(int(ch.socks_port)):
                raise RuntimeError(f"socks {ch.socks_port} не поднялся за 30 с")
        else:
            _awg_start()

        ch.egress = measure_egress(ch.socks_port, run)
        emit("egress")

        ch.status = "задержка"
        emit("status")
        for i in range(samples):
            if i:
                time.sleep(SAMPLE_PAUSE)
            value = measure_latency(ch.socks_port, timeout, run)
            ch.latency_samples.append(value)
            ok = [v for v in ch.latency_samples if v is not None]
            ch.latency_s = round(statistics.median(ok), 3) if ok else None
            emit("latency", value=value)

        ch.status = "ttfb"
        emit("status")
        for i in range(ttfb_samples):
            if i:
                time.sleep(SAMPLE_PAUSE)
            value = measure_ttfb(ch.socks_port, timeout, run)
            ch.ttfb_samples.append(value)
            ok = [v for v in ch.ttfb_samples if v is not None]
            if ok:
                ordered = sorted(ok)
                ch.ttfb_p50_ms = round(percentile(ordered, 0.50) * 1000, 0)
                ch.ttfb_p95_ms = round(percentile(ordered, 0.95) * 1000, 0)
            emit("ttfb", value=value)

        ch.status = "скорость"
        emit("status")
        for i in range(samples):
            if i:
                time.sleep(2.0)
            mbps, label = measure_bulk(ch.socks_port, flows, timeout, run)
            ch.speed_samples.append(mbps)
            if mbps is not None:
                ch.endpoint = label
            ok = [v for v in ch.speed_samples if v is not None]
            ch.speed_mbps = round(statistics.median(ok), 2) if ok else None
            emit("speed", value=mbps, endpoint=label)

        if ch.speed_mbps is None:
            if ch.ttfb_p50_ms is not None:
                # bulk не отдался, но TTFB есть: канал жив, только bulk зарезан
                ch.status = "только задержка"
                ch.note = ("bulk не отдал байты (зарезан), но TTFB измерен: "
                           f"p50 {ch.ttfb_p50_ms:.0f} мс")
            else:
                ch.status = "нет данных"
                ch.error = "bulk не отдал байты ни на одном эндпоинте"
        else:
            ch.status = "готово"
    except Exception as exc:  # noqa: BLE001 - отчёт важнее падения
        ch.status = "ошибка"
        ch.error = str(exc)
    finally:
        if ch.kind == "socks":
            _stop_socks(ch.name)
        else:
            _awg_stop()
    emit("status")
    return ch


def _vpn_binary() -> Optional[str]:
    return shutil.which("vpn")


def _awg_start() -> None:
    vpn = _vpn_binary()
    if not vpn:
        raise RuntimeError("команда vpn не найдена в PATH")
    subprocess.run([vpn, "start"], capture_output=True, text=True, timeout=180)


def _awg_stop() -> None:
    vpn = _vpn_binary()
    if vpn:
        subprocess.run([vpn, "reset"], capture_output=True, text=True, timeout=120)


def run_speed(socks_profiles: List[str], samples: int = DEFAULT_SAMPLES,
              flows: int = DEFAULT_FLOWS, timeout: float = DEFAULT_TIMEOUT,
              include_awg: bool = True, include_cdn: bool = False,
              run: RunCmd = run_cmd,
              on_event: Optional[OnEvent] = None,
              ttfb_samples: int = DEFAULT_TTFB_SAMPLES) -> dict:
    """Прогнать все каналы последовательно; вернуть отчёт dict.

    include_cdn=False (по умолчанию) — CDN-профили не меряются (CF не отдаёт
    bulk); в отчёт попадает список пропущенных `skipped`.
    """
    emit: OnEvent = on_event or (lambda _e: None)
    channels = collect_channels(socks_profiles, include_awg, include_cdn)
    measured = {ch.name for ch in channels}
    skipped = [n for n in socks_profiles if n not in measured]
    started = datetime.now(timezone.utc).isoformat()
    for index, ch in enumerate(channels):
        emit({"type": "channel_start", "index": index, "total": len(channels),
              "channel": ch.name, "state": ch.as_dict()})
        measure_channel(ch, samples, flows, timeout, run, emit,
                        ttfb_samples=ttfb_samples)
        emit({"type": "channel_done", "index": index, "total": len(channels),
              "channel": ch.name, "state": ch.as_dict()})
        if ch.kind == "socks":
            time.sleep(1.0)

    report = {
        "started": started,
        "finished": datetime.now(timezone.utc).isoformat(),
        "params": {"samples": samples, "flows": flows, "timeout": timeout,
                   "ttfb_samples": ttfb_samples},
        "channels": [ch.as_dict() for ch in channels],
        "skipped": skipped,
    }
    report["verdict"] = verdict(report["channels"])
    return report


def verdict(channels: List[dict]) -> dict:
    """Быстрейший и «самый стабильный» канал + предупреждения."""
    ok = [c for c in channels if c.get("speed_mbps")]
    if not ok:
        notes = [f"{c['name']}: {c['error']}" for c in channels if c.get("error")]
        return {"fastest": None, "most_stable": None, "notes": notes}
    fastest = max(ok, key=lambda c: c["speed_mbps"])
    stable = min(
        (c for c in ok if len([v for v in c.get("speed_samples") or [] if v]) >= 2),
        key=lambda c: _spread(c.get("speed_samples") or []),
        default=fastest,
    )
    notes = []
    for c in channels:
        if c.get("error"):
            notes.append(f"{c['name']}: {c['error']}")
        samples = [v for v in (c.get("speed_samples") or []) if v]
        if len(samples) >= 2 and _spread(samples) > 0.5 * max(samples):
            notes.append(f"{c['name']}: нестабильная скорость "
                         f"({', '.join(f'{v:.1f}' for v in samples)} Мбит/с)")
    return {"fastest": fastest["name"], "most_stable": stable["name"],
            "notes": notes}


def _spread(values: List[float]) -> float:
    return max(values) - min(values) if values else 0.0
