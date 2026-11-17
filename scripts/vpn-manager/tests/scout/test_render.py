"""Calibration 2026-11-18: the human-readable view warns about fresh threat.

The threat hint is informational only: it must appear when the server verdict
is fresh and ``threat_level`` is ``elevated``/``high``, and never otherwise.
It must not change the winner or the JSON contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.scout.cli import render_lines  # noqa: E402


def payload(threat: str, *, fresh: bool = True):
    return {
        "schema": "scout.report/1",
        "created": "2026-11-18T12:00:00Z",
        "context": {"verdict": {}, "verdict_fresh": fresh,
                    "verdict_age_s": 42 if fresh else None,
                    "threat_level": threat, "server_verdict": threat,
                    "lockdown_443": False, "findings_recent": 0,
                    "g04_endpoints": [], "notes": []},
        "threat_level": threat,
        "recommended": None,
        "fallback": None,
        "rejected": [],
        "candidates": [],
        "notify": {"attempted": False},
    }


def test_threat_hint_for_fresh_elevated() -> None:
    lines = render_lines(payload("elevated"))
    assert any(line.startswith("[info] threat=elevated")
               and "lock-443" in line and "relay/AWG" in line
               for line in lines)


def test_threat_hint_for_fresh_high() -> None:
    lines = render_lines(payload("high"))
    assert any(line.startswith("[info] threat=high") for line in lines)


def test_no_threat_hint_for_quiet() -> None:
    lines = render_lines(payload("quiet"))
    assert all("threat=elevated" not in line and "threat=high" not in line
               for line in lines)


def test_no_threat_hint_for_stale_verdict() -> None:
    lines = render_lines(payload("high", fresh=False))
    assert all("lock-443; держите" not in line for line in lines)


def test_threat_hint_does_not_change_the_winner() -> None:
    base = payload("elevated")
    row = {"route": "direct", "proto": "xray-reality", "profile": "phone",
           "host": "203.0.113.10", "port": 443, "sni": "example.com",
           "port_hop_range": None,
           "probe": {"status": "ok", "rtt_ms": 218.0, "detail": None},
           "score": 82, "status": "recommended", "reasons": []}
    base["recommended"] = row
    lines = render_lines(base)
    recommended = [line for line in lines if line.startswith("RECOMMENDED")]
    assert len(recommended) == 1
    assert "score=82" in recommended[0]
    assert "phone" in recommended[0]
