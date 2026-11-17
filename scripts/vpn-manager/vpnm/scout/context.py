"""WP-S1: what the scout knows before it probes anything.

Three read-only inputs, all through the existing modules (no path is
hardcoded here):

1. **candidates** — built from the real profile store
   (:func:`vpnm.profiles.list_profiles`, share links parsed by
   :func:`vpnm.links.parse_link`) and from the real AmneziaWG client configs
   in ``~/.awg/*.conf`` (``Endpoint = host:port``);
2. **server verdict** — ``server-verdict.json`` via
   :func:`vpnm.guard.verdict.load_verdict`; the scout trusts it only while it
   is **fresh (≤ 10 min)**, tighter than the guard's own 65-min window: a
   route decision made on an hour-old ``lockdown`` is a guess;
3. **recent findings** — G-01/G-03/G-04 from ``findings.jsonl`` within the
   last 30 minutes (read through :class:`vpnm.guard.store.FindingsStore`,
   whose ``read`` never writes).

Nothing in this module writes: the guard artefacts are opened read-only and
the store is never mutated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .. import profiles as profiles_mod
from ..links import (
    PROTOCOL_HYSTERIA2,
    PROTOCOL_XRAY_REALITY,
    PROTOCOL_XRAY_WS,
    PROTOCOL_XRAY_XHTTP,
    LinkError,
    ProfileLink,
    parse_link,
)
from .models import (
    PROTO_AWG,
    PROTO_HY2,
    PROTO_XRAY_CDN,
    PROTO_XRAY_REALITY,
    PROTO_XRAY_XHTTP,
    ROUTE_DIRECT,
    ROUTE_RELAY,
    Candidate,
)

#: Verdict older than this is not used for a route decision (WP-S1).
VERDICT_FRESH_S = 10 * 60
#: Findings window that feeds the "recent UDP black hole" signal (WP-S1).
RECENT_FINDINGS_S = 30 * 60
#: Rules whose recent history changes a route decision.
RECENT_RULES: Tuple[str, ...] = ("G-01", "G-03", "G-04")
#: Relay profile (``vpn start relay --proto reality``) and relay AWG instance.
RELAY_PROFILE = "relay-reality"
RELAY_INSTANCE = "relay"

#: Share-link protocol → scout protocol.
LINK_PROTO_MAP: Mapping[str, str] = {
    PROTOCOL_XRAY_REALITY: PROTO_XRAY_REALITY,
    PROTOCOL_XRAY_XHTTP: PROTO_XRAY_XHTTP,
    PROTOCOL_XRAY_WS: PROTO_XRAY_CDN,
    PROTOCOL_HYSTERIA2: PROTO_HY2,
}


def _sni(parsed: ProfileLink) -> Optional[str]:
    """TLS SNI the generated client config actually uses (mirrors xclient)."""
    value = parsed.params.get("sni") or ""
    if value:
        return value
    if parsed.protocol in (PROTOCOL_XRAY_REALITY, PROTOCOL_XRAY_XHTTP,
                           PROTOCOL_XRAY_WS, PROTOCOL_HYSTERIA2):
        return parsed.host
    return None


def _port_hop(parsed: ProfileLink) -> Optional[str]:
    """Hysteria2 ``mport`` range from the link (``None`` when absent)."""
    if parsed.protocol != PROTOCOL_HYSTERIA2:
        return None
    value = (parsed.params.get("mport") or "").strip()
    return value or None


def profile_candidates(
        profiles_path: Optional[Path] = None) -> Tuple[List[Candidate],
                                                        List[str]]:
    """Real store profiles → candidates (direct, except ``relay-*``).

    A profile named :data:`RELAY_PROFILE` (the socks fallback of
    ``vpn start relay``) is a **relay** candidate; everything else is direct.
    Unparsable links are reported as notes, never guessed around.
    """
    candidates: List[Candidate] = []
    notes: List[str] = []
    try:
        records = profiles_mod.list_profiles(profiles_path)
    except profiles_mod.ProfileStoreError as exc:
        return [], [f"profiles: {exc}"]
    for record in records:
        name = str(record.get("name") or "")
        link_text = str(record.get("link") or "")
        if not link_text:
            notes.append(f"profile '{name}': нет share-ссылки")
            continue
        try:
            parsed = parse_link(link_text)
        except LinkError as exc:
            notes.append(f"profile '{name}': ссылка не разбирается ({exc})")
            continue
        proto = LINK_PROTO_MAP.get(parsed.protocol)
        if proto is None:
            notes.append(f"profile '{name}': неизвестный протокол "
                         f"'{parsed.protocol}'")
            continue
        route = ROUTE_RELAY if name == RELAY_PROFILE else ROUTE_DIRECT
        candidates.append(Candidate(
            route=route,
            proto=proto,
            profile=name,
            host=parsed.host,
            port=parsed.port,
            sni=_sni(parsed),
            port_hop_range=_port_hop(parsed),
        ))
    return candidates, notes


def _parse_endpoint(text: str) -> Optional[Tuple[str, int]]:
    """``Endpoint = host:port`` (also ``[v6]:port``) from an AWG config."""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() != "endpoint":
            continue
        value = value.strip()
        if not value:
            return None
        if value.startswith("["):
            host, _, rest = value[1:].partition("]")
            port_s = rest.lstrip(":")
        else:
            host, _, port_s = value.rpartition(":")
        if not host or not port_s.isdigit():
            return None
        port = int(port_s)
        if not 1 <= port <= 65535:
            return None
        return host, port
    return None


def awg_candidates(conf_dir: Optional[Path] = None) -> Tuple[List[Candidate],
                                                             List[str]]:
    """AWG client configs → candidates (``relay.conf`` is the relay leg).

    Only the ``Endpoint`` is read: the config also holds the private key and
    preshared key, which never leave this function.
    """
    if conf_dir is None:
        from .. import amneziawg as awg_mod
        conf_dir = awg_mod.LOCAL_CONF_DIR
    candidates: List[Candidate] = []
    notes: List[str] = []
    try:
        paths = sorted(Path(conf_dir).glob("*.conf"))
    except OSError as exc:
        return [], [f"awg: {conf_dir}: {exc}"]
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            notes.append(f"awg '{path.stem}': {exc}")
            continue
        endpoint = _parse_endpoint(text)
        if endpoint is None:
            notes.append(f"awg '{path.stem}': нет Endpoint в конфиге")
            continue
        host, port = endpoint
        route = ROUTE_RELAY if path.stem == RELAY_INSTANCE else ROUTE_DIRECT
        candidates.append(Candidate(
            route=route,
            proto=PROTO_AWG,
            profile=path.stem,
            host=host,
            port=port,
            sni=None,
        ))
    return candidates, notes


def _dedupe(candidates: Sequence[Candidate]) -> Tuple[List[Candidate],
                                                      List[str]]:
    """Drop candidates that share a key (same route/proto/endpoint)."""
    seen: Dict[str, Candidate] = {}
    notes: List[str] = []
    for candidate in candidates:
        if candidate.key in seen:
            notes.append(f"дубль {candidate.key} "
                         f"({seen[candidate.key].profile} / "
                         f"{candidate.profile}) — оставлен первый")
            continue
        seen[candidate.key] = candidate
    return list(seen.values()), notes


def collect_candidates(profiles_path: Optional[Path] = None,
                       awg_conf_dir: Optional[Path] = None,
                       ) -> Tuple[List[Candidate], List[str]]:
    """All real candidates + notes about what could not be turned into one."""
    from_profiles, notes_p = profile_candidates(profiles_path)
    from_awg, notes_a = awg_candidates(awg_conf_dir)
    candidates, notes_d = _dedupe(from_profiles + from_awg)
    return candidates, notes_p + notes_a + notes_d


# --- server verdict -----------------------------------------------------------


def read_verdict(path: Optional[Path] = None
                 ) -> Tuple[Optional[Dict[str, Any]], bool, Optional[int]]:
    """``(payload, fresh, age_s)`` — fresh means ≤ :data:`VERDICT_FRESH_S`.

    A missing/broken file is never an error (R-9): the caller degrades to
    ``verdict=None, fresh=False``.
    """
    from ..guard import verdict as verdict_mod

    payload = verdict_mod.load_verdict(path)
    if payload is None:
        return None, False, None
    age = verdict_mod.age_seconds(payload.get("fetched_at"))
    if age is None:
        return payload, False, None
    return payload, age <= VERDICT_FRESH_S, age


def lockdown_active(payload: Optional[Mapping[str, Any]]) -> bool:
    """``lockdown.active`` of the verdict payload (False when unset)."""
    if not isinstance(payload, Mapping):
        return False
    block = payload.get("lockdown")
    return isinstance(block, Mapping) and bool(block.get("active"))


# --- recent findings ----------------------------------------------------------


def read_recent_findings(path: Optional[Path] = None, *,
                         now: Optional[float] = None,
                         window_s: int = RECENT_FINDINGS_S
                         ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """G-01/G-03/G-04 findings of the last ``window_s`` (read-only).

    ``FindingsStore.read`` only opens files for reading; no directory is
    created and no rotation runs (that lives in ``append``).
    """
    from ..guard import store

    target = path if path is not None else store.findings_path()
    moment = time.time() if now is None else float(now)
    findings_store = store.FindingsStore(path=target)
    items = findings_store.read(since_ts=moment - window_s, limit=200)
    recent = [item for item in items
              if isinstance(item, dict) and item.get("rule") in RECENT_RULES]
    notes: List[str] = []
    if not target.exists():
        notes.append("findings.jsonl нет — история guard пуста")
    return recent, notes


def g04_keys(findings: Sequence[Mapping[str, Any]]) -> List[str]:
    """Endpoint keys of recent G-04 findings (``host:port``, no secrets)."""
    keys: List[str] = []
    for finding in findings:
        if not isinstance(finding, Mapping) or finding.get("rule") != "G-04":
            continue
        endpoint = finding.get("endpoint")
        if not isinstance(endpoint, Mapping):
            continue
        host = endpoint.get("host")
        port = endpoint.get("port")
        if isinstance(host, str) and isinstance(port, int):
            keys.append(f"{host}:{port}")
    return keys


def has_recent_udp_blackhole(findings: Sequence[Mapping[str, Any]],
                             host: str, port: int) -> bool:
    """True when G-04 fired for this endpoint within the window."""
    return f"{host}:{port}" in set(g04_keys(findings))


# --- the assembled context ----------------------------------------------------


@dataclass
class ScoutContext:
    """Everything the probes and the ranker need, already read from disk."""

    candidates: List[Candidate] = field(default_factory=list)
    verdict: Optional[Dict[str, Any]] = None
    verdict_fresh: bool = False
    verdict_age_s: Optional[int] = None
    lockdown_443: bool = False
    findings: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    now: Optional[float] = None

    @property
    def g04_endpoints(self) -> List[str]:
        return g04_keys(self.findings)

    @property
    def threat_level(self) -> Optional[str]:
        value = (self.verdict or {}).get("threat_level")
        return value if isinstance(value, str) else None

    @property
    def server_verdict(self) -> Optional[str]:
        value = (self.verdict or {}).get("verdict")
        return value if isinstance(value, str) else None


def build_context(profiles_path: Optional[Path] = None,
                  awg_conf_dir: Optional[Path] = None,
                  verdict_path: Optional[Path] = None,
                  findings_path: Optional[Path] = None,
                  now: Optional[float] = None) -> ScoutContext:
    """Read the three inputs once; never writes anything."""
    moment = time.time() if now is None else float(now)
    candidates, notes = collect_candidates(profiles_path, awg_conf_dir)
    payload, fresh, age = read_verdict(verdict_path)
    findings, notes_f = read_recent_findings(findings_path, now=moment)
    if payload is not None and not fresh:
        notes.append(f"server-verdict.json устарел (age={age}s > "
                     f"{VERDICT_FRESH_S}s) — лок не учитывается; "
                     "обновите: vpn guard sync")
    if not candidates:
        notes.append("кандидатов нет: профили не импортированы "
                     "(vpn profile list) и AWG-конфигов в ~/.awg нет")
    return ScoutContext(
        candidates=candidates,
        verdict=payload,
        verdict_fresh=fresh,
        verdict_age_s=age,
        lockdown_443=bool(fresh and lockdown_active(payload)),
        findings=findings,
        notes=notes + notes_f,
        now=moment,
    )
