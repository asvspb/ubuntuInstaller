"""WP-S3: the scoring matrix that turns observations into a route choice.

Base scores (WP-S3, verbatim):

========================  ====
direct + awg               90
direct + reality           88
direct + hy2               85
relay + awg                82
relay + reality / hy2      80
xhttp (any route)          75
cdn (any route)            65
========================  ====

Adjustments:

- **lock-443** (server verdict is fresh and ``lockdown.active``): a direct
  xray on TCP 443 scores 0; relay / AWG / Hy2 transports get +10 because they
  are the ones that still work;
- **TSPU TLS filter** (a direct TLS probe saw RST/hanging ClientHello):
  direct Reality scores 0; relay routes get +15;
- **recent G-04** on a direct AWG endpoint: −60 (a black hole seen in the
  last 30 minutes is strong evidence, not a proof — hence a penalty, not 0);
- **silence** (UDP, no answer at all — Hy2): −6, because silence is an
  *unknown*, not a proof of reachability; a transport that answered both ways
  must outrank one that never answered;
- **RTT**: −1 for every full 25 ms of measured round-trip time, capped at
  −6 (a slow-but-working channel is still working; the cap keeps a 400 ms RTT
  from erasing the difference between transports).

Output: one ``RECOMMENDED`` (score ≥ :data:`RECOMMENDED_MIN`), one
``FALLBACK`` on a different route or protocol, everything else ``rejected``
with human-readable reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .models import (
    PROTO_AWG,
    PROTO_HY2,
    PROTO_XRAY_CDN,
    PROTO_XRAY_REALITY,
    PROTO_XRAY_XHTTP,
    ROUTE_DIRECT,
    ROUTE_RELAY,
    TLS_PROTOS,
    Candidate,
    ProbeResult,
    STATUS_RECENT_UDP_BLACKHOLE,
    STATUS_SILENCE,
    STATUS_TSPU_TLS_FILTER,
)

#: Base score per ``(route, proto)``; the transport-only rows apply to both
#: routes and are looked up through :func:`base_score`.
BASE_SCORES: Mapping[Tuple[str, str], int] = {
    (ROUTE_DIRECT, PROTO_AWG): 90,
    (ROUTE_DIRECT, PROTO_XRAY_REALITY): 88,
    (ROUTE_DIRECT, PROTO_HY2): 85,
    (ROUTE_RELAY, PROTO_AWG): 82,
    (ROUTE_RELAY, PROTO_XRAY_REALITY): 80,
    (ROUTE_RELAY, PROTO_HY2): 80,
    (ROUTE_DIRECT, PROTO_XRAY_XHTTP): 75,
    (ROUTE_RELAY, PROTO_XRAY_XHTTP): 75,
    (ROUTE_DIRECT, PROTO_XRAY_CDN): 65,
    (ROUTE_RELAY, PROTO_XRAY_CDN): 65,
}

LOCKDOWN_BONUS = 10
TSPU_RELAY_BONUS = 15
G04_PENALTY = 60
SILENCE_PENALTY = 6
RTT_STEP_MS = 25
RTT_PENALTY_CAP = 6
RECOMMENDED_MIN = 50

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


def score_candidate(candidate: Candidate, probe: ProbeResult, *,
                    lockdown_443: bool = False,
                    tspu_tls_filter: bool = False,
                    recent_g04: bool = False) -> Tuple[int, List[str]]:
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

    if lockdown_443:
        if (candidate.route == ROUTE_DIRECT and candidate.proto in TLS_PROTOS
                and candidate.port == 443):
            score = 0
            reasons.append("lock-443: прямой xray на 443 закрыт сервером → 0")
        elif candidate.route == ROUTE_RELAY or candidate.proto in (
                PROTO_AWG, PROTO_HY2):
            score += LOCKDOWN_BONUS
            reasons.append(f"lock-443: обходной транспорт (релей/AWG/Hy2) "
                           f"+{LOCKDOWN_BONUS}")

    if tspu_tls_filter:
        if candidate.route == ROUTE_DIRECT and candidate.proto == PROTO_XRAY_REALITY:
            score = 0
            reasons.append("TSPU TLS-фильтр: прямой Reality → 0")
        elif candidate.route == ROUTE_RELAY:
            score += TSPU_RELAY_BONUS
            reasons.append(f"TSPU TLS-фильтр: релейный маршрут "
                           f"+{TSPU_RELAY_BONUS}")

    if probe.status == STATUS_RECENT_UDP_BLACKHOLE and candidate.route == \
            ROUTE_DIRECT and candidate.proto == PROTO_AWG:
        score -= G04_PENALTY
        reasons.append(f"недавний G-04 (≤30 мин) на direct AWG −{G04_PENALTY}")

    if probe.status == STATUS_SILENCE:
        score -= SILENCE_PENALTY
        reasons.append(f"UDP без ответа (silence): −{SILENCE_PENALTY} за "
                       "недоказанную проходимость")

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
         active_profile: Optional[str] = None) -> RankReport:
    """Build the ranked report (see the module docstring for the matrix)."""
    tspu = detects_tspu_tls_filter(candidates, results)
    g04 = set(recent_g04_endpoints)
    rows: List[RankedCandidate] = []
    for candidate in candidates:
        probe = results.get(candidate.key) or ProbeResult(
            status="error", detail="проба не выполнялась")
        score, reasons = score_candidate(
            candidate, probe, lockdown_443=lockdown_443,
            tspu_tls_filter=tspu,
            recent_g04=f"{candidate.host}:{candidate.port}" in g04)
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


__all__ = [
    "BASE_SCORES",
    "G04_PENALTY",
    "LOCKDOWN_BONUS",
    "RECOMMENDED_MIN",
    "RTT_PENALTY_CAP",
    "RTT_STEP_MS",
    "RankReport",
    "RankedCandidate",
    "SILENCE_PENALTY",
    "TSPU_RELAY_BONUS",
    "base_score",
    "detects_tspu_tls_filter",
    "rank",
    "score_candidate",
]
