"""Doctor: environment diagnosis.

Checks (each yields ok / warn / fail):
(a) binaries present in BIN_DIR + their ``version`` output;
(b) SOCKS ports of the active profile (and all profiles) — free, or taken by
    OUR unit process;
(c) conflict with v2rayA: port 1080 held by a foreign process -> warn;
(d) ssh connectivity with the saved host (when set);
(e) DNS through the active profile's tunnel (curl --socks5-hostname resolve);
(f) zapret (channel 0): absent → warn, active + direct canary 204 → ok,
    active + canary fail → fail, installed but stopped → warn.

The verdict is fail when any check failed, warn when only warnings remain.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from . import profiles, runners
from .runners import RunCmd, run_cmd

BIN_DIR = runners.BIN_DIR
CURL_TIMEOUT = runners.CURL_TIMEOUT
V2RAYA_PORT = 1080

OkWarn = str  # "ok" | "warn" | "fail"
Check = Tuple[OkWarn, str, str]  # (status, subject, detail)



CONFLICT_PROCS = {"xray", "hysteria"}  # our binaries, matched against ps


# --- helpers -------------------------------------------------------------------

def _ps_pid_of_port(port: int, run: RunCmd) -> Optional[Tuple[int, str]]:
    """Return (pid, comm) of a process holding the TCP port, via ss + ps."""
    rc, out, _err = run(["ss", "-tlnpH", f"sport = :{port}"])
    if rc != 0:
        return None
    match = re.search(r"pid=(\d+)", out)
    if not match:
        return None
    pid = int(match.group(1))
    proc_rc, comm, _ = run(["ps", "-p", str(pid), "-o", "comm="])
    return pid, comm.strip() if proc_rc == 0 else ""


def _pid_owns_unit(pid: int, profile: str, run: RunCmd) -> bool:
    """True when the pid's process tree is our systemd user unit."""
    rc, out, _err = run(
        ["systemctl", "--user", "status", runners.unit_name(profile)])
    return rc == 0 and f"Main PID: {pid}" in out


def _port_held(port: int, run: RunCmd) -> bool:
    """True when something listens on the TCP port (via ss)."""
    rc, out, _err = run(["ss", "-tlnH", f"sport = :{port}"])
    return rc == 0 and "LISTEN" in out


def _free_or_ours(port: int, profile: Optional[str],
                  run: RunCmd) -> Tuple[OkWarn, str]:
    """Classify a SOCKS port: free / held by us / held by someone else."""
    if not _port_held(port, run):
        return "ok", f"port {port} is free"
    holder = _ps_pid_of_port(port, run)
    if holder is not None and profile and _pid_owns_unit(holder[0], profile, run):
        return "ok", f"port {port} is held by our unit '{profile}'"
    who = f"pid {holder[0]} ({holder[1] or 'unknown'})" if holder else "unknown process"
    return "warn", f"port {port} is held by a foreign process: {who}"


# --- individual checks ----------------------------------------------------------

def check_bins(bin_dir: Path = BIN_DIR,
               run: Optional[RunCmd] = None) -> List[Check]:
    runner: RunCmd = run or run_cmd
    out: List[Check] = []
    for bin_name in ("xray", "hysteria"):
        path = bin_dir / bin_name
        if not path.exists():
            out.append(("warn", "bins",
                        f"{bin_name} is missing at {path} — run 'vpn install-bins'"))
            continue
        rc, out_text, err_text = runner([str(path), "version"])
        text = (out_text + err_text).strip().splitlines()
        version = text[0] if text else "(no version output)"
        if rc != 0:
            out.append(("fail", "bins", f"{bin_name} is present but does not "
                                        f"run (exit {rc}): {version}"))
        else:
            out.append(("ok", "bins", f"{bin_name}: {version}"))
    return out


def check_ports(bin_dir: Path = BIN_DIR,
                run: Optional[RunCmd] = None) -> List[Check]:
    runner: RunCmd = run or run_cmd  # type: ignore[assignment]
    rows = profiles.list_profiles()
    out: List[Check] = []
    if not rows:
        return [("warn", "ports", "no profiles configured; nothing to check")]
    active = runners.get_active()
    for row in rows:
        name = str(row["name"])
        port = int(row["socks_port"])
        status, detail = _free_or_ours(port, name if name == active else None,
                                       runner)
        if status == "ok":
            out.append(("ok", "ports", f"{name}: {detail}"))
        else:
            is_active_marker = " (ACTIVE)" if name == active else ""
            if name == active:
                out.append(("fail", "ports",
                            f"{name}{is_active_marker}: {detail}"))
            else:
                out.append(("warn", "ports",
                            f"{name}{is_active_marker}: {detail}"))
    return out


def check_v2raya(run: Optional[RunCmd] = None) -> Check:
    runner: RunCmd = run or run_cmd
    if not _port_held(V2RAYA_PORT, runner):
        return ("ok", "v2rayA",
                f"port {V2RAYA_PORT} is free — no v2rayA conflict")
    holder = _ps_pid_of_port(V2RAYA_PORT, runner)
    if holder and holder[1] in CONFLICT_PROCS:
        return ("ok", "v2rayA",
                f"port {V2RAYA_PORT} is held by our own client ({holder[1]})")
    who = (f"pid {holder[0]} ({holder[1] or 'unknown'})" if holder
           else "unknown process")
    return ("warn", "v2rayA",
            f"port {V2RAYA_PORT} is taken by a foreign process ({who}) — "
            "likely v2rayA; it will conflict when profiles use 1080")


def check_ssh(host: Optional[str],
              run: Optional[RunCmd] = None) -> Optional[Check]:
    runner: RunCmd = run or run_cmd  # type: ignore[assignment]
    if not host:
        return None
    rc, _out, err = runner(["ssh", "-o", "BatchMode=yes", "-o",
                            "ConnectTimeout=10", host, "true"])
    if rc == 0:
        return ("ok", "ssh", f"connection to {host} works")
    detail = (err or "").strip().splitlines()
    msg = detail[-1] if detail else f"exit {rc}"
    return ("warn", "ssh", f"cannot connect to {host}: {msg}")


def check_dns(socks_port: Optional[int],
              run: Optional[RunCmd] = None) -> Optional[Check]:
    runner: RunCmd = run or run_cmd  # type: ignore[assignment]
    if socks_port is None:
        return None
    cmd = ["curl", "--socks5-hostname", f"127.0.0.1:{socks_port}",
           "--max-time", str(CURL_TIMEOUT), "-s", "-o", "/dev/null",
           "-w", "%{http_code}", "https://www.google.com/generate_204"]
    rc, out, _err = runner(cmd)
    if rc == 0 and out.strip() == "204":
        return ("ok", "dns",
                f"resolve + request through 127.0.0.1:{socks_port} works")
    return ("fail", "dns",
            f"request through 127.0.0.1:{socks_port} failed (exit {rc}, "
            f"http={out.strip() or 'none'}) — tunnel is down?")


# --- zapret (channel 0) -----------------------------------------------------------

def check_zapret(run: Optional[RunCmd] = None) -> Check:
    """Channel 0 health: not installed → warn (neutral), running+canary → ok/fail."""
    from . import zapret as zapret_mod
    if zapret_mod.find_binary() is None:
        return ("warn", "zapret",
                f"канал 0 (DPI-desync) не установлен — {zapret_mod.UNIT_HINT}")
    state = zapret_mod.service_state(run or run_cmd)
    if state == "active":
        ok, code = zapret_mod.canary(run or run_cmd)
        if ok:
            return ("ok", "zapret",
                    f"direct canary ok (youtube generate_204 → {code})")
        return ("fail", "zapret",
                f"сервис активен, но direct canary не прошла (http={code}) "
                "— стратегия не пропускает, запустите blockcheck заново")
    if state in ("inactive", "failed"):
        return ("warn", "zapret",
                f"установлен, но не запущен (zapret: {state}) — "
                f"{zapret_mod._manual_hint('start')}")
    return ("warn", "zapret", f"юнит zapret.service не определён ({state})")


# --- top level -------------------------------------------------------------------

def run_doctor(host: Optional[str] = None,
               run: Optional[RunCmd] = None) -> Tuple[List[Check], str]:
    """Run all checks; return (checks, verdict) with verdict ok|warn|fail."""
    checks: List[Check] = []
    checks.extend(check_bins(run=run))
    checks.extend(check_ports(run=run))
    checks.append(check_v2raya(run=run))
    checks.append(check_zapret(run=run))

    active = runners.get_active()
    active_port: Optional[int] = None
    if active:
        try:
            record = profiles.get(active)
            active_port = int(record["socks_port"])
        except profiles.ProfileStoreError:
            checks.append(("fail", "active",
                           f"active profile '{active}' record is broken"))
    else:
        checks.append(("warn", "active", "no active profile — DNS check skipped"))

    ssh_check = check_ssh(host, run=run)
    if ssh_check:
        checks.append(ssh_check)
    dns_check = check_dns(active_port, run=run)
    if dns_check:
        checks.append(dns_check)

    statuses = {c[0] for c in checks}
    if "fail" in statuses:
        verdict = "fail"
    elif "warn" in statuses:
        verdict = "warn"
    else:
        verdict = "ok"
    return checks, verdict
