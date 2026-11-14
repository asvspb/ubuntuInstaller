"""VPN channel availability monitoring.

Each channel check returns ``{channel, status, detail}`` with status
``ok | warn | fail | not_installed``:

- **vpnm**: the active profile + one egress probe (single curl measurement)
  through its SOCKS port (reuses ``probe.measure_latency``);
- **ZeroTier**: service state, ``zerotier-cli status`` ONLINE, active peers
  (``listpeers``: count + min latency), joined networks with assigned IPs
  (``listnetworks``), optional ping of an explicitly given peer address
  (``--zt-ping``).  ZeroTier is archived (owner decision 2026-11-10): its
  absence is ``not_installed`` (warn-colour), not a fail.  When
  zerotier-cli / the service exist but the daemon is down, the status is
  ``warn`` ("installed but not running").  A missing passwordless sudo is a
  ``warn`` too, never a fail;
- **AmneziaWG**: a placeholder slot — looks for binaries / ``*.conf``
  artifacts / an ``amz*|amn*|awg*|amnezia*|wg*`` interface; when nothing exists
  the status is
  ``not_installed`` with "транспорт в разработке (SPEC-20261110)";
- **zapret** (channel 0): DPI-desync on *direct* connections, no tunnel.
  nfqws binary missing → ``not_installed`` (neutral, like the AWG slot).
  Service active → direct canary ``curl https://www.youtube.com/generate_204``
  (NO socks proxy): 204 is ``ok`` ("direct DPI-bypass жив"), anything else
  (000/timeout) is ``fail`` — the strategy stopped working.  Installed but
  not running → ``warn`` (like ZeroTier);
- **dns-doh** (channel 0.5): the local DoH resolver (cloudflared proxy-dns
  on 127.0.0.1:5053, installed by ``scripts/13_dns-doh.sh``) that shields
  the system resolver from ISP DNS hijacking.  Service missing →
  ``not_installed`` (neutral).  Service active + ``dig @127.0.0.1 -p 5053
  www.youtube.com +short`` returns addresses → ``ok`` ("DoH-резолвер жив");
  active but the dig probe is empty → ``fail`` (upstream unreachable /
  hijacked).  Installed but stopped → ``fail`` — without the resolver the
  system DNS silently degrades to the hijacked plaintext path.

Summary: exit code 0 when at least one channel is ``ok``, 1 when every
channel is fail/not_installed.
"""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import probe, profiles, runners
from .runners import RunCmd, run_cmd

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_NOT_INSTALLED = "not_installed"

# status -> colour class for renderers
STATUS_WEIGHT = {
    STATUS_OK: 0,
    STATUS_WARN: 1,
    STATUS_NOT_INSTALLED: 1,
    STATUS_FAIL: 2,
}

Channel = Dict[str, str]

AWG_BIN_NAMES = ("awg", "awg-quick", "amnezia-wg")
AWG_BIN_DIRS = (Path("/usr/local/bin"), Path("/usr/bin"), Path("/usr/sbin"))
AWG_CONF_DIRS = (Path("/etc/amnezia/amneziawg"), Path("/etc/wireguard"))
ZT_CLI_FALLBACK_DIRS = (Path("/usr/bin"), Path("/usr/local/bin"),
                        Path("/usr/sbin"))
AWG_SPEC_NOTE = "транспорт в разработке (SPEC-20261110)"

ZT_BAD_NETWORK_MARKERS = ("ACCESS_DENIED", "NOT_FOUND")


def _result(channel: str, status: str, detail: str) -> Channel:
    return {"channel": channel, "status": status, "detail": detail}


def healthy_count(channels: List[Channel]) -> int:
    return sum(1 for c in channels if c["status"] == STATUS_OK)


# --- vpnm -----------------------------------------------------------------------

def check_vpnm(run: Optional[RunCmd] = None) -> Channel:
    """Active profile + a single egress probe through its SOCKS port."""
    active = runners.get_active()
    if not active:
        return _result("vpnm", STATUS_NOT_INSTALLED,
                       "нет активного профиля (vpn up <name>)")
    try:
        record = profiles.get(active)
    except profiles.ProfileStoreError as exc:
        return _result("vpnm", STATUS_FAIL,
                       f"запись активного профиля '{active}' сломана: {exc}")
    port = int(str(record["socks_port"]))
    latency = probe.measure_latency(port, run=run)
    if latency is None:
        return _result("vpnm", STATUS_FAIL,
                       f"профиль '{active}': запрос через "
                       f"127.0.0.1:{port} не прошёл — туннель не отвечает")
    # D-SPEED: bulk-скорость активного профиля (детекция «тихой деградации»:
    # generate_204 быстр даже под душением — только bulk показывает правду)
    speed = probe.measure_speed(port, run=run)
    speed_note = ""
    status = STATUS_OK
    if speed is not None and speed < probe.DEGRADED_MBPS:
        speed_note = f", DEGRADED: bulk {speed} Mbps < {probe.DEGRADED_MBPS}"
        status = STATUS_FAIL
    elif speed is not None:
        speed_note = f", bulk {speed} Mbps"
    return _result("vpnm", status,
                   f"профиль '{active}' ({record['protocol']}) через "
                   f"127.0.0.1:{port}, egress {round(latency * 1000.0, 1)} ms"
                   + speed_note)


# --- ZeroTier -------------------------------------------------------------------

def _zt_cli_available(run: RunCmd) -> bool:
    rc, _out, _err = run(["which", "zerotier-cli"])
    if rc == 0:
        return True
    return any((d / "zerotier-cli").exists() for d in ZT_CLI_FALLBACK_DIRS)


def _zt_joined_networks(status_out: str) -> List[str]:
    """Network ids of listnetworks rows without ACCESS_DENIED/NOT_FOUND."""
    joined: List[str] = []
    for line in status_out.splitlines()[1:]:  # skip the header line
        line = line.strip()
        if not line or any(marker in line for marker in ZT_BAD_NETWORK_MARKERS):
            continue
        cols = line.split()
        if cols:
            joined.append(cols[0])
    return joined


def _zt_network_rows(status_out: str) -> List[Dict[str, str]]:
    """listnetworks rows as {nwid, name, dev, ips}; bad rows skipped."""
    rows: List[Dict[str, str]] = []
    for line in status_out.splitlines()[1:]:  # skip the header line
        line = line.strip()
        if not line or any(marker in line for marker in ZT_BAD_NETWORK_MARKERS):
            continue
        cols = line.split()
        # <nwid> <name> <mac> <status> <type> <dev> <ips>
        if len(cols) < 7:
            continue
        rows.append({"nwid": cols[0], "name": cols[1], "dev": cols[5],
                     "ips": cols[6]})
    return rows


def _zt_networks_detail(status_out: str) -> str:
    """Human detail for joined networks: 'network <name>: <ip>' per row.

    Networks with an assigned IP on a zt* interface are the healthy case;
    rows without an address degrade to their bare id.
    """
    rows = _zt_network_rows(status_out)
    if not rows:
        return "joined-сетей нет"
    parts: List[str] = []
    for row in rows:
        ips = row["ips"] if row["ips"] != "-" else ""
        dev = row["dev"] if row["dev"].startswith("zt") else ""
        if ips and dev:
            parts.append(f"network '{row['name']}': {ips.split(',')[0]}")
        else:
            parts.append(row["nwid"])
    return "joined-сети: " + ", ".join(parts)


def _zt_peers_detail(run: RunCmd) -> str:
    """Active-peer probe via listpeers; '' when sudo is unavailable.

    latency is the third column of each peer row; rows without a numeric
    latency are skipped from the min-latency computation (but still counted).
    """
    rc, out, _err = run(["sudo", "-n", "zerotier-cli", "listpeers"])
    if rc != 0:
        return ""
    count = 0
    latencies: List[int] = []
    for line in out.splitlines()[1:]:  # skip the "200 listpeers ..." header
        line = line.strip()
        if not line:
            continue
        cols = line.split()
        if len(cols) < 3:
            continue
        count += 1
        try:
            latencies.append(int(cols[2]))
        except ValueError:
            continue
    if count == 0:
        return ""
    if latencies:
        return f", peers: {count}, min latency: {min(latencies)}ms"
    return f", peers: {count}"


def _ping_latency_ms(out: str) -> Optional[float]:
    """Extract rtt from ping output ('time=12.3 ms'); None when absent."""
    match = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
    if match:
        return float(match.group(1))
    return None


def _zt_peer_ping(run: RunCmd, peer_ip: str) -> str:
    """Optional reachability probe of an explicit ZT peer address."""
    rc, out, _err = run(["ping", "-c", "1", "-W", "2", peer_ip])
    if rc != 0:
        return f", ping {peer_ip} не отвечает"
    latency = _ping_latency_ms(out)
    if latency is not None:
        return f", ping {peer_ip} ok ({latency} ms)"
    return f", ping {peer_ip} ok"


def check_zerotier(run: Optional[RunCmd] = None,
                   peer_ip: Optional[str] = None) -> Channel:
    """ZeroTier channel: archived transport; absence is not a fail."""
    runner: RunCmd = run or run_cmd
    if not _zt_cli_available(runner):
        return _result("zerotier", STATUS_NOT_INSTALLED,
                       "zerotier-cli не найден (ZeroTier в архиве)")
    # service state distinguishes "installed" from "installed but stopped"
    rc, state_out, _err = runner(
        ["systemctl", "is-active", "zerotier-one"])
    state = state_out.strip()
    if rc != 0 and state in ("inactive", "failed"):
        return _result("zerotier", STATUS_WARN,
                       f"установлен, но не запущен (zerotier-one: {state})")
    if state == "not-found":
        return _result("zerotier", STATUS_NOT_INSTALLED,
                       "сервис zerotier-one не найден")
    rc, out, err = runner(["sudo", "-n", "zerotier-cli", "status"])
    if rc != 0:
        return _result("zerotier", STATUS_WARN,
                       "нет прав на zerotier-cli (sudo -n недоступен)")
    if "ONLINE" not in out:
        state_word = "OFFLINE" if "OFFLINE" in out else "не ONLINE"
        return _result("zerotier", STATUS_FAIL,
                       f"zerotier-cli status: {state_word}")
    rc, nets_out, _err = runner(
        ["sudo", "-n", "zerotier-cli", "listnetworks"])
    joined = _zt_joined_networks(nets_out) if rc == 0 else []
    detail = "ONLINE"
    detail += _zt_peers_detail(runner)
    if not joined:
        detail += ", joined-сетей нет"
    else:
        detail += ", " + _zt_networks_detail(nets_out)
    if peer_ip is not None:
        try:
            ipaddress.ip_address(peer_ip)
        except ValueError:
            detail += f", zt-ping: некорректный IP '{peer_ip}'"
        else:
            detail += _zt_peer_ping(runner, peer_ip)
    return _result("zerotier", STATUS_OK, detail)


# --- AmneziaWG (slot) -----------------------------------------------------------

def _awg_binaries() -> List[str]:
    found: List[str] = []
    for name in AWG_BIN_NAMES:
        for d in AWG_BIN_DIRS:
            if (d / name).exists():
                found.append(str(d / name))
                break
    return found


def _awg_confs() -> List[Path]:
    confs: List[Path] = []
    for d in AWG_CONF_DIRS:
        if d.is_dir():
            confs.extend(sorted(d.glob("*.conf")))
    return confs


def _awg_interfaces(run: RunCmd) -> List[str]:
    """Живые AWG-интерфейсы. Источник истины — 'awg show interfaces'
    (сам AWG); имя-паттерн по ip link — fallback (gap 2026-11-11: инстанс
    vpn-awg-split@laptop создаёт iface 'laptop', паттерн awg* не матчил ->
    ложный warn в monitor при живом туннеле)."""
    rc, out, _err = run(["sudo", "-n", "awg", "show", "interfaces"])
    if rc == 0 and out.strip():
        return [line.strip() for line in out.strip().splitlines()
                if line.strip()]
    rc, out, _err = run(["ip", "-o", "link", "show"])
    if rc != 0:
        return []
    ifaces: List[str] = []
    for line in out.splitlines():
        match = re.match(r"\d+:\s+(\S+?@\S+|\S+?):", line)
        if not match:
            continue
        name = match.group(1).split("@")[0]
        if re.match(r"^(amz|amn|awg|amnezia|wg)", name) and "UP" in line:
            ifaces.append(name)
    return ifaces


DOMAIN_SPLIT_CONF = Path("/etc/dnsmasq.d/awg-split.conf")
DOMAIN_SPLIT_LIST = Path("/opt/awg-split/domains.txt")


def _domain_split_active() -> bool:
    """Доменный split (15_domain-split.sh) активен — есть dnsmasq-конфиг."""
    return DOMAIN_SPLIT_CONF.exists()


def check_awg(run: Optional[RunCmd] = None) -> Channel:
    """AmneziaWG slot: transport not implemented yet, artifacts only."""
    runner: RunCmd = run or run_cmd
    bins_found = _awg_binaries()
    confs = _awg_confs()
    if not bins_found and not confs:
        return _result("amneziawg", STATUS_NOT_INSTALLED,
                       f"артефактов нет — {AWG_SPEC_NOTE}")
    ifaces = _awg_interfaces(runner)
    if ifaces:
        parts_detail: List[str] = [f"интерфейс {', '.join(ifaces)}"]
        if _domain_split_active():
            n = 0
            try:
                raw = DOMAIN_SPLIT_LIST.read_text(
                    encoding="utf-8")
                n = sum(1 for ln in raw.splitlines()
                        if ln.strip() and not ln.strip().startswith("#"))
            except OSError:
                pass
            parts_detail.append(
                f"domain-split: {n} зон/доменов через туннель "
                "(com/org/net/io/dev/ai + youtube/telegram; GLM/RU — напрямую)")
        return _result("amneziawg", STATUS_OK, "; ".join(parts_detail))
    parts = []
    if bins_found:
        parts.append(f"бинарь: {', '.join(Path(b).name for b in bins_found)}")
    if confs:
        parts.append(f"конфигов: {len(confs)}")
    return _result("amneziawg", STATUS_WARN,
                   "артефакты найдены (" + "; ".join(parts)
                   + "), интерфейс не поднят")


def check_xraysplit(run: Optional[RunCmd] = None) -> Channel:
    """Канал XHTTP-split (SPEC-20261114): системный tun xray0.

    Нет юнита и конфигов → not_installed (нейтрально, как AWG-слот); конфиги
    есть, но юнит остановлен → warn (резерв одной командой 'vpn scheme
    xhttp-split'); юнит active → nft/rule/iface + прямая проба 204 через
    tun (без socks — трафик идёт через xray0 по mark 0x23000).
    """
    from . import xsplit as xsplit_mod  # lazy: avoids module-level cycles
    runner: RunCmd = run or run_cmd
    st = xsplit_mod.status(runner)
    profile = st["profile"]
    if not profile:
        if not st["configs"]:
            return _result("xray-split", STATUS_NOT_INSTALLED,
                           "артефактов нет (юнит/конфиги) — SPEC-20261114")
        return _result("xray-split", STATUS_WARN,
                       f"конфиги есть ({', '.join(st['configs'])}), юнит "
                       "остановлен — резерв: 'vpn scheme xhttp-split'")
    parts = ["профиль '%s'" % profile, "unit active"]
    if st["iface_up"]:
        parts.append("iface %s up" % xsplit_mod.DEV)
    if st["ip_rule"] and st["nft_table"]:
        parts.append("split активен (mark %s, table %s)"
                     % (xsplit_mod.MARK_HEX, xsplit_mod.ROUTE_TABLE))
    elif not st["ip_rule"]:
        parts.append("ip rule НЕ на месте (правила сняты?)")
    rc, out, _err = runner(
        ["curl", "--noproxy", "*", "--max-time", "10", "-s", "-o", "/dev/null",
         "-w", "%{http_code} %{time_total}",
         "https://www.youtube.com/generate_204"])
    if rc == 0 and out.strip().startswith("204"):
        st["probe_ok"] = True
        parts.append("egress через tun: %ss" % out.strip())
        if st["iface_up"] and st["ip_rule"] and st["nft_table"]:
            return _result("xray-split", STATUS_OK, "; ".join(parts))
        return _result("xray-split", STATUS_WARN, "; ".join(parts))
    return _result("xray-split", STATUS_FAIL,
                   "юнит active, но egress через tun не прошёл "
                   "(http=%s)" % (out.strip() or "none"))


# --- zapret (channel 0) ------------------------------------------------------------


def check_cdn(run: Optional[RunCmd] = None) -> Channel:
    """Канал выживания (CDN/Cloudflare): CDN-профиль (ws).

    Имя профиля не хардкодится: vds рендерит CDN-ссылки с произвольным
    именем, поэтому берётся profiles.resolve_cdn() (cdn-home → *cdn* →
    первый ws-профиль).
    up -> одна egress-проба через его socks; существует, но остановлен ->
    warn (резерв доступен одной командой); нет профиля -> not_installed.
    Отдельная строка матрицы, потому что cdn используется И как активный
    профиль (sysproxy), И как резерв при схеме AWG-split — раньше был
    невидим в monitor именно во втором случае (gap 2026-11-11).
    """
    runner: RunCmd = run or run_cmd
    name = profiles.resolve_cdn()
    if not name:
        return _result("cdn", STATUS_NOT_INSTALLED,
                       "CDN-профиля нет — импортируйте CDN-ссылку "
                       "(vds gateway links)")
    record = profiles.get(name)
    port = int(str(record["socks_port"]))
    if not runners.is_active(name, runner):
        return _result("cdn", STATUS_WARN,
                       f"профиль '{name}' есть, но остановлен — резерв: "
                       f"'vpn up {name}' (socks 127.0.0.1:{port})")
    rc, out, _err = runner(
        ["curl", "--socks5-hostname", f"127.0.0.1:{port}",
         "--max-time", "10", "-s", "-o", "/dev/null",
         "-w", "%{http_code} %{time_total}",
         "https://www.google.com/generate_204"])
    if rc == 0 and out.strip().startswith("204"):
        return _result("cdn", STATUS_OK,
                       f"профиль '{name}' через CF "
                       f"(socks 127.0.0.1:{port}, {out.strip()}s)")
    return _result("cdn", STATUS_FAIL,
                   f"профиль '{name}' запущен, но egress не прошёл "
                   f"(http={out.strip() or 'none'})")


def check_zapret(run: Optional[RunCmd] = None) -> Channel:
    """Channel 0: direct DPI-bypass via nfqws; canary without any SOCKS proxy."""
    runner: RunCmd = run or run_cmd
    from . import zapret as zapret_mod  # lazy: avoids module-level cycles
    if zapret_mod.find_binary() is None:
        return _result("zapret", STATUS_NOT_INSTALLED,
                       f"nfqws не найден — канал 0 не установлен "
                       f"({zapret_mod.UNIT_HINT})")
    state = zapret_mod.service_state(runner)
    if state == "active":
        ok, code = zapret_mod.canary(runner)
        if ok:
            return _result("zapret", STATUS_OK,
                           "direct DPI-bypass жив (youtube generate_204 "
                           f"→ {code}, без socks)")
        return _result("zapret", STATUS_FAIL,
                       f"сервис активен, но canary не прошла "
                       f"(http={code}) — стратегия не пропускает; "
                       "blockcheck заново")
    if state in ("inactive", "failed"):
        return _result("zapret", STATUS_WARN,
                       f"установлен, но не запущен (zapret: {state}); "
                       f"vpn zapret on или {zapret_mod._manual_hint('start')}")
    return _result("zapret", STATUS_WARN,
                   f"nfqws есть, юнит {zapret_mod.SERVICE}.service не "
                   f"определён (systemctl: {state})")


# --- dns-doh (channel 0.5) ----------------------------------------------------------

DOH_SERVICE = "cloudflared-proxy-dns"
DOH_PORT = "5053"
DOH_DIG_CMD = ["dig", "@127.0.0.1", "-p", DOH_PORT, "www.youtube.com", "+short"]
DOH_INSTALL_HINT = "bash scripts/13_dns-doh.sh"


def _doh_answers(run: RunCmd) -> List[str]:
    """Non-empty address lines from the local DoH dig probe."""
    rc, out, _err = run(DOH_DIG_CMD)
    if rc != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def check_dnsdoh(run: Optional[RunCmd] = None) -> Channel:
    """Channel 0.5: local cloudflared DoH resolver shields system DNS."""
    runner: RunCmd = run or run_cmd
    rc, state_out, _err = runner(["systemctl", "is-active", DOH_SERVICE])
    state = state_out.strip()
    if rc != 0 and state == "not-found":
        return _result("dns-doh", STATUS_NOT_INSTALLED,
                       f"сервис {DOH_SERVICE}.service не найден "
                       f"({DOH_INSTALL_HINT})")
    if rc != 0 or state != "active":
        return _result("dns-doh", STATUS_FAIL,
                       f"сервис {DOH_SERVICE}: {state or 'unknown'} — "
                       f"системный DNS идёт мимо DoH; "
                       f"sudo systemctl restart {DOH_SERVICE}")
    if _doh_answers(runner):
        return _result("dns-doh", STATUS_OK,
                       "DoH-резолвер жив (локальный cloudflared)")
    return _result("dns-doh", STATUS_FAIL,
                   f"сервис активен, но dig @127.0.0.1 -p {DOH_PORT} пуст — "
                   f"upstream недоступен/перехвачен; "
                   f"journalctl -u {DOH_SERVICE} -n 50")


# --- AWG tun latency ----------------------------------------------------------------

YT_HOST = "www.youtube.com"
YT_PROBE_URL = "https://www.youtube.com/generate_204"
AWG_PROBE_TIMEOUT = 8.0  # seconds per curl measurement through the tun iface
DOH_URL = f"https://dns.google/resolve?name={YT_HOST}&type=A"


def resolve_youtube_ip(run: Optional[RunCmd] = None) -> Optional[str]:
    """Resolve www.youtube.com via DoH (dns.google); None on any failure.

    The system resolver may be blocked/poisoned on a censored link, so the
    probe resolves through DNS-over-HTTPS over the tunnel instead.
    """
    runner: RunCmd = run or run_cmd
    try:
        rc, out, _err = runner(
            ["curl", "--noproxy", "*", "-s", "--max-time", "5", DOH_URL])
    except Exception:  # defensive: latency reporting must never crash the run
        return None
    if rc != 0:
        return None
    try:
        payload = json.loads(out)
    except ValueError:
        return None
    for answer in payload.get("Answer", []):
        if int(answer.get("type", 0)) == 1 and answer.get("data"):
            return str(answer["data"])
    return None


def awg_tunnel_latency(
        run: Optional[RunCmd] = None,
        samples: int = 3) -> Optional[float]:
    """Median latency (ms) of YouTube generate_204 through the AWG tun iface.

    Traffic goes through the tunnel by AllowedIPs, so plain curl (no proxy)
    with ``--resolve`` pinning the host to its real IP measures the tun path.
    Returns None when the interface is not up, DoH resolution fails, or any
    sample fails (strict, same rule as the socks probe).
    """
    runner: RunCmd = run or run_cmd
    if check_awg(runner)["status"] != STATUS_OK:
        return None
    ip = resolve_youtube_ip(runner)
    if not ip:
        return None
    cmd = [
        "curl", "--noproxy", "*",
        "--resolve", f"{YT_HOST}:443:{ip}",
        "-o", "/dev/null", "-s", "-w", "%{time_total}",
        "--max-time", str(AWG_PROBE_TIMEOUT), YT_PROBE_URL,
    ]
    values: List[float] = []
    for _ in range(samples):
        try:
            rc, out, err = runner(cmd)
        except Exception:  # defensive, same as probe.measure_latency
            return None
        value = probe.parse_time_total(out, err, rc)
        if value is None:
            return None
        values.append(value)
    if not values:
        return None
    return round(probe.median(values) * 1000.0, 1)


# --- top level -------------------------------------------------------------------

def check_all(run: Optional[RunCmd] = None) -> List[Channel]:
    return [check_vpnm(run=run), check_cdn(run=run),
            check_zerotier(run=run),
            check_awg(run=run), check_xraysplit(run=run), check_zapret(run=run),
            check_dnsdoh(run=run)]


def run_monitor(run: Optional[RunCmd] = None,
                peer_ip: Optional[str] = None) -> Dict[str, object]:
    """Run all channel checks; return channels + healthy counters."""
    channels = [
        check_vpnm(run=run),
        check_cdn(run=run),
        check_zerotier(run=run, peer_ip=peer_ip),
        check_awg(run=run),
        check_xraysplit(run=run),
        check_zapret(run=run),
        check_dnsdoh(run=run),
    ]
    return {
        "channels": channels,
        "healthy": healthy_count(channels),
        "total": len(channels),
    }


def exit_code(outcome: Dict[str, object]) -> int:
    """0 when at least one channel is ok, 1 otherwise."""
    return 0 if int(str(outcome["healthy"])) > 0 else 1
