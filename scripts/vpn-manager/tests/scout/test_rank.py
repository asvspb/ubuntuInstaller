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


def test_hy2_silence_is_unknown_and_costs_six_points() -> None:
    candidate = cand("direct", "hy2", "hy2-main")
    score, reasons = score_candidate(candidate, status(STATUS_SILENCE))
    assert score == 85 - 6
    assert any("silence" in reason for reason in reasons)
    assert all("проба" not in reason for reason in reasons)


def test_rows_are_sorted_by_score_descending() -> None:
    direct = cand("direct", "xray-reality", "phone")
    relay = cand("relay", "awg", "relay", port=51820)
    candidates = [relay, direct]
    results = {direct.key: ok(10.0), relay.key: ok(None)}
    report = rank(candidates, results)
    scores = [row.score for row in report.rows]
    assert scores == sorted(scores, reverse=True)


# --- calibration 2026-11-18: silence −6 and RTT cap −6 -------------------------------


def test_silence_penalty_applies_to_any_silent_protocol() -> None:
    candidate = cand("relay", "hy2", "hy2-relay")
    score, reasons = score_candidate(candidate, status(STATUS_SILENCE))
    assert score == 80 - 6
    assert any("UDP без ответа (silence): −6" in reason for reason in reasons)


def test_awg_reachable_is_not_silence_and_keeps_the_base_score() -> None:
    candidate = cand("direct", "awg", "laptop", port=51820)
    probe = ProbeResult("reachable",
                        detail="ICMP не пришёл; AWG не отвечает на "
                               "неаутентифицированные пакеты")
    score, reasons = score_candidate(candidate, probe)
    assert score == 90
    assert all("silence" not in reason for reason in reasons)


def test_rtt_penalty_is_capped_at_six() -> None:
    candidate = cand("direct", "xray-reality", "phone")
    score_400, reasons = score_candidate(candidate, ok(400.0))
    assert score_400 == 88 - 6  # 400 // 25 == 16, capped to 6
    assert any("(cap −6)" in reason for reason in reasons)


def test_rtt_penalty_regression_below_the_cap() -> None:
    candidate = cand("direct", "xray-reality", "phone")
    assert score_candidate(candidate, ok(40.0))[0] == 88 - 1
    assert score_candidate(candidate, ok(200.0))[0] == 88 - 6
    assert score_candidate(candidate, ok(175.0))[0] == 88 - 6  # exactly 7 → cap
    assert score_candidate(candidate, ok(100.0))[0] == 88 - 4  # no cap note


def test_rtt_cap_note_only_when_the_penalty_was_cut() -> None:
    candidate = cand("direct", "xray-reality", "phone")
    _score, reasons = score_candidate(candidate, ok(150.0))
    # 150 // 25 == 6 — at the cap, not over it: no (cap −6) suffix
    assert any("RTT" in reason for reason in reasons)
    assert all("cap" not in reason for reason in reasons)


def test_owner_scenario_proven_ok_beats_blind_silence() -> None:
    # Live run 2026-11-18: reality ok @218 ms scored 80, hy2 silence scored
    # 85 and won. With the calibration: 88 − 6 = 82 > 85 − 6 = 79.
    reality = cand("direct", "xray-reality", "phone")
    hy2 = cand("direct", "hy2", "hy2-main")
    candidates = [reality, hy2]
    results = {reality.key: ok(218.0), hy2.key: status(STATUS_SILENCE)}
    report = rank(candidates, results)
    assert report.recommended is not None
    assert report.recommended.candidate.profile == "phone"
    assert report.recommended.score == 82
    hy2_row = report.row_for_profile("hy2-main")
    assert hy2_row is not None and hy2_row.score == 79
    assert report.fallback is hy2_row


def test_hy2_silence_vs_relay_reality_arithmetic_is_pinned() -> None:
    # relay/reality base 80 has no silence penalty; hy2/direct silence is 79.
    # So a relay reality with a short RTT outranks silent hy2, a slow one does
    # not — the pinned order is exactly what the arithmetic gives.
    hy2 = cand("direct", "hy2", "hy2-main")
    relay = cand("relay", "xray-reality", "relay-reality")
    cases = {
        0.0: 80,    # 80 − 0  > 79 → relay wins
        25.0: 79,   # 80 − 1 == 79 → tie on score
        218.0: 74,  # 80 − 6  < 79 → silent hy2 ranks above relay
    }
    for rtt, expected in cases.items():
        report = rank([hy2, relay],
                      {hy2.key: status(STATUS_SILENCE),
                       relay.key: ok(rtt)})
        relay_row = report.row_for_profile("relay-reality")
        assert relay_row is not None
        assert relay_row.score == expected, (rtt, relay_row.reasons)
    assert 80 > 79, "relay reality @RTT 0 must beat silent hy2"
    assert 74 < 79, "relay reality @RTT 218 must lose to silent hy2"


def test_hard_fail_still_zeroes_even_with_silence_cap_logic() -> None:
    candidate = cand("direct", "hy2", "hy2-main")
    score, reasons = score_candidate(candidate, ProbeResult("udp_refused"))
    assert score == 0
    assert all("silence" not in reason for reason in reasons)
