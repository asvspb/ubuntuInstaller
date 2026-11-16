"""Incident correlation, root cause and suppression accounting (WP-G4).

SPEC §9.2: findings within **180 s** on one unit (or channel) form an
incident; the root cause is chosen by a fixed priority, the incident keeps the
finding ids, the server block and the first/last wall timestamps, and closes
after **10 min** without repeats (``resolved_at``) while staying in history.

The module is intentionally pure: it reads no clock, no files and no network.
The caller passes the monotonic time of the finding (the same domain the rule
engine uses) and the wall timestamp used to close an incident, so a replay is
reproducible (AC-9).

Suppression reminders that live here (the rest is enforced by ``rules.py``
right where the evidence is, §8.4):

- ``G-12 guard_selfdegraded`` never raises the incident confidence;
- a fresh server verdict raises the confidence only for causes the server can
  actually see (``censorship_suspect``/``endpoint_unreachable``/``tunnel_down``)
  and a stale verdict is ignored (D-06, AC-10);
- ``lockdown.active`` marks the incident ``expected_lockdown`` and lowers it to
  ``info`` (server protection is not censorship).

Root cause follows SPEC §9.2 verbatim (DECISIONS D-08):
``local_net > local_fault > config > dns > tunnel_down > censorship_suspect >
endpoint_unreachable > degraded > observability``.  A crashed unit (G-10,
``local_fault``) or broken credentials (G-05, ``config``) legitimately produces
DNS errors (G-06) as a **secondary symptom**, so DNS ranks below them; the
earlier WP-G4 ordering was a temporary matrix and is superseded.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Set

from . import events, schema
from .rules import ServerContext
from .window import MONO_US_PER_S

INCIDENT_WINDOW_S = 180
INCIDENT_RESOLVE_S = 600
MAX_FINDINGS_PER_INCIDENT = 64
MAX_HISTORY = 64

#: Root-cause priority of SPEC §9.2 (DECISIONS D-08), highest first.
#: Categories only; a finding's rule maps to one of them.  Local causes beat
#: symptoms: ``local_fault``/``config`` rank above ``dns`` because a broken
#: unit or credentials *cause* DNS errors, not the other way round.
ROOT_CAUSE_PRIORITY: Sequence[str] = (
    "local_net",          # G-07
    "local_fault",        # G-10
    "config",             # G-05/G-08
    "dns",                # G-06
    "tunnel_down",        # G-01
    "censorship_suspect",  # G-02/G-03/G-04
    "endpoint_unreachable",  # G-09
    "degraded",           # G-11
    "observability",      # G-12
)

CONFIDENCE_RANK: Mapping[str, int] = {"low": 0, "medium": 1, "high": 2}
SEVERITY_RANK: Mapping[str, int] = {
    "info": 0, "low": 1, "medium": 2, "high": 3}

#: Causes a fresh server verdict can corroborate; a local/DNS fault cannot be
#: re-classified by the server (§9.3).
SERVER_VISIBLE_CAUSES: frozenset = frozenset(
    ("censorship_suspect", "endpoint_unreachable", "tunnel_down"))


def _rank(table: Mapping[str, int], value: Any) -> int:
    return table.get(str(value), -1)


def _max_by(table: Mapping[str, int], values: Sequence[Any],
            default: str) -> str:
    best = default
    best_rank = -1
    for value in values:
        rank = _rank(table, value)
        if rank > best_rank:
            best, best_rank = str(value), rank
    return best


def _root_cause(categories: Sequence[str]) -> str:
    for category in ROOT_CAUSE_PRIORITY:
        if category in categories:
            return category
    return "observability"


def incident_id(unit_key: str, window_first: str) -> str:
    """``i-<6 hex>`` from ``unit|window_start`` (schema pattern)."""
    digest = hashlib.sha256(
        f"{unit_key}|{window_first}".encode("utf-8")).hexdigest()
    return "i-" + digest[:6]


def _window(finding: Mapping[str, Any]) -> tuple:
    window = finding.get("window")
    if not isinstance(window, Mapping):
        return "", ""
    first = window.get("first")
    last = window.get("last")
    return (first if isinstance(first, str) else "",
            last if isinstance(last, str) else "")


@dataclass
class _OpenIncident:
    key: str
    id: str
    unit: Optional[str]
    profile: Optional[str]
    first: str
    last: str
    last_us: int
    findings: List[str]
    rules: Set[str]
    categories: Set[str]
    confidences: List[str]
    severities: List[str]
    created_us: int

    def as_dict(self, server: Optional[ServerContext] = None,
                resolved_at: Optional[str] = None) -> Dict[str, Any]:
        root = _root_cause(sorted(self.categories))
        confidence = _max_by(CONFIDENCE_RANK, self.confidences, "low")
        severity = _max_by(SEVERITY_RANK, self.severities, "low")
        expected_lockdown = False
        cross_vantage = False
        if server is not None and server.fresh:
            if (server.threat_level == "high"
                    and root in SERVER_VISIBLE_CAUSES):
                confidence = "high"  # cross-vantage second signal (§9.3)
                cross_vantage = True
            if server.lockdown and root in SERVER_VISIBLE_CAUSES:
                expected_lockdown = True
                severity = "info"
        if self.rules == {"G-12"}:
            # SPEC §8.4: G-12 never raises a confidence and never notifies.
            confidence = "low"
            severity = "low"
        headline = (f"{root}: {len(self.findings)} finding(s) "
                    f"({', '.join(sorted(self.rules))})")
        if self.unit:
            headline = f"{self.unit}: " + headline
        payload: Dict[str, Any] = {
            "schema": events.INCIDENT_SCHEMA,
            "id": self.id,
            "headline": headline[:240],
            "root_cause": root,
            "confidence": confidence,
            # additive fields inside major /1 (ADR-014 §2): status exit codes
            # and the suppression matrix need them without re-reading findings
            "severity": severity,
            "first": self.first,
            "last": self.last,
            "resolved_at": resolved_at,
            "unit": self.unit,
            "profile": self.profile,
            "findings": list(self.findings)[:MAX_FINDINGS_PER_INCIDENT],
        }
        if server is not None and (server.present or server.threat_level
                                   is not None or server.lockdown):
            payload["server"] = server.as_finding()
        if cross_vantage:
            payload["cross_vantage"] = True
        if expected_lockdown:
            payload["expected_lockdown"] = True
        errors = schema.validate(payload, events.INCIDENT_SCHEMA)
        if errors:
            raise ValueError("invalid guard.incident/1: " + "; ".join(errors))
        return payload


class Correlator:
    """Group findings into ``guard.incident/1`` and close them after 10 min."""

    def __init__(self, *, server: Optional[ServerContext] = None,
                 window_s: int = INCIDENT_WINDOW_S,
                 resolve_s: int = INCIDENT_RESOLVE_S,
                 max_findings: int = MAX_FINDINGS_PER_INCIDENT,
                 max_history: int = MAX_HISTORY) -> None:
        self.window_us = max(1, int(window_s)) * MONO_US_PER_S
        self.resolve_us = max(1, int(resolve_s)) * MONO_US_PER_S
        self.max_findings = max(1, int(max_findings))
        self.max_history = max(1, int(max_history))
        self._server = server
        self._open: Dict[str, _OpenIncident] = {}
        self._history: Deque[Dict[str, Any]] = deque(maxlen=self.max_history)

    # -- contexts ------------------------------------------------------------

    def set_server(self, server: Optional[ServerContext]) -> None:
        self._server = server

    @property
    def server(self) -> Optional[ServerContext]:
        return self._server

    # -- ingestion -----------------------------------------------------------

    @staticmethod
    def _key(finding: Mapping[str, Any]) -> str:
        unit = finding.get("unit")
        if isinstance(unit, str) and unit:
            return unit
        profile = finding.get("profile")
        if isinstance(profile, str) and profile:
            return f"profile:{profile}"
        return "-"

    def note(self, finding: Dict[str, Any],
             mono_us: int) -> Dict[str, Any]:
        """Attach *finding* to an open incident; returns the incident payload.

        The finding dict is mutated in place: ``incident_id`` is part of
        ``guard.finding/1`` and the sink writes the enriched copy.
        """
        first, last = _window(finding)
        if not first or not last:
            first = last = first or last
        key = self._key(finding)
        stamp = int(mono_us)
        incident = self._open.get(key)
        if incident is not None and stamp - incident.last_us > self.window_us:
            self._close(incident, first or incident.last)
            incident = None
        if incident is None:
            incident = _OpenIncident(
                key=key,
                id=incident_id(key, first),
                unit=finding.get("unit") if isinstance(
                    finding.get("unit"), str) else None,
                profile=(finding.get("profile")
                         if isinstance(finding.get("profile"), str) else None),
                first=first,
                last=last,
                last_us=stamp,
                findings=[],
                rules=set(),
                categories=set(),
                confidences=[],
                severities=[],
                created_us=stamp,
            )
            self._open[key] = incident
        incident.last = max(incident.last, last)
        incident.last_us = stamp
        rule = finding.get("rule")
        if isinstance(rule, str):
            incident.rules.add(rule)
        category = finding.get("category")
        if isinstance(category, str):
            incident.categories.add(category)
        confidence = finding.get("confidence")
        if isinstance(confidence, str):
            incident.confidences.append(confidence)
        severity = finding.get("severity")
        if isinstance(severity, str):
            incident.severities.append(severity)
        finding_id = finding.get("id")
        if (isinstance(finding_id, str)
                and finding_id not in incident.findings
                and len(incident.findings) < self.max_findings):
            incident.findings.append(finding_id)
        finding["incident_id"] = incident.id
        return incident.as_dict(self._server)

    # -- lifecycle -----------------------------------------------------------

    def tick(self, now_us: int, now_ts: str) -> List[Dict[str, Any]]:
        """Close incidents quiet for ``resolve_s``; returns resolved payloads."""
        resolved: List[Dict[str, Any]] = []
        stamp = int(now_us)
        for key in list(self._open):
            incident = self._open[key]
            if stamp - incident.last_us < self.resolve_us:
                continue
            resolved.append(self._close(incident, now_ts))
        return resolved

    def _close(self, incident: _OpenIncident,
               resolved_at: Optional[str]) -> Dict[str, Any]:
        payload = incident.as_dict(self._server, resolved_at=resolved_at)
        self._open.pop(incident.key, None)
        self._history.appendleft(payload)
        return payload

    # -- introspection -------------------------------------------------------

    def open_incidents(self) -> List[Dict[str, Any]]:
        """Current open incidents, oldest first (``status --json``)."""
        return [incident.as_dict(self._server)
                for incident in self._open.values()]

    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def high_open(self) -> bool:
        """``True`` when an open incident is ``high`` and not a self-degrade."""
        for incident in self._open.values():
            payload = incident.as_dict(self._server)
            if payload.get("severity") == "high" and payload["root_cause"] != (
                    "observability"):
                return True
        return False

    def incident_for(self, finding_id: str) -> Optional[Dict[str, Any]]:
        for incident in self._open.values():
            if finding_id in incident.findings:
                return incident.as_dict(self._server)
        for payload in self._history:
            if finding_id in payload.get("findings", []):
                return payload
        return None


__all__ = [
    "CONFIDENCE_RANK",
    "Correlator",
    "INCIDENT_RESOLVE_S",
    "INCIDENT_WINDOW_S",
    "ROOT_CAUSE_PRIORITY",
    "SERVER_VISIBLE_CAUSES",
    "SEVERITY_RANK",
    "incident_id",
]
