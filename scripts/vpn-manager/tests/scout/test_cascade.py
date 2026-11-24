"""Stealth Cascade tests (2026-11-18): ordering, jitter, stop-on-first-good.

The cascade is the *default* probe mode: candidates are sorted by the local,
network-free :func:`vpnm.scout.rank.pre_score` and then probed one at a time
until the first good node.  These tests pin the four properties the owner
asked for — the winner is the only one contacted, the order is driven by local
facts, the jitter only separates failures, and the JSON stays compatible.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.scout import cli as scout_cli  # noqa: E402
from vpnm.scout import probes  # noqa: E402
from vpnm.scout.context import ScoutContext  # noqa: E402
from vpnm.scout.models import (  # noqa: E402
    STATUS_LOCAL_ROUTE,
    STATUS_OK,
    STATUS_RECENT_INCIDENT,
    STATUS_RECENT_UDP_BLACKHOLE,
    STATUS_TIMEOUT,
    STATUS_TLS_ONLY,
    Candidate,
    ProbeResult,
)
from vpnm.scout.rank import (  # noqa: E402
    G04_PENALTY,
    LOCAL_ROUTE_PENALTY,
    RECENT_INCIDENT_PENALTY,
    TLS_ONLY_PENALTY,
    base_score,
    pre_score,
    presort,
    rank_cascade,
    score_candidate,
)


def cand(route: str = "direct", proto: str = "xray-reality",
         profile: str = "p", host: str = "203.0.113.10", port: int = 443
         ) -> Candidate:
    return Candidate(route=route, proto=proto, profile=profile, host=host,
                     port=port, sni="example.com")


# --- 1. stop-on-first-good ----------------------------------------------------------


def test_cascade_stops_after_the_first_good_node(
        monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        cand("direct", "awg", "laptop", "203.0.113.10", 51820),
        cand("direct", "xray-reality", "phone", "203.0.113.11", 443),
        cand("relay", "awg", "relay", "203.0.113.12", 51820),
    ]
    calls: List[str] = []

    def fake_probe(candidate, **kwargs):
        calls.append(candidate.key)
        return ProbeResult(STATUS_OK, rtt_ms=10.0)

    monkeypatch.setattr(probes, "probe_candidate", fake_probe)
    results, probed = probes.probe_cascade(candidates)
    assert calls == [candidates[0].key]  # nobody else was ever contacted
    assert probed == [candidates[0].key]
    assert list(results) == [candidates[0].key]


def test_cascade_continues_past_a_hard_fail_then_stops(
        monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        cand("direct", "awg", "laptop", "203.0.113.10", 51820),
        cand("direct", "hy2", "hy2-main", "203.0.113.11", 443),
        cand("relay", "awg", "relay", "203.0.113.12", 51820),
    ]
    calls: List[str] = []

    def fake_probe(candidate, **kwargs):
        calls.append(candidate.key)
        if candidate.profile == "hy2-main":
            return ProbeResult(STATUS_LOCAL_ROUTE)
        return ProbeResult(STATUS_TIMEOUT)

    monkeypatch.setattr(probes, "probe_candidate", fake_probe)
    _results, probed = probes.probe_cascade(candidates, sleep=lambda _d: None)
    assert calls == [candidates[0].key, candidates[1].key]
    assert probed == [candidates[0].key, candidates[1].key]


def test_cascade_budget_is_reported_honestly(
        monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [cand(profile=f"p{i}", host=f"203.0.113.{10 + i}")
                  for i in range(3)]

    def slow_probe(candidate, **kwargs):
        import time
        time.sleep(0.1)
        return ProbeResult(STATUS_TIMEOUT)

    monkeypatch.setattr(probes, "probe_candidate", slow_probe)
    results, probed = probes.probe_cascade(candidates, budget_ms=120,
                                           sleep=lambda _d: None)
    assert probed == [candidates[0].key]  # only the one whose probe started
    assert results[candidates[1].key].status == "budget"
    assert candidates[2].key not in results


# --- 2. local pre-sort ---------------------------------------------------------------


def test_lock443_lifts_relay_awg_hy2_above_direct_tls() -> None:
    direct_tls = cand("direct", "xray-reality", "phone")
    direct_awg = cand("direct", "awg", "laptop", "203.0.113.10", 51820)
    relay_awg = cand("relay", "awg", "relay", "203.0.113.11", 51820)
    direct_hy2 = cand("direct", "hy2", "hy2-main", "203.0.113.12", 443)
    ordered = presort([direct_tls, direct_awg, relay_awg, direct_hy2],
                      lockdown_443=True)
    order = [candidate.key for candidate, _score, _r in ordered]
    assert order[-1] == direct_tls.key  # a locked 443 is tried last (score 0)
    assert set(order[:3]) == {direct_awg.key, relay_awg.key, direct_hy2.key}
    scores = {candidate.key: score for candidate, score, _r in ordered}
    assert scores[direct_tls.key] == 0
    assert scores[direct_awg.key] > scores[direct_tls.key]


def test_recent_g04_drops_the_direct_awg_candidate() -> None:
    direct_tls = cand("direct", "xray-reality", "phone")
    direct_awg = cand("direct", "awg", "laptop", "203.0.113.10", 51820)
    ordered = presort([direct_awg, direct_tls],
                      recent_g04_endpoints=[f"{direct_awg.host}:"
                                            f"{direct_awg.port}"])
    scores = {candidate.key: score for candidate, score, _r in ordered}
    assert scores[direct_awg.key] == base_score("direct", "awg") - G04_PENALTY
    assert scores[direct_tls.key] == base_score("direct", "xray-reality")
    assert ordered[0][0].key == direct_tls.key


def test_recent_g01_g03_demotes_the_profile_in_pre_score() -> None:
    hy2 = cand("direct", "hy2", "hy2-main")
    score, reasons = pre_score(hy2, recent_incident_profiles=["hy2-main"])
    assert score == base_score("direct", "hy2") - RECENT_INCIDENT_PENALTY
    assert any("G-01/G-03" in reason for reason in reasons)
    # another profile's incident does not touch this candidate
    assert pre_score(hy2, recent_incident_profiles=["other"])[0] == base_score("direct", "hy2")


def test_presort_is_deterministic_with_equal_scores() -> None:
    first = cand("direct", "xray-reality", "a", "203.0.113.10", 443)
    second = cand("direct", "xray-reality", "b", "203.0.113.11", 443)
    keys = [[c.key for c, _s, _r in presort([first, second])],
            [c.key for c, _s, _r in presort([second, first])]]
    assert keys[0] == keys[1]


# --- 3. jitter -----------------------------------------------------------------------


def test_jitter_only_between_failures_and_in_the_150_400ms_range(
        monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [cand(profile=f"p{i}", host=f"203.0.113.{10 + i}")
                  for i in range(3)]

    def fake_probe(candidate, **kwargs):
        return ProbeResult(STATUS_TIMEOUT)

    monkeypatch.setattr(probes, "probe_candidate", fake_probe)
    delays: List[float] = []
    probes.probe_cascade(candidates, sleep=delays.append)
    assert len(delays) == 2  # between 3 attempts, not before the first
    assert all(0.15 <= delay <= 0.4 for delay in delays), delays


def test_no_jitter_when_the_first_node_is_good(
        monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [cand(profile=f"p{i}", host=f"203.0.113.{10 + i}")
                  for i in range(3)]
    monkeypatch.setattr(probes, "probe_candidate",
                        lambda candidate, **kwargs: ProbeResult(STATUS_OK))
    delays: List[float] = []
    probes.probe_cascade(candidates, sleep=delays.append)
    assert delays == []


# --- 4. local_route / tls_only / recent_incident in the matrix -----------------------


def test_local_route_costs_the_inconclusive_penalty() -> None:
    hy2 = cand("direct", "hy2", "hy2-main")
    score, reasons = score_candidate(hy2, ProbeResult(STATUS_LOCAL_ROUTE))
    assert score == base_score("direct", "hy2") - LOCAL_ROUTE_PENALTY
    assert any("0 байт в эфир" in reason for reason in reasons)


def test_tls_only_costs_less_than_a_full_ok() -> None:
    reality = cand("direct", "xray-reality", "phone")
    score, reasons = score_candidate(reality, ProbeResult(STATUS_TLS_ONLY))
    assert score == base_score("direct", "xray-reality") - TLS_ONLY_PENALTY
    assert any("TLS ok, HTTP-ответа нет" in reason for reason in reasons)


def test_recent_incident_is_not_a_hard_fail() -> None:
    hy2 = cand("direct", "hy2", "hy2-main")
    probe = ProbeResult(STATUS_RECENT_INCIDENT, detail="G-01 по hy2-main")
    score, reasons = score_candidate(hy2, probe)
    assert probe.hard_fail is False
    assert score == base_score("direct", "hy2") - RECENT_INCIDENT_PENALTY
    assert any("recent_incident" in reason for reason in reasons)


def test_g04_status_penalises_any_udp_protocol() -> None:
    hy2 = cand("direct", "hy2", "hy2-main")
    score, _reasons = score_candidate(
        hy2, ProbeResult(STATUS_RECENT_UDP_BLACKHOLE))
    # штраф больше базы direct hy2 → пол 0 (floor)
    assert score == max(0, base_score("direct", "hy2") - G04_PENALTY)
    assert score == 0


# --- 5. rank_cascade: winner is the cascade's stop, fallback is untried --------------


def test_rank_cascade_prefers_the_winner_over_a_higher_scoring_attempt() -> None:
    # WP-QX-3 матрица: attempt = relay reality (pre 86) с TLS_ONLY — не good;
    # cascade падает к winner = direct reality (pre 60) с LOCAL_ROUTE — good,
    # score 54 ≥ RECOMMENDED_MIN. Winner важнее более высокого pre-score.
    attempt = cand("relay", "xray-reality", "relay-reality",
                   "203.0.113.10", 443)
    winner = cand("direct", "xray-reality", "phone", "203.0.113.11", 443)
    results: Dict[str, ProbeResult] = {
        attempt.key: ProbeResult(STATUS_TLS_ONLY),
        winner.key: ProbeResult(STATUS_LOCAL_ROUTE),
    }
    report = rank_cascade(
        [attempt, winner], results,
        pre_scores={attempt.key: base_score("relay", "xray-reality"),
                    winner.key: base_score("direct", "xray-reality")})
    assert report.recommended is not None
    assert report.recommended.candidate.key == winner.key


def test_rank_cascade_fallback_is_a_not_probed_node() -> None:
    winner = cand("direct", "awg", "laptop", "203.0.113.10", 51820)
    untried = cand("relay", "awg", "relay", "203.0.113.11", 51820)
    results = {winner.key: ProbeResult(STATUS_OK, rtt_ms=5.0)}
    report = rank_cascade(
        [winner, untried], results,
        pre_scores={winner.key: base_score("direct", "awg"), untried.key: base_score("relay", "awg")})
    assert report.recommended is not None
    assert report.fallback is not None
    assert report.fallback.candidate.key == untried.key
    assert report.fallback.probe.status == "not_probed"
    assert any("не проверялся" in reason
               for reason in report.fallback.reasons)


# --- 6. CLI/JSON: mode + probed are additive ----------------------------------------


def _context(candidates) -> ScoutContext:
    return ScoutContext(candidates=list(candidates))


def test_cli_default_is_cascade_and_reports_one_probed_node(
        monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        cand("direct", "awg", "laptop", "203.0.113.10", 51820),
        cand("direct", "xray-reality", "phone", "203.0.113.11", 443),
    ]
    monkeypatch.setattr(scout_cli, "build_context",
                        lambda **kwargs: _context(candidates))

    def fake_cascade(candidates, **kwargs):
        first = candidates[0]
        return {first.key: ProbeResult(STATUS_OK, rtt_ms=10.0)}, [first.key]

    monkeypatch.setattr(scout_cli, "probe_cascade", fake_cascade)
    payload = scout_cli.build_report(active_profile=None)
    assert payload["mode"] == "cascade"
    assert payload["probed"] == ["laptop"]
    assert payload["recommended"]["profile"] == "laptop"
    assert payload["fallback"]["probe"]["status"] == "not_probed"
    assert any(line.startswith("[scout] режим: каскад")
               for line in scout_cli.render_lines(payload))


def test_probe_all_mode_contacts_every_candidate(
        monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        cand("direct", "awg", "laptop", "203.0.113.10", 51820),
        cand("direct", "xray-reality", "phone", "203.0.113.11", 443),
        cand("relay", "awg", "relay", "203.0.113.12", 51820),
    ]
    monkeypatch.setattr(scout_cli, "build_context",
                        lambda **kwargs: _context(candidates))
    seen: List[str] = []

    def fake_all(candidates_arg, **kwargs):
        seen.extend(candidate.key for candidate in candidates_arg)
        return {candidate.key: ProbeResult(STATUS_OK, rtt_ms=10.0)
                for candidate in candidates_arg}

    monkeypatch.setattr(scout_cli, "probe_all", fake_all)
    payload = scout_cli.build_report(active_profile=None, probe_all_mode=True)
    assert payload["mode"] == "all"
    assert set(payload["probed"]) == {"laptop", "phone", "relay"}
    # порядок контактов — presort (WP-QX-3: relay-first), поэтому множество
    assert set(seen) == {candidate.key for candidate in candidates}
    assert any(line.startswith("[scout] режим: полный опрос")
               for line in scout_cli.render_lines(payload))
    assert payload["recommended"] is not None


def test_exit_codes_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout_cli, "build_report",
                        lambda **kwargs: {"recommended": None})
    assert scout_cli.run_scout_cli(as_json=True) == 1
    monkeypatch.setattr(scout_cli, "build_report",
                        lambda **kwargs: {"recommended": {"profile": "p"}})
    assert scout_cli.run_scout_cli(as_json=True) == 0
