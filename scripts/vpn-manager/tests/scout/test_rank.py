"""WP-S3 tests: the scoring matrix (lock-443, TSPU TLS filter, recent G-04).

The three required slices — ``-k "lockdown or tspu or g04"`` — pin the
adjustments verbatim; the rest pins the threshold, the fallback choice and
the RTT penalty.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.scout.models import (  # noqa: E402
    STATUS_RECENT_UDP_BLACKHOLE,
    STATUS_SILENCE,
    STATUS_TSPU_TLS_FILTER,
    Candidate,
    ProbeResult,
)
from vpnm.scout.rank import (  # noqa: E402
    BASE_SCORES,
    RECOMMENDED_MIN,
    base_score,
    rank,
    score_candidate,
)


def cand(route: str = "direct", proto: str = "xray-reality",
         profile: str = "p", host: str = "203.0.113.10", port: int = 443):
    return Candidate(route=route, proto=proto, profile=profile, host=host,
                     port=port, sni="example.com")


def ok(rtt: float = 10.0) -> ProbeResult:
    return ProbeResult("ok", rtt_ms=rtt)


def status(probe_status: str) -> ProbeResult:
    return ProbeResult(probe_status)


def test_base_scores_match_the_matrix() -> None:
    assert base_score("direct", "awg") == 90
    assert base_score("direct", "xray-reality") == 88
    assert base_score("direct", "hy2") == 85
    assert base_score("relay", "awg") == 82
    assert base_score("relay", "xray-reality") == 80
    assert base_score("relay", "hy2") == 80
    assert base_score("direct", "xray-xhttp") == 75
    assert base_score("relay", "xray-xhttp") == 75
    assert base_score("direct", "xray-cdn") == 65
    assert set(BASE_SCORES.values()) == {90, 88, 85, 82, 80, 75, 65}


# --- lockdown -----------------------------------------------------------------------


def test_lockdown_zeroes_direct_xray_443_and_promotes_relay() -> None:
    direct = cand("direct", "xray-reality", "phone")
    relay = cand("relay", "xray-reality", "relay-reality")
    candidates = [direct, relay]
    results = {direct.key: ok(10.0), relay.key: ok(10.0)}
    report = rank(candidates, results, lockdown_443=True)
    direct_score, direct_reasons = score_candidate(
        direct, results[direct.key], lockdown_443=True)
    assert direct_score == 0
    assert any("lock-443" in reason for reason in direct_reasons)
    assert report.recommended is not None
    assert report.recommended.candidate.route == "relay"
    # 80 base + 10 lockdown bonus − 0 RTT penalty
    assert report.recommended.score == 90


def test_lockdown_bonus_applies_to_awg_and_hy2() -> None:
    awg = cand("direct", "awg", "laptop", port=51820)
    hy2 = cand("direct", "hy2", "hy2-main")
    for candidate in (awg, hy2):
        score, reasons = score_candidate(candidate, ok(0.0),
                                         lockdown_443=True)
        assert score == base_score(candidate.route, candidate.proto) + 10, reasons


# --- TSPU TLS filter ----------------------------------------------------------------


def test_tspu_tls_filter_zeroes_direct_reality_and_lifts_relay() -> None:
    direct = cand("direct", "xray-reality", "phone")
    relay = cand("relay", "xray-reality", "relay-reality")
    candidates = [direct, relay]
    results = {direct.key: status(STATUS_TSPU_TLS_FILTER),
               relay.key: ok(10.0)}
    report = rank(candidates, results)
    assert report.tspu_tls_filter is True
    direct_row = report.row_for_profile("phone")
    assert direct_row is not None and direct_row.score == 0
    assert any("TSPU" in reason for reason in direct_row.reasons)
    relay_row = report.row_for_profile("relay-reality")
    assert relay_row is not None
    # 80 base + 15 TSPU relay bonus − 0 RTT penalty
    assert relay_row.score == 95
    assert report.recommended is relay_row


# --- recent G-04 --------------------------------------------------------------------


def test_recent_g04_penalizes_direct_awg_by_sixty() -> None:
    direct = cand("direct", "awg", "laptop", port=51820)
    relay = cand("relay", "awg", "relay", port=51820)
    candidates = [direct, relay]
    results = {direct.key: status(STATUS_RECENT_UDP_BLACKHOLE),
               relay.key: ok(None)}
    report = rank(candidates, results,
                  recent_g04_endpoints=[f"{direct.host}:{direct.port}"])
    direct_row = report.row_for_profile("laptop")
    assert direct_row is not None
    assert direct_row.score == 30  # 90 − 60
    assert any("G-04" in reason for reason in direct_row.reasons)
    assert report.recommended is not None
    assert report.recommended.candidate.route == "relay"


def test_g04_for_another_endpoint_does_not_penalize() -> None:
    direct = cand("direct", "awg", "laptop", port=51820)
    score, _reasons = score_candidate(direct, ok(None), recent_g04=False)
    assert score == 90


# --- thresholds, fallback, RTT --------------------------------------------------------


def test_rtt_costs_one_point_per_full_25ms() -> None:
    candidate = cand("direct", "xray-reality", "phone")
    score_100, reasons = score_candidate(candidate, ok(100.0))
    assert score_100 == 88 - 4
    assert any("RTT" in reason for reason in reasons)
    score_124, _ = score_candidate(candidate, ok(124.9))
    assert score_124 == 88 - 4  # 124.9 // 25 == 4


def test_awg_has_no_rtt_penalty() -> None:
    candidate = cand("direct", "awg", "laptop", port=51820)
    score, _ = score_candidate(candidate, ok(200.0))
    assert score == 90


def test_recommended_threshold_and_fallback_on_another_route() -> None:
    direct = cand("direct", "xray-reality", "phone")
    hy2 = cand("direct", "hy2", "hy2-main")
    relay = cand("relay", "xray-reality", "relay-reality")
    candidates = [direct, hy2, relay]
    results = {direct.key: ok(20.0), hy2.key: ok(20.0),
               relay.key: ok(20.0)}
    report = rank(candidates, results)
    assert report.recommended is not None
    assert report.recommended.candidate.profile == "phone"
    assert report.recommended.score >= RECOMMENDED_MIN
    assert report.fallback is not None
    # a different *route* wins over a different protocol on the same route
    assert report.fallback.candidate.route == "relay"
    assert report.fallback.status == "fallback"


def test_fallback_falls_back_to_another_protocol_when_no_other_route() -> None:
    direct = cand("direct", "xray-reality", "phone")
    hy2 = cand("direct", "hy2", "hy2-main")
    candidates = [direct, hy2]
    results = {direct.key: ok(10.0), hy2.key: ok(10.0)}
    report = rank(candidates, results)
    assert report.recommended is not None
    assert report.fallback is not None
    assert report.fallback.candidate.proto == "hy2"


def test_no_recommendation_when_everything_is_hard_failed() -> None:
    direct = cand("direct", "xray-reality", "phone")
    hy2 = cand("direct", "hy2", "hy2-main")
    candidates = [direct, hy2]
    results = {direct.key: status(STATUS_TSPU_TLS_FILTER),
               hy2.key: ProbeResult("refused")}
    report = rank(candidates, results)
    assert report.recommended is None
    assert report.fallback is None
    assert len(report.rejected) == 2
    assert all(row.reasons for row in report.rejected)


def test_hy2_silence_is_unknown_and_keeps_the_base_score() -> None:
    candidate = cand("direct", "hy2", "hy2-main")
    score, reasons = score_candidate(candidate, status(STATUS_SILENCE))
    assert score == 85
    assert all("проба" not in reason for reason in reasons)


def test_rows_are_sorted_by_score_descending() -> None:
    direct = cand("direct", "xray-reality", "phone")
    relay = cand("relay", "awg", "relay", port=51820)
    candidates = [relay, direct]
    results = {direct.key: ok(10.0), relay.key: ok(None)}
    report = rank(candidates, results)
    scores = [row.score for row in report.rows]
    assert scores == sorted(scores, reverse=True)
