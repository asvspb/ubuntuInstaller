"""Protocol health probe: measure latency per profile and pick the fastest.

For each stored profile (in store order): bring it ``up`` (unless it is
already active — then measure directly, no restart), run 3 curl latency
measurements through its local SOCKS port against
``https://www.google.com/generate_204``, compute the metric, then stop it
again (unless it was already running or it is the initially active profile
kept for the final state).

Metric: **median of the 3 samples** (more robust to a single network spike
than min). A profile fails when *any* of the 3 measurements is a timeout /
exit-code failure / ``000`` output (strict per spec: a flaky tunnel is not a
healthy tunnel). On success ``latency_ms`` is the median in milliseconds.

All curl/systemctl interaction goes through ``runners.run_cmd`` subprocess
argument lists (no shell=True); tests monkeypatch ``run_cmd`` and the runner
lifecycle functions.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from . import profiles, runners
from .links import LinkError

PROBE_URL = "https://www.google.com/generate_204"
PROBE_TIMEOUT = 8.0  # seconds per curl measurement
SAMPLES = 3
SLEEP_BETWEEN = 1.0  # seconds between consecutive profile up/down cycles

RunCmd = Callable[[List[str]], "tuple[int, str, str]"]


class ProbeResult(Dict[str, object]):
    """One row of the probe table (plain dict subclass for testability)."""


def parse_time_total(stdout: str, stderr: str, rc: int) -> Optional[float]:
    """Parse ``curl -w %{time_total}`` output; None on any failure.

    Fail conditions: non-zero exit code, empty output, ``000`` (curl's
    placeholder when no transfer happened, e.g. timeout), or a non-numeric
    value.
    """
    if rc != 0:
        return None
    text = stdout.strip()
    if not text or text == "000":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def measure_latency(
        socks_port: int,
        run: Optional[RunCmd] = None,
        timeout: float = PROBE_TIMEOUT,
        url: str = PROBE_URL) -> Optional[float]:
    """One latency measurement (seconds) through the SOCKS port; None on fail."""
    run = run or runners.run_cmd
    cmd = [
        "curl", "--socks5-hostname", f"127.0.0.1:{socks_port}",
        "-o", "/dev/null", "-s", "-w", "%{time_total}",
        "--max-time", str(timeout), url,
    ]
    try:
        rc, out, _err = run(cmd)
    except Exception:  # defensive: a broken probe must never crash the run
        return None
    return parse_time_total(out, _err, rc)


def median(values: List[float]) -> float:
    """Median of a non-empty list; for even length, the mean of the middle two."""
    if not values:
        raise ValueError("median of an empty list")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


PORT_WAIT_TIMEOUT = 3.0  # seconds to wait for the client to bind after up()


def _wait_port(socks_port: int,
               sleep_fn: Callable[[float], None] = time.sleep,
               timeout: float = PORT_WAIT_TIMEOUT) -> bool:
    """Wait until the client binds its SOCKS port; False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runners.port_in_use(socks_port):
            return True
        sleep_fn(0.2)
    return runners.port_in_use(socks_port)


def probe_profile(
        name: str,
        run: Optional[RunCmd] = None,
        samples: int = SAMPLES,
        timeout: float = PROBE_TIMEOUT,
        sleep_fn: Callable[[float], None] = time.sleep) -> ProbeResult:
    """Measure one profile: ensure it is up, take ``samples`` measurements.

    Does not stop the profile afterwards — the orchestrator owns the
    lifecycle so the initially-active profile stays untouched.
    """
    record = profiles.get(name)
    socks_port = int(str(record["socks_port"]))
    result = ProbeResult(
        name=name,
        protocol=str(record["protocol"]),
        port=socks_port,
        ok=False,
        latency_ms=None,
        error="",
        already_active=runners.is_active(name, run),
    )
    try:
        if not result["already_active"]:
            runners.up(name, run)
            _wait_port(socks_port, sleep_fn=sleep_fn)
    except (profiles.ProfileStoreError, runners.RunnerError, LinkError) as exc:
        result["error"] = str(exc)
        return result
    values: List[float] = []
    failed = False
    for _ in range(samples):
        value = measure_latency(socks_port, run=run, timeout=timeout)
        if value is None:
            failed = True  # any timeout/000 fails the whole profile (strict)
        else:
            values.append(value)
    if failed or not values:
        result["error"] = "timeout or curl failure on %s" % (
            "some samples" if values else "all samples")
        return result
    result["ok"] = True
    result["latency_ms"] = round(median(values) * 1000.0, 1)
    return result


def run_probe(
        run: Optional[RunCmd] = None,
        switch_best: bool = True,
        sleep_between: float = SLEEP_BETWEEN,
        sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, object]:
    """Probe all profiles in store order and (optionally) switch to the fastest.

    Returns a dict with ``rows`` (probe results), ``initial_active``,
    ``fastest`` (name or None) and ``switched_to`` (name or None).
    """
    rows: List[ProbeResult] = []
    initial_active = runners.get_active()
    for record in profiles.list_profiles():
        name = str(record["name"])
        row = probe_profile(name, run=run, sleep_fn=sleep_fn)
        rows.append(row)
        # stop the profile we brought up for measuring, unless it was already
        # running before the probe (the initial active profile stays up)
        if not row["already_active"] and runners.get_active() == name:
            try:
                runners.down(name, run)
            except runners.RunnerError as exc:
                row["error"] = row["error"] or f"post-measure stop failed: {exc}"
        if sleep_between > 0:
            sleep_fn(sleep_between)

    ok_rows = [r for r in rows if r["ok"]]
    fastest: Optional[str] = None
    switched_to: Optional[str] = None
    if ok_rows:
        best = min(ok_rows, key=lambda r: float(r["latency_ms"]))  # type: ignore[arg-type]
        fastest = str(best["name"])
        if switch_best:
            if runners.is_active(fastest, run):
                # already running (e.g. the initially active profile): no
                # restart needed, only fix the state.json tracking
                runners.set_active(fastest)
                switched_to = fastest
            else:
                try:
                    runners.switch(fastest, run)
                    switched_to = fastest
                except (profiles.ProfileStoreError, runners.RunnerError,
                        LinkError) as exc:
                    # failed to switch: fall back to the initial active profile
                    _restore_initial(initial_active, run)
                    rows.append(ProbeResult(
                        name="(switch failed)", protocol="", port=0, ok=False,
                        latency_ms=None, error=str(exc), already_active=False))
        else:
            _restore_initial(initial_active, run)
    else:
        # every profile failed: restore whatever was running before
        _restore_initial(initial_active, run)

    return {
        "rows": rows,
        "initial_active": initial_active,
        "fastest": fastest,
        "switched_to": switched_to,
    }


def _restore_initial(initial_active: Optional[str],
                     run: Optional[RunCmd]) -> None:
    """Bring back the initially active profile if the state drifted.

    Unit-running state and state.json tracking are checked separately: the
    initially active profile may still be running as a unit while ``active``
    in state.json was cleared by an intermediate down().
    """
    if initial_active is not None and runners.is_active(initial_active, run):
        runners.set_active(initial_active)
        return
    current = runners.get_active()
    if current is not None:
        try:
            runners.down(current, run)
        except runners.RunnerError:
            pass  # best effort; the up() below is the meaningful action
    if initial_active is not None:
        try:
            runners.up(initial_active, run)
            runners.set_active(initial_active)
        except (profiles.ProfileStoreError, runners.RunnerError, LinkError):
            pass  # best effort: the caller reports the outcome
