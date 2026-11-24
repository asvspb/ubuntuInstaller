"""WP-S3: the scoring matrix that turns observations into a route choice.

Base scores (WP-S3; WP-QX-3 min-risk cutover 2026-11-25 — relay-first,
verbatim; правит ТОЛЬКО WP-QX-3, guard v1.1 rank.py не трогает):

========================  ====
relay + awg               92
relay + reality           86
direct + awg              80
direct + reality          60
hy2 (any route)           55
xhttp (any route)         55
cdn (any route)           50
========================  ====

Adjustments:

- **lock-443** (server verdict is fresh and ``lockdown.active``): a direct
  xray on TCP 443 scores 0; relay / AWG / Hy2 transports get +10 because they
  are the ones that still work;
- **TSPU TLS filter** (a direct TLS probe saw RST/hanging ClientHello):
  direct Reality scores 0; relay routes get +15;
- **recent G-04** on a UDP endpoint: −60 (a black hole seen in the last 30
  minutes is strong evidence, not a proof — hence a penalty, not 0);
- **recent G-01/G-03 for the profile**: −12 (an incident is a signal about the
  channel, not a probe result);
- **silence** (UDP, no answer at all — legacy Hy2 path): −6, because silence
  is an *unknown*, not a proof of reachability;
- **local_route** (Stealth Cascade: UDP endpoint is locally routable, **0 bytes
  sent**): −6, same honesty as ``silence`` — the routing table has a path, the
  transport is not proven;
- **tls_only** (TLS 1.3 handshake completed, no HTTP answer): −4 — the
  handshake is proven, the HTTP leg is not; it must stay below a full
  ``TLS+HTTP`` ``ok`` but above an unproven UDP route;
- **RTT**: −1 for every full 25 ms of measured round-trip time, capped at
  −6 (a slow-but-working channel is still working; the cap keeps a 400 ms RTT
  from erasing the difference between transports).

:func:`pre_score` is the same arithmetic **without a probe**: it is what the
Stealth Cascade sorts candidates by before it touches the network.  It shares
the lock-443 / G-04 / incident adjustments with :func:`score_candidate` (one
implementation, no drift).

Output: one ``RECOMMENDED`` (score ≥ :data:`RECOMMENDED_MIN`), one
``FALLBACK`` on a different route or protocol, everything else ``rejected``
with human-readable reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .models import (
    GOOD_STATUSES,
    PROTO_AWG,
    PROTO_HY2,
    PROTO_XRAY_CDN,
    PROTO_XRAY_REALITY,
    PROTO_XRAY_XHTTP,
    ROUTE_DIRECT,
    ROUTE_RELAY,
    STATUS_LOCAL_ROUTE,
    STATUS_NOT_PROBED,
    STATUS_RECENT_INCIDENT,
    STATUS_RECENT_UDP_BLACKHOLE,
    STATUS_SILENCE,
    STATUS_TLS_ONLY,
    STATUS_TSPU_TLS_FILTER,
    TLS_PROTOS,
    Candidate,
    ProbeResult,
)

#: Base score per ``(route, proto)``; the transport-only rows apply to both
#: routes and are looked up through :func:`base_score`.
#: WP-QX-3 min-risk cutover: relay-first — direct+awg demoted to OOB-резерв
#: (SPEC-20261124-quiet-exit §4); правит ТОЛЬКО WP-QX-3.
BASE_SCORES: Mapping[Tuple[str, str], int] = {
    (ROUTE_RELAY, PROTO_AWG): 92,
    (ROUTE_RELAY, PROTO_XRAY_REALITY): 86,
    (ROUTE_DIRECT, PROTO_AWG): 80,
    (ROUTE_DIRECT, PROTO_XRAY_REALITY): 60,
    (ROUTE_DIRECT, PROTO_HY2): 55,
    (ROUTE_RELAY, PROTO_HY2): 55,
    (ROUTE_DIRECT, PROTO_XRAY_XHTTP): 55,
    (ROUTE_RELAY, PROTO_XRAY_XHTTP): 55,
    (ROUTE_DIRECT, PROTO_XRAY_CDN): 50,
    (ROUTE_RELAY, PROTO_XRAY_CDN): 50,
}

LOCKDOWN_BONUS = 10
TSPU_RELAY_BONUS = 15
G04_PENALTY = 60
SILENCE_PENALTY = 6
TLS_ONLY_PENALTY = 4
LOCAL_ROUTE_PENALTY = 6
RECENT_INCIDENT_PENALTY = 12
RTT_STEP_MS = 25
RTT_PENALTY_CAP = 6
RECOMMENDED_MIN = 50

#: Protocols that speak UDP; only their endpoints can be a G-04 black hole.
UDP_PROTOS = (PROTO_AWG, PROTO_HY2)

STATUS_RECOMMENDED = "recommended"
STATUS_FALLBACK = "fallback"
STATUS_REJECTED = "rejected"


def base_score(route: str, proto: str) -> int:
    """Base score of one route/protocol pair (0 when the pair is unknown)."""
    return int(BASE_SCORES.get((route, proto), 0))


def detects_tspu_tls_filter(candidates: Sequence[Candidate],
                            results: Mapping[str, ProbeResult]) -> bool:
    """True when a **direct** TLS probe observed the TLS filter signature."""
    for candidate in candidates:
        if candidate.route != ROUTE_DIRECT or candidate.proto not in TLS_PROTOS:
            continue
        result = results.get(candidate.key)
        if result is not None and result.status == STATUS_TSPU_TLS_FILTER:
            return True
    return False


def needs_network_view(candidate: Candidate) -> bool:
    """AWG is a UDP transport: the RTT of a TCP probe is not its latency."""
    return candidate.proto != PROTO_AWG


def _lockdown_adjust(candidate: Candidate, score: int, reasons: List[str],
                     *, lockdown_443: bool) -> int:
    """lock-443: direct xray-443 → 0; relay/AWG/Hy2 get a bonus."""
    if not lockdown_443:
        return score
    if (candidate.route == ROUTE_DIRECT and candidate.proto in TLS_PROTOS
            and candidate.port == 443):
        reasons.append("lock-443: прямой xray на 443 закрыт сервером → 0")
        return 0
    if (candidate.route == ROUTE_RELAY
            or candidate.proto in (PROTO_AWG, PROTO_HY2)):
        reasons.append(f"lock-443: обходной транспорт (релей/AWG/Hy2) "
                       f"+{LOCKDOWN_BONUS}")
        return score + LOCKDOWN_BONUS
    return score


def _g04_adjust(candidate: Candidate, score: int, reasons: List[str],
                *, recent_g04: bool) -> int:
    """A fresh G-04 black hole on this UDP endpoint costs 60 points."""
    if not recent_g04 or candidate.proto not in UDP_PROTOS:
        return score
    reasons.append(f"недавний G-04 (≤30 мин) на UDP-эндпоинте −{G04_PENALTY}")
    return score - G04_PENALTY


def _incident_adjust(candidate: Candidate, score: int, reasons: List[str],
                     *, recent_incident: bool) -> int:
    """A recent G-01/G-03 for this profile demotes it without a probe."""
    if not recent_incident:
        return score
    reasons.append(f"недавний G-01/G-03 по профилю "
                   f"'{candidate.profile or '-'}' −{RECENT_INCIDENT_PENALTY}")
    return score - RECENT_INCIDENT_PENALTY


def pre_score(candidate: Candidate, *,
              lockdown_443: bool = False,
              recent_g04_endpoints: Sequence[str] = (),
              recent_incident_profiles: Sequence[str] = ()
              ) -> Tuple[int, List[str]]:
    """Local, network-free weight used to sort candidates before any probe.

    Everything here comes from disk (fresh server verdict, 30-min findings)
    plus the base matrix — no socket, no DNS.  The Stealth Cascade tries the
    highest ``pre_score`` first, so a cheap local fact (lock-443, a recent
    G-04) decides the order instead of a blind sequence of probes.
    """
    reasons: List[str] = []
    score = base_score(candidate.route, candidate.proto)
    if score == 0:
        reasons.append(f"нет базовой оценки для {candidate.route}+"
                       f"{candidate.proto}")
    score = _lockdown_adjust(candidate, score, reasons,
                             lockdown_443=lockdown_443)
    score = _g04_adjust(
        candidate, score, reasons,
        recent_g04=f"{candidate.host}:{candidate.port}"
                   in set(recent_g04_endpoints))
    score = _incident_adjust(
        candidate, score, reasons,
        recent_incident=candidate.profile in set(recent_incident_profiles))
    score = max(0, score)
    if not reasons:
        reasons.append("предварительный вес без сетевых проб")
    return score, reasons


def score_candidate(candidate: Candidate, probe: ProbeResult, *,
                    lockdown_443: bool = False,
                    tspu_tls_filter: bool = False,
                    recent_g04: bool = False,
                    recent_incident: bool = False) -> Tuple[int, List[str]]:
    """Score one candidate; returns ``(score, reasons)`` (see module doc)."""
    reasons: List[str] = []
    score = base_score(candidate.route, candidate.proto)
    if score == 0:
        reasons.append(f"нет базовой оценки для {candidate.route}+"
                       f"{candidate.proto}")

    if probe.hard_fail:
        reason = f"проба: {probe.status}"
        if probe.detail:
            reason += f" — {probe.detail}"
        reasons.append(reason)
        return 0, reasons

    score = _lockdown_adjust(candidate, score, reasons,
                             lockdown_443=lockdown_443)

    if tspu_tls_filter:
        if candidate.route == ROUTE_DIRECT and candidate.proto == PROTO_XRAY_REALITY:
            score = 0
            reasons.append("TSPU TLS-фильтр: прямой Reality → 0")
        elif candidate.route == ROUTE_RELAY:
            score += TSPU_RELAY_BONUS
            reasons.append(f"TSPU TLS-фильтр: релейный маршрут "
                           f"+{TSPU_RELAY_BONUS}")

    if probe.status == STATUS_RECENT_UDP_BLACKHOLE:
        score = _g04_adjust(candidate, score, reasons, recent_g04=True)
    if recent_incident:
        score = _incident_adjust(candidate, score, reasons,
                                 recent_incident=True)

    if probe.status == STATUS_SILENCE:
        score -= SILENCE_PENALTY
        reasons.append(f"UDP без ответа (silence): −{SILENCE_PENALTY} за "
                       "недоказанную проходимость")

    if probe.status == STATUS_LOCAL_ROUTE:
        score -= LOCAL_ROUTE_PENALTY
        reasons.append(
            f"локальная маршрутизируемость (0 байт в эфир): "
            f"−{LOCAL_ROUTE_PENALTY} за недоказанную проходимость")

    if probe.status == STATUS_TLS_ONLY:
        score -= TLS_ONLY_PENALTY
        reasons.append(f"TLS ok, HTTP-ответа нет: −{TLS_ONLY_PENALTY} "
                       "(доказан только handshake)")

    if probe.status == STATUS_RECENT_INCIDENT:
        score -= RECENT_INCIDENT_PENALTY
        reasons.append(f"recent_incident: −{RECENT_INCIDENT_PENALTY} "
                       "(история G-01/G-03 по профилю)")

    if needs_network_view(candidate) and probe.rtt_ms is not None:
        raw_penalty = int(probe.rtt_ms // RTT_STEP_MS)
        penalty = min(RTT_PENALTY_CAP, raw_penalty)
        if penalty:
            score -= penalty
            note = (f" (cap −{RTT_PENALTY_CAP})"
                    if raw_penalty > RTT_PENALTY_CAP else "")
            reasons.append(f"RTT {probe.rtt_ms:.1f} мс: −{penalty}{note}")

    score = max(0, score)
    if not reasons:
        reasons.append("базовая оценка без корректировок")
    return score, reasons


@dataclass
class RankedCandidate:
    """One row of the ranking (candidate + probe + score + verdict)."""

    candidate: Candidate
    probe: ProbeResult
    score: int = 0
    status: str = STATUS_REJECTED
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        payload = self.candidate.as_dict()
        payload.update({"probe": self.probe.as_dict(), "score": self.score,
                        "status": self.status, "reasons": list(self.reasons)})
        return payload


@dataclass
class RankReport:
    """The whole ranking: recommended / fallback / rejected."""

    rows: List[RankedCandidate] = field(default_factory=list)
    recommended: Optional[RankedCandidate] = None
    fallback: Optional[RankedCandidate] = None
    lockdown_443: bool = False
    tspu_tls_filter: bool = False

    @property
    def rejected(self) -> List[RankedCandidate]:
        return [row for row in self.rows
                if row.status == STATUS_REJECTED]

    def row_for_profile(self, profile: Optional[str]) -> Optional[RankedCandidate]:
        if not profile:
            return None
        for row in self.rows:
            if row.candidate.profile == profile:
                return row
        return None

    def as_dict(self) -> Dict[str, object]:
        return {
            "rows": [row.as_dict() for row in self.rows],
            "recommended": (self.recommended.as_dict()
                            if self.recommended is not None else None),
            "fallback": (self.fallback.as_dict()
                         if self.fallback is not None else None),
            "lockdown_443": self.lockdown_443,
            "tspu_tls_filter": self.tspu_tls_filter,
        }


def rank(candidates: Sequence[Candidate],
         results: Mapping[str, ProbeResult], *,
         lockdown_443: bool = False,
         recent_g04_endpoints: Sequence[str] = (),
         recent_incident_profiles: Sequence[str] = (),
         active_profile: Optional[str] = None) -> RankReport:
    """Build the ranked report (see the module docstring for the matrix)."""
    tspu = detects_tspu_tls_filter(candidates, results)
    g04 = set(recent_g04_endpoints)
    incidents = set(recent_incident_profiles)
    rows: List[RankedCandidate] = []
    for candidate in candidates:
        probe = results.get(candidate.key) or ProbeResult(
            status="error", detail="проба не выполнялась")
        score, reasons = score_candidate(
            candidate, probe, lockdown_443=lockdown_443,
            tspu_tls_filter=tspu,
            recent_g04=f"{candidate.host}:{candidate.port}" in g04,
            recent_incident=candidate.profile in incidents)
        rows.append(RankedCandidate(candidate=candidate, probe=probe,
                                    score=score, reasons=reasons))
    rows.sort(key=lambda row: (-row.score, row.candidate.key))

    report = RankReport(rows=rows, lockdown_443=lockdown_443,
                        tspu_tls_filter=tspu)
    recommended = next(
        (row for row in rows if row.score >= RECOMMENDED_MIN), None)
    if recommended is None:
        for row in rows:
            if row.score > 0:
                row.reasons.append(
                    f"score {row.score} < {RECOMMENDED_MIN} — рекомендации нет")
            row.status = STATUS_REJECTED
        return report
    recommended.status = STATUS_RECOMMENDED
    report.recommended = recommended

    others = [row for row in rows
              if row is not recommended and row.score > 0]
    fallback = next((row for row in others
                     if row.candidate.route != recommended.candidate.route),
                    None)
    if fallback is None:
        fallback = next((row for row in others
                         if row.candidate.proto != recommended.candidate.proto),
                        None)
    if fallback is not None:
        fallback.status = STATUS_FALLBACK
        fallback.reasons.append(
            "страховка на другом маршруте/протоколе")
    report.fallback = fallback

    for row in rows:
        if row.status in (STATUS_RECOMMENDED, STATUS_FALLBACK):
            continue
        if row.score >= RECOMMENDED_MIN:
            row.reasons.append("не выбран: ниже рекомендации по score")
        elif row.score > 0:
            row.reasons.append(
                f"score {row.score} < {RECOMMENDED_MIN} — рекомендации нет")
        row.status = STATUS_REJECTED
    if active_profile is not None:
        active = report.row_for_profile(active_profile)
        if active is not None and active.status == STATUS_RECOMMENDED:
            active.reasons.append("это и есть текущий канал")
    return report


def presort(candidates: Sequence[Candidate], *,
            lockdown_443: bool = False,
            recent_g04_endpoints: Sequence[str] = (),
            recent_incident_profiles: Sequence[str] = ()
            ) -> List[Tuple[Candidate, int, List[str]]]:
    """Order candidates by :func:`pre_score` **before any probe**.

    Deterministic: score descending, then the stable candidate key.  The
    Stealth Cascade probes this list top-down, so a local fact (lock-443, a
    recent G-04, a recent incident) decides who is tried first.
    """
    g04 = set(recent_g04_endpoints)
    incidents = set(recent_incident_profiles)
    scored: List[Tuple[Candidate, int, List[str]]] = []
    for candidate in candidates:
        score, reasons = pre_score(
            candidate, lockdown_443=lockdown_443,
            recent_g04_endpoints=g04,
            recent_incident_profiles=incidents)
        scored.append((candidate, score, reasons))
    scored.sort(key=lambda item: (-item[1], item[0].key))
    return scored


def rank_cascade(candidates: Sequence[Candidate],
                 results: Mapping[str, ProbeResult], *,
                 pre_scores: Optional[Mapping[str, int]] = None,
                 lockdown_443: bool = False,
                 recent_g04_endpoints: Sequence[str] = (),
                 recent_incident_profiles: Sequence[str] = (),
                 active_profile: Optional[str] = None) -> RankReport:
    """Rank a **cascade** run: one winner, the rest never contacted.

    ``candidates`` is in cascade order (the :func:`presort` order), ``results``
    holds only the candidates that were actually attempted.  The
    recommendation is the node the cascade stopped on — never a higher-scoring
    node it happened not to reach.  The fallback is chosen from the nodes that
    were **not probed**, by ``pre_scores`` alone, and is labelled
    ``fallback (не проверялся)``.
    """
    g04 = set(recent_g04_endpoints)
    incidents = set(recent_incident_profiles)
    scores = dict(pre_scores or {})
    probed = [candidate for candidate in candidates
              if candidate.key in results]
    unprobed = [candidate for candidate in candidates
                if candidate.key not in results]
    tspu = detects_tspu_tls_filter(probed, results)

    rows: List[RankedCandidate] = []
    for candidate in probed:
        probe = results[candidate.key]
        score, reasons = score_candidate(
            candidate, probe, lockdown_443=lockdown_443,
            tspu_tls_filter=tspu,
            recent_g04=f"{candidate.host}:{candidate.port}" in g04,
            recent_incident=candidate.profile in incidents)
        rows.append(RankedCandidate(candidate=candidate, probe=probe,
                                    score=score, reasons=reasons))
    rows.sort(key=lambda row: (-row.score, row.candidate.key))
    report = RankReport(rows=rows, lockdown_443=lockdown_443,
                        tspu_tls_filter=tspu)

    winner_key = probed[-1].key if probed else None
    winner_row = next((row for row in rows
                       if row.candidate.key == winner_key), None)
    good = (winner_row is not None
            and results[winner_key].status in GOOD_STATUSES
            and winner_row.score >= RECOMMENDED_MIN)
    if good:
        winner_row.status = STATUS_RECOMMENDED
        winner_row.reasons.append("каскад остановлен на первом хорошем узле")
        report.recommended = winner_row
    else:
        for row in rows:
            row.status = STATUS_REJECTED
            if row.score >= RECOMMENDED_MIN:
                row.reasons.append(
                    "не выбран: каскад не нашёл хорошего узла")
            elif row.score > 0:
                row.reasons.append(
                    f"score {row.score} < {RECOMMENDED_MIN} — рекомендации нет")

    # --- the nodes the cascade never touched -------------------------------------
    stop_reason = ("каскад остановился на первом хорошем узле" if good
                   else "каскад исчерпал попытки/бюджет")
    untried_rows: List[RankedCandidate] = []
    for candidate in unprobed:
        row = RankedCandidate(
            candidate=candidate,
            probe=ProbeResult(STATUS_NOT_PROBED,
                              detail="не проверялся: сетевых проб не было"),
            score=int(scores.get(candidate.key, 0)),
            reasons=[f"не проверялся: {stop_reason}"])
        untried_rows.append(row)
    untried_rows.sort(key=lambda row: (-row.score, row.candidate.key))

    if good and untried_rows:
        fallback_row = next(
            (row for row in untried_rows
             if row.candidate.route != winner_row.candidate.route
             and row.score > 0), None)
        if fallback_row is None:
            fallback_row = next((row for row in untried_rows
                                 if row.score > 0), None)
        if fallback_row is not None:
            fallback_row.status = STATUS_FALLBACK
            fallback_row.reasons = [
                "fallback (не проверялся): выбран по pre_score, "
                "сетевых проб к нему не было"]
            report.fallback = fallback_row

    for row in untried_rows:
        if row.status != STATUS_FALLBACK:
            row.status = STATUS_REJECTED
        report.rows.append(row)

    if active_profile is not None:
        active = report.row_for_profile(active_profile)
        if active is not None and active.status == STATUS_RECOMMENDED:
            active.reasons.append("это и есть текущий канал")
    return report


__all__ = [
    "BASE_SCORES",
    "G04_PENALTY",
    "LOCKDOWN_BONUS",
    "LOCAL_ROUTE_PENALTY",
    "RECENT_INCIDENT_PENALTY",
    "RECOMMENDED_MIN",
    "RTT_PENALTY_CAP",
    "RTT_STEP_MS",
    "RankReport",
    "RankedCandidate",
    "SILENCE_PENALTY",
    "TLS_ONLY_PENALTY",
    "TSPU_RELAY_BONUS",
    "UDP_PROTOS",
    "base_score",
    "detects_tspu_tls_filter",
    "pre_score",
    "presort",
    "rank",
    "rank_cascade",
    "score_candidate",
]
