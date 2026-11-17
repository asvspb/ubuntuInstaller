"""Structured-first parser of journal lines into ``guard.event/1`` dicts.

Pipeline (SPEC-20261116-vpn-guard §7):

1. **structured-first** — systemd lifecycle from ``_SYSTEMD_USER_UNIT`` /
   ``_SYSTEMD_UNIT`` / ``UNIT`` + ``JOB_TYPE``/``JOB_RESULT``/``EXIT_CODE``/
   ``EXIT_STATUS`` (locale-free);
2. **structured app payloads** — Xray/sing-box log lines are JSON
   (``{"level":…, "msg"/"message":…}``); parsed before any regex;
3. **app-regex fallback** — ordered catalogue, first match wins (English,
   C-locale app logs of Xray/Hysteria2 only — never localized systemd text);
4. **unparsed** — unknown app lines become a real ``unparsed`` event with a
   template hash (never a crash, never silence).

The raw line text is never stored: only the signal, a normalized template
hash (:func:`template_hash`) and an endpoint allowlist.  Every event dict
returned conforms to ``guard.event/1`` (validated in tests, not per line in
the daemon hot path).

A line of an own unit that matches no family becomes ``unparsed`` (never
silence) — that is how catalogue/version drift surfaces (RSK-1).  This
includes ``awg-quick@*`` shell output: AmneziaWG has no app-log contract
(§4.3), so such lines are honestly unparseable, while its real observability
comes from lifecycle events (S2) and sysfs counters (S3).

Message text is bounded by :data:`MAX_MESSAGE_CHARS`: one pathological line
must not stall the single analyzer thread (R-6) or blow the template
normaliser's regex budget.

This module does no I/O and imports nothing from the rest of ``vpnm`` except
the frozen registry.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from .events import EVENT_SCHEMA, LEVELS, SIGNALS

# --- unit classification ---------------------------------------------------------

VPNM_PREFIX = "vpnm-"
AWG_UNIT_RES = ("vpn-awg-split@", "awg-quick@")
GUARD_UNIT = "vpn-guard.service"
GUARD_IDENTIFIER = "vpn-guard"

#: App-log sources that make up the AC-5 denominator (``unparsed < 5%``).
#: ``unknown`` is a vpnm unit whose proto hint was not supplied — it is still
#: an app source, not infra.  dns/zapret/awg-quick lines never enter it.
APP_PROTOS: Tuple[str, ...] = ("xray", "hysteria2", "unknown")

#: awg-quick shell output: two lifecycle lines matter, the rest is infra noise.
AWG_QUICK_ADD_RE = re.compile(r"^\s*\[#\]\s+ip link add\b")
AWG_QUICK_DEL_RE = re.compile(r"^\s*\[#\]\s+ip link delete\b")

#: «Локальная сеть недоступна» (G-07).  Держим здесь, чтобы парсер мог
#: отбросить IPv6-вариант (IPv4-only хост) до того, как он дойдёт до правил.
LOCAL_NET_SIGNALS: frozenset = frozenset(("network_unreachable", "no_route"))

#: journald truncates long messages itself (``LineMax``); the parser keeps its
#: own bound so a single pathological line cannot stall the analyzer thread
#: (§12 burst budget) and the template normalisation stays linear-ish.
MAX_MESSAGE_CHARS = 8192

# --- template normalisation (§7.4) -----------------------------------------------

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}\b"
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}\b")
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?(?:ms|us|ns|s|m|h|k|mib|kb|mb|gb)?\b")
_HEX16_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_PATH_RE = re.compile(r"(?:/[A-Za-z0-9_.\-]+)+/?")
_QUOTED_RE = re.compile(r'"[^"]*"')
#: Local/domain parts are length-bounded: an unbounded ``[...]+@`` run is
#: quadratic on adversarial input (one log line must stay cheap, R-6).
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"\.[A-Za-z]{2,24}\b"
)
_DOMAIN_RE = re.compile(
    r"\b(?:[A-Za-z0-9-]{1,63}\.){1,6}[A-Za-z]{2,24}\b"
)

_TEMPLATE_STEPS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("<uuid>", _UUID_RE),
    ("<email>", _EMAIL_RE),
    ("<ip>", _IPV4_RE),
    ("<ip>", _IPV6_RE),
    ("<path>", _PATH_RE),
    ("<str>", _QUOTED_RE),
    ("<hex>", _HEX16_RE),
    ("<n>", _NUM_RE),
    ("<domain>", _DOMAIN_RE),
)


def normalize_line(text: str) -> str:
    """Template normalisation of a log line (§7.4); order matters."""
    out = text
    for placeholder, pattern in _TEMPLATE_STEPS:
        out = pattern.sub(placeholder, out)
    return out


def template_hash(text: str) -> str:
    """``sha1(normalized line)[:12]`` — the only thing kept from the text.

    The text is clipped to :data:`MAX_MESSAGE_CHARS` first, so the parser and
    any other caller derive the same hash for the same (bounded) message.
    """
    digest = hashlib.sha1(normalize_line(_clip(text)).encode("utf-8")).hexdigest()
    return digest[:12]


# --- signal catalogue (§7.2, ordered — first match wins) --------------------------

_LEVEL_JSON_RE = re.compile(r"\blevel[\"']?\s*[:=]\s*[\"']?(\w+)")
#: Length-bounded (see ``_EMAIL_RE``) so ``host:port`` extraction stays linear.
_HOSTPORT_RE = re.compile(
    r"\b((?:\d{1,3}\.){3}\d{1,3}|"
    r"(?:[A-Za-z0-9-]{1,63}\.){1,6}[A-Za-z]{2,24}"
    r"|localhost):(\d{1,5})\b"
)

#: Ordered app-log families.  More specific patterns must come first
#: (e.g. ``conn_reset`` before a generic ``conn_closed``-ish wording).
_SIGNAL_FAMILIES: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    # systemd English text (stable C-locale wording, not localized)
    ("unit_restart_scheduled", re.compile(
        r"\bScheduled restart job\b", re.I)),
    # app boot (SPEC §8.3: both daemons print it regardless of loglevel).
    # Deliberately narrow: a false banner would let G-01 fire as a real
    # `tunnel_down`, so only the startup lines the SPEC names are accepted.
    ("app_banner_ok", re.compile(
        r"(?:\bXray\b[^\n]*\bstarted\b|"
        r"\bHysteria2\b[^\n]*\bclient mode\b|"
        r"\bhy2 client mode\b|"
        r"\bserver listening\b)", re.I)),
    # QUIC (Hysteria2) — before generic timeouts/resets
    ("quic_handshake_ok", re.compile(
        r"\b(?:quic handshake (?:ok|complete|established)|"
        r"connected to server|handshake complete)\b", re.I)),
    ("quic_handshake_fail", re.compile(
        r"\b(?:quic handshake (?:fail|error|failed)|"
        r"handshake (?:error|failed) with server)\b", re.I)),
    ("quic_no_recent_activity", re.compile(
        r"\bno recent network activity\b", re.I)),
    ("quic_idle_timeout", re.compile(
        r"\b(?:idle timeout|timeout: no recent network activity|"
        r"QUIC(?:-go)?[^\n]*idle)\b", re.I)),
    # auth — before resets (wrong creds often end in resets too)
    ("auth_fail", re.compile(
        r"\b(?:auth(?:entication)? (?:fail|error|denied|failed)|"
        r"invalid (?:password|auth|user)|"
        r"(?:password|auth) (?:mismatch|incorrect)|access denied)\b", re.I)),
    ("auth_ok", re.compile(
        r"\bauth(?:entication)? (?:ok|succeed|succeeded|success)\b", re.I)),
    # TLS — before generic handshake
    ("tls_cert_error", re.compile(
        r"\b(?:certificate (?:is )?(?:error|invalid|expired|not trusted)|"
        r"x509:[^\n]*(?:unknown|expired|mismatch)|"
        r"failed to verify (?:certificate|server))\b", re.I)),
    ("tls_alert", re.compile(
        r"\btls (?:alert|bad certificate|handshake failure)\b", re.I)),
    ("tls_handshake_ok", re.compile(
        r"\btls handshake (?:ok|succeeded|complete)\b", re.I)),
    # TCP / dial
    ("address_in_use", re.compile(
        r"\baddress already in use\b", re.I)),
    ("network_unreachable", re.compile(
        r"\bnetwork is unreachable\b", re.I)),
    ("no_route", re.compile(
        r"\bno route to host\b", re.I)),
    ("conn_refused", re.compile(
        r"\bconnection refused\b", re.I)),
    ("dial_timeout", re.compile(
        r"\b(?:i/o timeout|dial timeout|dial tcp[^\n]*: i/o timeout|"
        r"context deadline exceeded|connect: timeout)\b", re.I)),
    ("tcp_connected", re.compile(
        r"\b(?:tcp connected|connection established to|"
        r"dial tcp[^\n]*: connect: success)\b", re.I)),
    ("conn_reset", re.compile(
        r"\b(?:connection reset(?: by peer)?|ECONNRESET|"
        r"read: connection reset|broken pipe)\b", re.I)),
    # graceful close before EOF: "connection closed normally" must not be
    # counted as a data-phase drop, so `conn_closed` wins when both match.
    ("conn_closed", re.compile(
        r"\b(?:connection closed (?:normally|by request)|"
        r"closed connection|disconnected from server)\b", re.I)),
    ("conn_eof", re.compile(
        r"\b(?:unexpected EOF|EOF|connection closed by peer)\b", re.I)),
    ("handshake_ok", re.compile(
        r"\bhandshake ok\b", re.I)),
    ("conn_established", re.compile(
        r"\btunnel established|proxy started\b", re.I)),
    ("dial_start", re.compile(
        r"\b(?:dialing|connecting to|dial tcp)\b", re.I)),
    # DNS
    ("dns_servfail", re.compile(
        r"\bSERVFAIL\b")),
    ("dns_nxdomain", re.compile(
        r"\b(?:NXDOMAIN|no such (?:host|name))\b", re.I)),
    ("dns_timeout", re.compile(
        r"\b(?:dns[^\n]*timeout|i/o timeout.*lookup|"
        r"no servers could be reached)\b", re.I)),
    ("dns_lookup_fail", re.compile(
        r"\b(?:lookup[^\n]*(?:no such host|failed|error)|"
        r"failed to (?:resolve|lookup)|dns (?:failure|error))\b", re.I)),
    # perf
    ("read_timeout", re.compile(
        r"\bread (?:timeout|deadline)\b", re.I)),
    ("write_timeout", re.compile(
        r"\bwrite (?:timeout|deadline)\b", re.I)),
    ("slow_consumer", re.compile(
        r"\bslow (?:consumer|reader|connection)\b", re.I)),
)

_SIGNAL_SET = set(SIGNALS)


def _check_families() -> None:
    """Fail fast when a family names a signal outside the frozen catalogue."""
    unknown = sorted({signal for signal, _ in _SIGNAL_FAMILIES}
                     - _SIGNAL_SET)
    if unknown:
        raise RuntimeError(
            f"parse families reference signals missing from the catalogue: {unknown}")


_check_families()

_LEVEL_BY_PRIORITY: Dict[int, str] = {
    0: "error", 1: "error", 2: "error", 3: "error",
    4: "warn", 5: "info", 6: "info", 7: "debug",
}

_LEVEL_ALIASES: Dict[str, str] = {
    "warning": "warn", "err": "error", "critical": "error",
    "fatal": "error", "notice": "info", "verbose": "debug",
    "trace": "debug",
}

_JOB_SIGNALS: Dict[Tuple[str, str], str] = {
    ("start", "done"): "unit_start",
    ("stop", "done"): "unit_stop",
    ("start", "failed"): "job_failed",
    ("stop", "failed"): "job_failed",
    ("start", "canceled"): "job_failed",
    ("stop", "canceled"): "job_failed",
}


@dataclass
class ParseStats:
    """Counters the reader/coverage layer reports (never crashes the parser)."""

    lines: int = 0
    app_lines: int = 0
    parsed: int = 0
    unparsed: int = 0
    #: Infra shell output (awg-quick, dns/zapret noise) — deliberately kept
    #: out of ``unparsed``/``app_lines`` so AC-5 measures app logs only.
    ignored_infra: int = 0
    #: structural failures (non-dict payload, broken types) — a bug shield,
    #: each one still produces no event and no exception.
    errors: int = 0

    @property
    def unparsed_ratio(self) -> float:
        """unparsed / app lines over the window (0.0 when empty).

        AC-5 defines the denominator as *app* lines (xray/hysteria2).  Infra
        shell output (awg-quick, dnsmasq, zapret) is counted in
        :attr:`ignored_infra` and never dilutes the ratio.
        """
        return self.unparsed / self.app_lines if self.app_lines else 0.0


@dataclass
class JournalParser:
    """Parse ``journald -o json`` dicts into ``guard.event/1`` payload dicts.

    ``proto_hints`` maps a unit name (``vpnm-asv2.service``) to its transport
    proto (``xray``/``hysteria2``) when the caller knows it from profile
    config; unknown units fall back to content-based detection and finally to
    ``unknown`` (the frozen enum keeps that value precisely for this case).
    """

    proto_hints: Mapping[str, str] = field(default_factory=dict)
    stats: ParseStats = field(default_factory=ParseStats)
    #: Injectable wall clock (µs) used only when a journal entry has no
    #: ``__REALTIME_TIMESTAMP``; the daemon leaves it ``None`` (real clock),
    #: tests pin it so a corpus replay is bit-for-bit reproducible (R-2).
    now_us: Optional[int] = None

    # --- entry point -----------------------------------------------------------

    def parse(self, entry: Mapping[str, Any],
              source: str = "journal:user") -> Optional[Dict[str, Any]]:
        """Normalize one journald entry; ``None`` when the line is not ours."""
        self.stats.lines += 1
        if not isinstance(entry, Mapping):
            self.stats.errors += 1
            return None
        try:
            return self._parse_entry(entry, source)
        except (KeyError, TypeError, ValueError, AttributeError):
            # A malformed journal entry must degrade to a counter, not a crash
            # (SPEC §12 «битая JSON-строка → unparsed++, без падения»).
            self.stats.errors += 1
            return None

    # --- internals --------------------------------------------------------------

    def _parse_entry(self, entry: Mapping[str, Any],
                     source: str) -> Optional[Dict[str, Any]]:
        # S1 units arrive as `_SYSTEMD_USER_UNIT`, S2 (AWG/dnsmasq/zapret) as
        # `_SYSTEMD_UNIT`; `UNIT` is the legacy/no-prefix spelling (§6.1/§6.2).
        unit = _first_str(entry, "_SYSTEMD_USER_UNIT", "_SYSTEMD_UNIT", "UNIT")
        if not unit or _is_self(entry, unit):
            return None
        proto = self._proto_for(entry, unit)
        if proto is None:
            return None  # not a vpnm-*/AWG unit — foreign noise (RSK-4)

        message = _message_text(entry)
        ts, mono_us, clock_fallback = _timestamps(entry, self.now_us)
        invocation = _first_str(entry, "_SYSTEMD_INVOCATION_ID", "INVOCATION_ID")
        level = _level_of(entry, message)

        event = self._structured_event(
            entry, source, unit, proto, ts, mono_us, clock_fallback,
            invocation, level,
        )
        if event is not None:
            return event

        if message is None or not message.strip():
            return None  # nothing recognizable and nothing to hash

        if proto == "amneziawg" and unit.startswith("awg-quick@"):
            # awg-quick emits shell output, not an app log.  Two lifecycle
            # lines become events; everything else is infra noise (AC-5:
            # it must not dilute the app-only unparsed denominator).
            signal = None
            if AWG_QUICK_ADD_RE.search(message):
                signal = "unit_start"
            elif AWG_QUICK_DEL_RE.search(message):
                signal = "unit_stop"
            if signal is None:
                self.stats.ignored_infra += 1
                return None
            self.stats.parsed += 1
            return _build_event(
                source=source, unit=unit, proto=proto, level=level,
                signal=signal, message=message, ts=ts, mono_us=mono_us,
                clock_fallback=clock_fallback, invocation=invocation,
                fields={}, endpoint=None,
            )

        signal, fields, endpoint = self._app_signal(message)
        if signal in LOCAL_NET_SIGNALS and _targets_ipv6(message):
            # IPv4-only хост: ENETUNREACH на IPv6-адресат — это отсутствие
            # v6-маршрута, а не падение локальной сети. Иначе dual-stack
            # процессы (cloudflared по [2606:4700::…], xray) дают G-07 high
            # каждые ~5 с и своим батчем маскируют реальный G-06 (§8.4).
            self.stats.ignored_infra += 1
            return None
        if signal is None:
            if proto in APP_PROTOS:
                # SPEC §7.2 step 3: unknown app text is counted, hashed and
                # never a finding — except as the G-12 unparsed spike.
                self.stats.unparsed += 1
            else:
                # Infra line (S2/dns/awg-split): visible as an event, but
                # never in the AC-5 app denominator.
                self.stats.ignored_infra += 1
            signal = "unparsed"
            fields = {}
            endpoint = None
        else:
            self.stats.parsed += 1
        if proto in APP_PROTOS:
            self.stats.app_lines += 1

        return _build_event(
            source=source, unit=unit, proto=proto, level=level,
            signal=signal, message=message, ts=ts, mono_us=mono_us,
            clock_fallback=clock_fallback, invocation=invocation,
            fields=fields, endpoint=endpoint,
        )

    def _structured_event(
        self, entry: Mapping[str, Any], source: str, unit: str,
        proto: str, ts: str, mono_us: int, clock_fallback: bool,
        invocation: Optional[str], level: str,
    ) -> Optional[Dict[str, Any]]:
        """systemd structured fields → lifecycle signal (locale-free)."""
        job_type = _first_str(entry, "JOB_TYPE")
        job_result = _first_str(entry, "JOB_RESULT")
        exit_code = _first_str(entry, "EXIT_CODE")
        exit_status = _first_str(entry, "EXIT_STATUS")

        signal: Optional[str] = None
        fields: Dict[str, Any] = {}
        if job_type and job_result:
            key = (job_type.lower(), job_result.lower())
            signal = _JOB_SIGNALS.get(key)
            if job_result.lower() != "done":
                fields["job_result"] = job_result.lower()
        if signal is None and exit_code is not None:
            signal = "unit_exit"
            status: int
            if exit_status is not None:
                try:
                    status = int(exit_status)
                except ValueError:
                    status = -1
                fields["exit_status"] = status
                if status != 0:
                    fields["exit_abnormal"] = True
        if signal is None:
            return None

        message = _message_text(entry) or ""
        self.stats.parsed += 1
        lifecycle_proto = proto if proto == "amneziawg" else "systemd"
        return _build_event(
            source=source, unit=unit, proto=lifecycle_proto, level=level,
            signal=signal, message=message or f"systemd {signal}",
            ts=ts, mono_us=mono_us, clock_fallback=clock_fallback,
            invocation=invocation, fields=fields, endpoint=None,
        )

    def _app_signal(self, message: str,
                    ) -> Tuple[Optional[str], Dict[str, Any], Optional[Dict[str, Any]]]:
        """Ordered app-regex catalogue, first match wins (§7.2 step 2)."""
        text = self._structured_payload_text(message)
        if text is not None:
            message = text
        for signal, pattern in _SIGNAL_FAMILIES:
            if pattern.search(message) is None:
                continue
            return signal, {}, _endpoint_of(message)
        return None, {}, None

    def _structured_payload_text(self, message: str) -> Optional[str]:
        """JSON app line (Xray/sing-box): return its ``msg``/``message``."""
        stripped = message.strip()
        if not stripped.startswith("{"):
            return None
        try:
            payload = json.loads(stripped)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        for key in ("msg", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    # --- classification ----------------------------------------------------------

    def _proto_for(self, entry: Mapping[str, Any], unit: str) -> Optional[str]:
        """proto by unit/identifier/source (§7.1); ``None`` = skip the line."""
        if unit.startswith(VPNM_PREFIX):
            hint = self.proto_hints.get(unit)
            if hint in ("xray", "hysteria2"):
                return hint
            return "unknown"
        if unit.startswith(AWG_UNIT_RES):
            return "amneziawg"
        # S2 units sometimes arrive without SYSLOG_IDENTIFIER
        # (``--output-fields`` fallback on old systemd), so the unit name is a
        # second, equally structured source.
        lowered = unit.lower()
        if lowered.startswith(("dnsmasq", "systemd-resolved",
                               "cloudflared-proxy-dns")):
            return "dns"
        if lowered.startswith("zapret"):
            return "zapret"
        identifier = _first_str(entry, "SYSLOG_IDENTIFIER", "_COMM")
        if identifier:
            ident = identifier.lower()
            if ident in ("dnsmasq", "systemd-resolved", "cloudflared-proxy-dns"):
                return "dns"
            if ident == "zapret":
                return "zapret"
            if ident.startswith("awg"):
                return "amneziawg"
        return None


# --- helpers ----------------------------------------------------------------------


def _first_str(entry: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _message_text(entry: Mapping[str, Any]) -> Optional[str]:
    value = entry.get("MESSAGE")
    if isinstance(value, str):
        return _clip(value)
    if isinstance(value, list):  # journald renders arrays-of-bytes sometimes
        try:
            return _clip(bytes(value).decode("utf-8", errors="replace"))
        except (TypeError, ValueError):
            return None
    return None


def _clip(text: str) -> str:
    """Bound a message: one huge line must not stall the analyzer thread."""
    return text[:MAX_MESSAGE_CHARS] if len(text) > MAX_MESSAGE_CHARS else text


def _is_self(entry: Mapping[str, Any], unit: str) -> bool:
    """No self-feeding: the guard's own unit/identifier is never analysed."""
    if unit == GUARD_UNIT:
        return True
    return (_first_str(entry, "SYSLOG_IDENTIFIER", "_COMM") or "") == GUARD_IDENTIFIER


def _timestamps(entry: Mapping[str, Any],
                now_us: Optional[int] = None) -> Tuple[str, int, bool]:
    """RFC3339-ms UTC ``ts`` + monotonic ``mono_us`` with fallback flag."""
    realtime = entry.get("__REALTIME_TIMESTAMP")
    mono = entry.get("__MONOTONIC_TIMESTAMP")
    rt_us = _to_int(realtime)
    mono_us = _to_int(mono)
    clock_fallback = mono_us is None
    if mono_us is None:
        mono_us = rt_us if rt_us is not None else 0
    if rt_us is None:
        rt_us = now_us if now_us is not None else _now_us()
    dt = datetime.fromtimestamp(rt_us / 1_000_000, tz=timezone.utc)
    ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return ts, mono_us, clock_fallback


def _now_us() -> int:
    now = datetime.now(timezone.utc)
    return int(now.timestamp() * 1_000_000)


def _to_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _level_of(entry: Mapping[str, Any], message: Optional[str] = None) -> str:
    """Structured-first level: JSON app payload → journald PRIORITY → text."""
    if message:
        payload_level = _json_level(message)
        if payload_level is not None:
            return payload_level
    priority = _to_int(entry.get("PRIORITY"))
    if priority is not None and priority in _LEVEL_BY_PRIORITY:
        return _LEVEL_BY_PRIORITY[priority]
    if message:
        match = _LEVEL_JSON_RE.search(message)
        if match:
            raw = _LEVEL_ALIASES.get(match.group(1).lower(), match.group(1).lower())
            if raw in LEVELS:
                return raw
    return "info"


def _json_level(message: str) -> Optional[str]:
    """``level`` of a JSON app line (Xray/sing-box), normalised or ``None``."""
    stripped = message.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("level")
    if not isinstance(raw, str):
        return None
    normalised = _LEVEL_ALIASES.get(raw.lower(), raw.lower())
    return normalised if normalised in LEVELS else None


def _endpoint_of(message: str) -> Optional[Dict[str, Any]]:
    """host:port allowlist extraction (only well-formed pairs, §7.1)."""
    for match in _HOSTPORT_RE.finditer(message):
        host = match.group(1)
        port = int(match.group(2))
        if not 1 <= port <= 65535:
            continue
        if _is_local_host(host):
            # Our own listener (socks5://127.0.0.1:1080) is not a server
            # endpoint: keeping it would poison endpoint-keyed rules (G-09).
            continue
        # Endpoint hosts are hashed/masked downstream (redact.py); the parser
        # only extracts the well-formed pair into the allowlist shape.
        return {"host": host, "port": port}
    return None


_V6_CANDIDATE_RE = re.compile(
    r"\[[0-9A-Fa-f:.]+\]|(?<![0-9A-Za-z.])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.]*")


def _targets_ipv6(message: str) -> bool:
    """В сообщении есть IPv6-адресат (2620:…, [2606:4700::1111]:443, …).

    Нужно для «network is unreachable»: на IPv4-only хосте (домашние сети РФ,
    AWG/Xray-сплит без v6-маршрута) ядро отвечает ENETUNREACH именно на
    IPv6-попытку dual-stack-процесса, и это **не** падение локальной сети.
    """
    for token in _V6_CANDIDATE_RE.findall(message):
        # Хвостовая пунктуация/двоеточие порта не должна мешать разбору:
        # «2606:4700::1001:443:» → валидный v6 (порт просто станет группой).
        candidate = token.strip("[]").split("%", 1)[0].rstrip(":.")
        try:
            if ipaddress.ip_address(candidate).version == 6:
                return True
        except ValueError:
            continue
    return False


def _is_local_host(host: str) -> bool:
    lowered = host.lower()
    if lowered == "localhost":
        return True
    if lowered in ("::1", "::", "[::1]", "[::]"):
        return True
    parts = lowered.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        first = int(parts[0])
        return first == 127 or lowered == "0.0.0.0"
    return False


def _build_event(*, source: str, unit: str, proto: str, level: str,
                 signal: str, message: str, ts: str, mono_us: int,
                 clock_fallback: bool, invocation: Optional[str],
                 fields: Dict[str, Any],
                 endpoint: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    profile = _profile_of(unit)
    event: Dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "ts": ts,
        "mono_us": mono_us,
        "source": source,
        "unit": unit,
        "profile": profile,
        "proto": proto,
        "level": level,
        "signal": signal,
        "template": template_hash(message),
        "count": 1,
        "clock_fallback": clock_fallback,
        "invocation_id": invocation,
    }
    if fields:
        event["fields"] = {k: v for k, v in fields.items() if _scalar(v)}
    if endpoint is not None:
        event["endpoint"] = endpoint
    return event


def _scalar(value: Any) -> bool:
    """Event ``fields`` allow only short scalars — never free text (§7.1)."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and len(value) <= 32


def _profile_of(unit: str) -> Optional[str]:
    """``vpnm-<name>.service`` → ``<name>``; AWG instance → its iface name."""
    for prefix in (VPNM_PREFIX,) + AWG_UNIT_RES:
        if unit.startswith(prefix):
            instance = unit[len(prefix):]
            return instance[:-len(".service")] if instance.endswith(".service") else instance
    return None


__all__ = [
    "AWG_UNIT_RES",
    "GUARD_IDENTIFIER",
    "GUARD_UNIT",
    "JournalParser",
    "MAX_MESSAGE_CHARS",
    "ParseStats",
    "VPNM_PREFIX",
    "normalize_line",
    "template_hash",
]
