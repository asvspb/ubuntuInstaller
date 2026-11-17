"""WP-S4: the ``vpn scout`` command surface (also used by ``vpn up --scout``).

Read-only by construction: the report is built from the profile store, the
guard artefacts and the probes; nothing is written.  The only network call
is the explicit ``--sync``, which delegates to the existing
:func:`vpnm.guard.verdict.sync` (one ssh fetch, unchanged contract).

JSON contract — ``scout.report/1``::

    {
      "schema": "scout.report/1",
      "created": "<RFC3339 UTC>",
      "context": {"verdict": {...}|null, "verdict_fresh": false,
                  "verdict_age_s": null, "threat_level": null,
                  "server_verdict": null, "lockdown_443": false,
                  "findings_recent": 0, "g04_endpoints": [],
                  "notes": []},
      "threat_level": null,
      "recommended": <row>|null,
      "fallback": <row>|null,
      "rejected": [<row>, ...],
      "candidates": [<row>, ...],
      "notify": {"attempted": false}
    }

where ``<row>`` is the candidate (route/proto/profile/host/port/sni/
port_hop_range) plus ``probe`` (status/rtt_ms/detail), ``score``, ``status``
(``recommended``/``fallback``/``rejected``) and human-readable ``reasons``.

The top-level ``threat_level`` mirrors ``context.threat_level`` (additive in
the same ``scout.report/1`` version: old consumers that only read
``context.threat_level`` are unaffected).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from .context import ScoutContext, build_context
from .models import PROTO_AWG, ROUTE_DIRECT, ROUTE_RELAY
from .probes import DEFAULT_BUDGET_MS, DEFAULT_TIMEOUT_S, probe_all
from .rank import RankReport, rank

SCHEMA = "scout.report/1"


def _iso_now(now: Optional[float] = None) -> str:
    moment = (datetime.fromtimestamp(now, tz=timezone.utc)
              if now is not None else datetime.now(timezone.utc))
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _context_payload(context: ScoutContext) -> Dict[str, Any]:
    return {
        "verdict": context.verdict,
        "verdict_fresh": context.verdict_fresh,
        "verdict_age_s": context.verdict_age_s,
        "threat_level": context.threat_level,
        "server_verdict": context.server_verdict,
        "lockdown_443": context.lockdown_443,
        "findings_recent": len(context.findings),
        "g04_endpoints": context.g04_endpoints,
        "notes": list(context.notes),
    }


def _notify_blocked(report: RankReport, active_profile: Optional[str],
                    notifier: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Guard notification when the **current** channel itself is blocked.

    The existing :class:`vpnm.guard.notify.Notifier` contract is reused as-is:
    it receives a finding-shaped mapping (no file is written, the opt-in and
    the rate limit stay in force) and the body carries the next command to
    run — ``vpn switch <profile>``.
    """
    row = report.row_for_profile(active_profile)
    if row is None or not row.probe.hard_fail:
        return None
    hint = "vpn scout (нет рекомендации)"
    for candidate_row in (report.recommended, report.fallback):
        if candidate_row is None:
            continue
        candidate = candidate_row.candidate
        if candidate.proto == PROTO_AWG:
            hint = ("vpn start relay" if candidate.route == ROUTE_RELAY
                    else "vpn start")
            break
        if candidate.profile:
            hint = f"vpn switch {candidate.profile}"
            break
    finding = {
        "rule": "G-03",
        "severity": "high",
        "confidence": "medium",
        "unit": "vpn-scout",
        "profile": active_profile,
        "hypothesis": f"текущий канал недоступен: {row.probe.status}",
        "action": hint,
    }
    instance = notifier
    if instance is None:
        from ..guard.notify import Notifier
        instance = Notifier()
    sent = bool(instance.notify(finding))
    return {"attempted": True, "sent": sent,
            "reason": getattr(instance, "last_reason", None),
            "hint": hint}


def build_report(*, sync: bool = False,
                 host: Optional[str] = None,
                 sync_timeout: float = 10.0,
                 profiles_path: Optional[Path] = None,
                 awg_conf_dir: Optional[Path] = None,
                 verdict_path: Optional[Path] = None,
                 findings_path: Optional[Path] = None,
                 now: Optional[float] = None,
                 active_profile: Optional[str] = None,
                 notifier: Optional[Any] = None,
                 budget_ms: int = DEFAULT_BUDGET_MS,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 ) -> Dict[str, Any]:
    """Assemble the ``scout.report/1`` payload (read-only without ``--sync``)."""
    notes: List[str] = []
    if sync:
        from ..guard import verdict as verdict_mod
        result = verdict_mod.sync(host=host, timeout=sync_timeout,
                                  path=verdict_path)
        if result.ok:
            notes.append(f"server-verdict обновлён: verdict={result.verdict} "
                         f"threat={result.threat_level}")
        else:
            notes.append(f"sync не выполнен: {result.error or 'unknown'}; "
                         "использован прежний server-verdict")

    if active_profile is None:
        from .. import runners
        active_profile = runners.get_active()

    context = build_context(profiles_path=profiles_path,
                            awg_conf_dir=awg_conf_dir,
                            verdict_path=verdict_path,
                            findings_path=findings_path,
                            now=now)
    context.notes.extend(notes)
    results = probe_all(context.candidates,
                        budget_ms=budget_ms, timeout_s=timeout_s,
                        lockdown_443=context.lockdown_443,
                        recent_g04_endpoints=context.g04_endpoints)
    report = rank(context.candidates, results,
                  lockdown_443=context.lockdown_443,
                  recent_g04_endpoints=context.g04_endpoints,
                  active_profile=active_profile)

    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "created": _iso_now(now),
        "context": _context_payload(context),
        "threat_level": context.threat_level,
        "recommended": (report.recommended.as_dict()
                        if report.recommended is not None else None),
        "fallback": (report.fallback.as_dict()
                     if report.fallback is not None else None),
        "rejected": [row.as_dict() for row in report.rejected],
        "candidates": [row.as_dict() for row in report.rows],
        "notify": {"attempted": False},
    }
    if active_profile is not None:
        payload["context"]["active_profile"] = active_profile
    if active_profile is not None:
        try:
            outcome = _notify_blocked(report, active_profile, notifier)
        except (OSError, ValueError) as exc:  # notification is never fatal
            outcome = {"attempted": True, "sent": False,
                       "reason": f"notify_error:{type(exc).__name__}"}
        if outcome is not None:
            payload["notify"] = outcome
    return payload


# --- rendering ------------------------------------------------------------------


def _row_line(tag: str, row: Dict[str, Any]) -> str:
    candidate = row
    probe = candidate.get("probe") or {}
    head = (f"{tag:<12} {candidate.get('route')}/{candidate.get('proto'):<13} "
            f"{str(candidate.get('profile') or '-'):<18} "
            f"{candidate.get('host')}:{candidate.get('port')} "
            f"score={candidate.get('score')} probe={probe.get('status')}")
    return head


def render_lines(payload: Dict[str, Any]) -> List[str]:
    """Plain-text view of the report (colors are added by the CLI)."""
    context = payload.get("context") or {}
    verdict = context.get("server_verdict") or "-"
    threat = context.get("threat_level") or "-"
    freshness = ("свежий" if context.get("verdict_fresh")
                 else "устарел/нет")
    lines: List[str] = [
        f"[scout] кандидатов: {len(payload.get('candidates') or [])}; "
        f"verdict={verdict} threat={threat} ({freshness}); "
        f"lockdown 443: {'да' if context.get('lockdown_443') else 'нет'}; "
        f"findings (G-01/03/04, 30 мин): "
        f"{context.get('findings_recent', 0)}",
    ]
    if context.get("verdict_fresh") and threat in ("elevated", "high"):
        lines.append(f"[info] threat={threat} — 443/tcp может уйти в lock-443; "
                     "держите relay/AWG под рукой")
    recommended = payload.get("recommended")
    fallback = payload.get("fallback")
    if recommended:
        lines.append(_row_line("RECOMMENDED", recommended))
    if fallback:
        lines.append(_row_line("FALLBACK", fallback))
    rejected = payload.get("rejected") or []
    if rejected:
        lines.append("[warning] отсеяны:")
        for row in rejected:
            lines.append("  - " + _row_line("", row).strip()
                         + " — " + "; ".join(row.get("reasons") or []))
    if not recommended:
        lines.append("[warning] рекомендации нет: ни один маршрут не набрал "
                     "50+ (см. причины выше)")
    for note in context.get("notes") or []:
        lines.append(f"[info] {note}")
    notify = payload.get("notify") or {}
    if notify.get("attempted"):
        state = "отправлено" if notify.get("sent") else (
            f"не отправлено ({notify.get('reason') or 'без причины'})")
        lines.append(f"[info] guard-notify: {state}"
                     + (f"; подсказка: {notify['hint']}"
                        if notify.get("hint") else ""))
    return lines


def run_scout_cli(*, as_json: bool = False, sync: bool = False,
                  host: Optional[str] = None) -> int:
    """Print the report; exit 0 when a route is recommended, 1 otherwise."""
    payload = build_report(sync=sync, host=host)
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2,
                              default=str))
    else:
        for line in render_lines(payload):
            if line.startswith("[warning]"):
                typer.secho(line, fg=typer.colors.YELLOW)
            elif line.startswith("RECOMMENDED"):
                typer.secho(line, fg=typer.colors.GREEN)
            elif line.startswith("FALLBACK"):
                typer.secho(line, fg=typer.colors.CYAN)
            else:
                typer.echo(line)
    return 0 if payload.get("recommended") else 1


# --- `vpn up --scout` -------------------------------------------------------------


def up_target(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """What ``vpn up --scout`` would run, derived from the recommendation."""
    recommended = payload.get("recommended")
    if not recommended:
        return {"kind": "none"}
    if recommended.get("proto") == PROTO_AWG:
        route = recommended.get("route") or ROUTE_DIRECT
        return {"kind": "awg", "route": route,
                "argv": "vpn start relay" if route == ROUTE_RELAY
                        else "vpn start"}
    profile = recommended.get("profile")
    if not profile:
        return {"kind": "none"}
    return {"kind": "profile", "profile": str(profile),
            "argv": f"vpn up {profile}"}


def plan_lines(payload: Dict[str, Any]) -> List[str]:
    """Explain, in one line, what the planned action is (dry-run included)."""
    target = up_target(payload)
    if target["kind"] == "none":
        return ["ничего не запускаю: рекомендации нет "
                "(vpn scout — причины)"]
    row = payload.get("recommended") or {}
    probe = row.get("probe") or {}
    return [f"запустил бы: {target['argv']} "
            f"(score={row.get('score')}, probe={probe.get('status')})"]


# Re-exported for the tests of the ranking thresholds.
__all__ = [
    "SCHEMA",
    "build_report",
    "plan_lines",
    "render_lines",
    "run_scout_cli",
    "up_target",
]
