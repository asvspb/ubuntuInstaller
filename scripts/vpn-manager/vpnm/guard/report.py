"""Human-readable rendering of findings/incidents (WP-G3/WP-G4, SPEC §10.1).

Nothing here reads files, clocks or the network: the CLI hands in already
validated ``guard.finding/1`` / ``guard.incident/1`` payloads.  Rendering uses
counters, rule ids and the prose fields only — never log text or secrets.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional


def _clock(finding: Mapping[str, Any]) -> str:
    window = finding.get("window")
    if isinstance(window, Mapping):
        first = window.get("first")
        if isinstance(first, str) and len(first) >= 19:
            return first[11:19]
    return "--:--:--"


def finding_line(finding: Mapping[str, Any]) -> str:
    """One-line summary: ``[guard] 12:00:03 G-03 high/medium vpnm-asv2 …``."""
    unit = finding.get("unit") or finding.get("profile") or "-"
    return (f"[guard] {_clock(finding)} {finding.get('rule')} "
            f"{finding.get('severity')}/{finding.get('confidence')}  "
            f"{unit}  count={finding.get('count')} "
            f"{finding.get('category')}/{finding.get('stage_hint') or '-'}")


def incident_line(incident: Mapping[str, Any]) -> str:
    return (f"[guard] incident {incident.get('id')} "
            f"root_cause={incident.get('root_cause')} "
            f"({incident.get('confidence')})")


def render_finding(finding: Mapping[str, Any]) -> List[str]:
    """Multi-line block: evidence, executed FP checks and the action."""
    lines = [finding_line(finding)]
    facts: List[str] = []
    for item in finding.get("evidence") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("signal"):
            facts.append(f"{item['signal']}={item.get('value')}")
        elif item.get("metric"):
            value = item.get("value")
            if value is None:
                value = item.get("median")
            facts.append(f"{item['metric']}={value}")
    if facts:
        lines.append("        evidence: " + ", ".join(facts))
    checks = finding.get("fp_checks") or []
    if checks:
        lines.append("        fp: " + ", ".join(str(item) for item in checks))
    if finding.get("expected_lockdown"):
        lines.append("        expected_lockdown: активен серверный lock-443")
    action = finding.get("action")
    if action:
        lines.append(f"        action: {action}")
    return lines


def explain(finding: Mapping[str, Any],
            incident: Optional[Mapping[str, Any]] = None) -> str:
    """Why the rule fired: window, evidence, FP checks, merge and action."""
    lines = [
        f"finding {finding.get('id')}  rule={finding.get('rule')}  "
        f"severity={finding.get('severity')}/{finding.get('confidence')}",
        f"category={finding.get('category')}  "
        f"stage_hint={finding.get('stage_hint')}  "
        f"unit={finding.get('unit') or '-'}  "
        f"profile={finding.get('profile') or '-'}",
    ]
    endpoint = finding.get("endpoint")
    if isinstance(endpoint, Mapping):
        lines.append(f"endpoint: {endpoint.get('host')}:{endpoint.get('port')}")
    window = finding.get("window")
    if isinstance(window, Mapping):
        lines.append(f"window: {window.get('first')} .. {window.get('last')}")
    lines.append(f"count: {finding.get('count')}")
    for item in finding.get("evidence") or []:
        if isinstance(item, Mapping):
            lines.append("evidence: " + ", ".join(
                f"{key}={value}" for key, value in item.items()))
    for check in finding.get("fp_checks") or []:
        lines.append(f"fp_check: {check}")
    server = finding.get("server")
    if isinstance(server, Mapping):
        lines.append(
            "server: threat={threat} stage={stage} age={age}s fresh={fresh} "
            "lockdown={lockdown}".format(
                threat=server.get("threat_level"),
                stage=server.get("stage"),
                age=server.get("age_s"),
                fresh=server.get("fresh"),
                lockdown=server.get("lockdown")))
    if finding.get("cross_vantage"):
        lines.append("cross_vantage: да (второй vantage подтвердил)")
    if finding.get("expected_lockdown"):
        lines.append("expected_lockdown: да (lock-443 объясняет обрывы)")
    hypothesis = finding.get("hypothesis")
    if hypothesis:
        lines.append(f"hypothesis: {hypothesis}")
    for item in finding.get("alternatives") or []:
        lines.append(f"alternative: {item}")
    action = finding.get("action")
    if action:
        lines.append(f"action: {action}")
    if incident is not None:
        lines.append(incident_line(incident))
        lines.append(f"incident findings: {len(incident.get('findings') or [])}")
    return "\n".join(lines)


def finding_dict_line(finding: Mapping[str, Any]) -> Dict[str, Any]:
    """JSON view without reordering contract keys (CLI ``findings --json``)."""
    return dict(finding)


def rules_payload(config_root: Optional[Any] = None) -> Dict[str, Any]:
    """``vpn guard rules --json``: catalogue + effective thresholds + hashes.

    Hashes are deterministic (no clock, no state): ``rules_hash`` covers the
    catalogue **and** the effective thresholds, ``config_hash`` the whole
    effective config, ``schemas`` the seven contract files (SPEC §10.2).
    """
    from . import schema, store
    from .rules import rule_catalog

    return {
        "rules": rule_catalog(),
        "thresholds": store.effective_thresholds(config_root).as_dict(),
        "config": store.effective_config(config_root),
        "rules_hash": store.rules_hash(config_root),
        "config_hash": store.config_hash(config_root),
        "schemas": schema.schema_hashes(),
    }


def render_rules(payload: Mapping[str, Any], *,
                 verbose: bool = False) -> List[str]:
    """Human table for ``vpn guard rules`` (no values from logs)."""
    lines: List[str] = []
    for item in payload.get("rules") or []:
        if not isinstance(item, Mapping):
            continue
        flag = "" if item.get("notify", True) else "  (не уведомляет)"
        lines.append(
            f"{item.get('id')}  {item.get('name')}  "
            f"{item.get('severity')}/{item.get('category')}  "
            f"stage={item.get('stage')}{flag}")
        summary = item.get("summary")
        if summary:
            lines.append(f"      {summary}")
    thresholds = payload.get("thresholds")
    if isinstance(thresholds, Mapping):
        lines.append("")
        lines.append("thresholds:")
        for name in sorted(thresholds):
            lines.append(f"      {name}={thresholds[name]}")
    if verbose:
        config = payload.get("config")
        if isinstance(config, Mapping):
            lines.append("")
            lines.append("effective config: "
                         + json.dumps(config, ensure_ascii=False,
                                      sort_keys=True))
    lines.append("")
    lines.append(f"rules_hash={payload.get('rules_hash')}")
    lines.append(f"config_hash={payload.get('config_hash')}")
    return lines


__all__ = [
    "explain",
    "finding_dict_line",
    "finding_line",
    "incident_line",
    "render_finding",
    "render_rules",
    "rules_payload",
]
