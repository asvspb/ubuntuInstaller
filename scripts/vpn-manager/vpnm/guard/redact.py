"""Client-side redaction for everything guard persists (SPEC §7.3, R-3).

Layers, implemented independently on the stdlib (no cross-repo imports):

- :func:`redact_text` — contextual secret markers, share links, UUIDs,
  e-mails and URLs with userinfo/tokens → placeholders.  Applied to any free
  text *before* it can reach an artefact; contract values (hashes) survive it.
- :func:`redact_raw` — ``redact_text`` + greedy hex ≥ 32 / base64 ≥ 24 →
  ``<blob>``.  The mode for raw log text and other free-text fields, where a
  key material blob must never survive.
- :func:`hash_ip` — HMAC-SHA256(key, ip)[:12] with the ``ip:`` prefix;
  deterministic per key so the same client IP maps to the same pseudonym
  across findings and exports.
- :func:`sanitize_payload` / :func:`sanitize_finding` — the **write-path**
  pass: mask the free-text fields of a finding/state/coverage payload and
  count ``redaction_hits`` (SPEC §7.3: "finding пишется без сниппета,
  redaction_hits инкрементится").
- :func:`export_view` / :func:`export_gate` — the **export path**:
  hosts → ``<host>`` (``redact.hosts_in_export=true`` default) and a hard
  block (exit code 2) when the post-scanner still finds a secret.

The post-scanner :func:`scan_secrets` walks a serialized finding/bundle and
returns **field paths** (never values) for anything secret-like that
survived.  It must not fire on the contract's own hash fields: keys from
:data:`vpnm.guard.events.HASH_EXEMPT_KEYS`, ``*_hash`` / ``*_sha256`` keys
and ``sha256:<64 hex>`` values are legal (AC-13).  A *bare* 64-hex string is
legal only under such a key — anywhere else it is a possible leaked key.
"""

from __future__ import annotations

import copy
import hashlib
import hmac as hmac_mod
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .events import HASH_EXEMPT_KEYS

REDACTED = "<redacted>"
BLOB = "<blob>"
UUID_PLACEHOLDER = "<uuid>"
LINK_PLACEHOLDER = "<link>"
URL_PLACEHOLDER = "<url>"
EMAIL_PLACEHOLDER = "<email>"
HOST_PLACEHOLDER = "<host>"
TG_TOKEN_PLACEHOLDER = "<redacted:tg_token>"
IP_PREFIX = "ip:"

#: Schema limits of the free-text fields (guard.finding/1); redaction happens
#: before serialisation, so clipping here keeps a masked value inside the
#: contract instead of failing validation in the writer.
MAX_PROSE_CHARS = 240
MAX_ITEM_CHARS = 120
MAX_SNIPPET_CHARS = 120

#: Top-level keys whose values are prose the guard may persist (§7.3).
FREE_TEXT_KEYS: Tuple[str, ...] = (
    "hypothesis",
    "action",
    "alternatives",
    "note",
    "notes",
    "reason",
)
SNIPPET_KEY = "snippet"
FIELDS_KEY = "fields"

#: Default key location (created by ``vpn guard install``; absent key →
#: :func:`load_hmac_key` returns ``None`` and the caller decides — the
#: daemon degrades, it does not crash).
HMAC_KEY_FILE = Path.home() / ".local/share/vpn-manager/guard/hmac.key"

# --- patterns ---------------------------------------------------------------------
# Every unbounded ``[...]+`` run is length-bounded: one adversarial log line
# must not turn the scanner quadratic (R-6).

_LINK_RE = re.compile(
    r"\b(?:vless|hysteria2|hy2|trojan|ss|ssr|vmess|vpn|wg|awg)://[^\s\"']+",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}\b"
)
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"\.[A-Za-z]{2,24}\b"
)
_URL_USERINFO_RE = re.compile(
    r"\bhttps?://[^\s\"'/]+@[^\s\"']+",
    re.IGNORECASE,
)
_URL_TOKEN_RE = re.compile(
    r"\bhttps?://[^\s\"']*[?&](?:token|key|secret|password|access_token)="
    r"[^\s\"'&]+",
    re.IGNORECASE,
)
#: Canonical fixture prefix from AGENTS §6 / SPEC-20261116-vpn-guard AC-13.
#: Must be caught even as a bare token without a ``password=`` context.
#: ``TG_TOKEN`` is the WP-FAILOVER fixture family (SPEC-20261118 AC-F10).
_FIXTURE_RE = re.compile(
    r"\bFIXTURE_(?:PW|UUID|SECRET|TG_TOKEN)_[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
#: Telegram bot token: ``<8-10 digits>:<35 chars>`` (SPEC-20261118 §9).  It
#: must never survive into an artefact, an exception text or an export.
_TG_TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")

_VALUE = r"[^\s\"',;}\]]+"
_CONTEXT_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(rf"(?i)\bprivateKey\s*[=:]\s*{_VALUE}"),
    re.compile(rf"(?i)\bpreSharedKey\s*[=:]\s*{_VALUE}"),
    re.compile(rf"(?i)\bpublicKey\s*[=:]\s*{_VALUE}"),
    re.compile(rf"(?i)\bshortIds?\s*[=:]\s*{_VALUE}"),
    re.compile(rf"(?i)\bobfs(?:-password)?\s*[=:]\s*{_VALUE}"),
    re.compile(rf"(?i)\bpassword\s*[=:]\s*{_VALUE}"),
    re.compile(rf"(?i)\bauth\s*[=:]\s*{_VALUE}"),
    re.compile(rf"(?i)\bpsk\s*[=:]\s*{_VALUE}"),
)

_HEX32_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_B64_RE = re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")

#: IPv4 / domain / IPv6-lite — the "host" notion of §7.3 (local vs export).
_HOST_RE = re.compile(
    r"\b(?:\d{1,3}(?:\.\d{1,3}){3}|"
    r"(?:[A-Za-z0-9-]{1,63}\.){1,6}[A-Za-z]{2,24})\b"
)

_SHA256_VALUE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: Canonical hash token inside prose: a contract value, never a secret.
_SHA256_TOKEN_RE = re.compile(r"sha256:[0-9a-f]{64}")

# --- post-scanner secret patterns (what must NEVER survive) ------------------------

_SCANNER_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("share_link", _LINK_RE),
    ("uuid", _UUID_RE),
    ("secret_marker", re.compile(
        r"(?i)\b(?:privateKey|preSharedKey|publicKey|shortIds?|"
        r"obfs(?:-password)?|password|psk)\s*[=:]\s*[^\s<]+")),
    ("auth_token", re.compile(
        r"(?i)\bauth\s*[=:]\s*[^\s<]+")),
    ("url_userinfo", _URL_USERINFO_RE),
    ("url_token", _URL_TOKEN_RE),
    ("tg_token", _TG_TOKEN_RE),
    ("fixture_marker", _FIXTURE_RE),
    ("base64_blob", re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b")),
    ("hex_blob", re.compile(r"\b[0-9a-fA-F]{40,}\b")),
)


# --- text redaction ----------------------------------------------------------------


def redact_text(text: str) -> Tuple[str, int]:
    """Contextual redaction of free text; returns ``(text, hits)``.

    Order matters: share links first (they contain UUIDs/passwords), then
    URLs with userinfo/tokens, UUIDs, e-mails, then secret markers.  Blobs
    (hex/base64) are **kept** — use :func:`redact_raw` for raw log text.
    """
    hits = 0
    out, n = _LINK_RE.subn(LINK_PLACEHOLDER, text)
    hits += n
    out, n = _URL_USERINFO_RE.subn(URL_PLACEHOLDER, out)
    hits += n
    out, n = _URL_TOKEN_RE.subn(URL_PLACEHOLDER, out)
    hits += n
    out, n = _TG_TOKEN_RE.subn(TG_TOKEN_PLACEHOLDER, out)
    hits += n
    out, n = _UUID_RE.subn(UUID_PLACEHOLDER, out)
    hits += n
    out, n = _FIXTURE_RE.subn(REDACTED, out)
    hits += n
    out, n = _EMAIL_RE.subn(EMAIL_PLACEHOLDER, out)
    hits += n
    for pattern in _CONTEXT_PATTERNS:
        out, n = pattern.subn(REDACTED, out)
        hits += n
    return out, hits


def redact_raw(text: str) -> Tuple[str, int]:
    """Raw-text mode: :func:`redact_text` + greedy hex ≥ 32 / base64 ≥ 24.

    Only for raw log text and prose fields — applying it to JSON contract
    values would eat the guard's own hashes (the AC-13 lesson).
    """
    out, hits = redact_text(text)
    for pattern in (_HEX32_RE, _B64_RE):
        out, n = pattern.subn(BLOB, out)
        hits += n
    return out, hits


# --- IP hashing ---------------------------------------------------------------------


def hash_ip(ip: str, key: bytes) -> str:
    """``ip:<12 hex>`` — HMAC-SHA256 pseudonym, deterministic per key."""
    digest = hmac_mod.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{IP_PREFIX}{digest[:12]}"


def redact_ips(text: str, key: bytes) -> Tuple[str, int]:
    """Replace every IP/domain token with its deterministic pseudonym."""
    hits = 0

    def _sub(match: "re.Match[str]") -> str:
        nonlocal hits
        hits += 1
        return hash_ip(match.group(0), key)

    out = _HOST_RE.sub(_sub, text)
    return out, hits


def load_hmac_key(path: Optional[Path] = None) -> Optional[bytes]:
    """Read the guard HMAC key (0600, written by install); ``None`` if absent."""
    key_file = Path(path) if path is not None else HMAC_KEY_FILE
    try:
        raw = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return raw.encode("utf-8") if raw else None


# --- export mode ---------------------------------------------------------------------


def redact_hosts(text: str) -> Tuple[str, int]:
    """Export mode: endpoint hosts/domains/IPs → ``<host>`` (§11.6)."""
    out, hits = _HOST_RE.subn(HOST_PLACEHOLDER, text)
    return out, hits


# --- post-scanner --------------------------------------------------------------------


def _exempt_key(key: str) -> bool:
    """Contract keys whose values are hashes/enums, not secrets (§7.3)."""
    return key in HASH_EXEMPT_KEYS or key.endswith(("_hash", "_sha256"))


def _scan_value(key: str, value: Any, path: str, found: List[str]) -> None:
    """Scan one value under *key*; secret hits append the field path."""
    if isinstance(value, str):
        if _exempt_key(key):
            return
        if _SHA256_VALUE_RE.match(value):
            return  # canonical ``sha256:<64 hex>`` is a contract value
        if value.startswith((REDACTED, BLOB, UUID_PLACEHOLDER, LINK_PLACEHOLDER,
                             URL_PLACEHOLDER, EMAIL_PLACEHOLDER,
                             HOST_PLACEHOLDER, TG_TOKEN_PLACEHOLDER)):
            return  # already-masked placeholder, nothing to leak
        if re.fullmatch(r"ip:[0-9a-f]{12}", value):
            return  # deterministic IP pseudonym (hash_ip output)
        # Canonical ``sha256:<64 hex>`` tokens are contract values (§7.3/AC-13);
        # their presence must not make a legal prose field look like a leak.
        subject = _SHA256_TOKEN_RE.sub("", value)
        for name, pattern in _SCANNER_PATTERNS:
            if pattern.search(subject):
                found.append(f"{path}:{name}")
                return
    elif isinstance(value, Mapping):
        _scan_mapping(value, path, found)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_value(key, item, f"{path}[{index}]", found)


def _scan_mapping(payload: Mapping[str, Any], path: str, found: List[str]) -> None:
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        if _exempt_key(key) and not isinstance(value, (Mapping, list)):
            continue
        _scan_value(key, value, f"{path}.{key}", found)


def scan_secrets(payload: Any) -> List[str]:
    """Post-scan a finding/bundle/coverage dict for survived secrets.

    Returns a sorted list of ``path:pattern_name`` hits — **field paths,
    never values** (a leak report must not itself leak).  Empty list = clean.
    Keys of the contract (``*_hash``, ``template``, ``id``, …) and values of
    the shape ``sha256:<64 hex>`` are legal by design (AC-13).
    """
    found: List[str] = []
    if isinstance(payload, Mapping):
        _scan_mapping(payload, "$", found)
    elif isinstance(payload, str):
        _scan_value("", payload, "$", found)
    return sorted(set(found))


def scan_export(findings: Iterable[Mapping[str, Any]],
                coverage: Optional[Mapping[str, Any]] = None) -> List[str]:
    """Convenience wrapper: scan a findings iterable + coverage (export path)."""
    found: List[str] = []
    for index, finding in enumerate(findings):
        hits = scan_secrets(finding)
        if hits:
            found.extend(hits)
            found.append(f"$findings[{index}]")
    if coverage is not None and scan_secrets(coverage):
        found.append("$coverage")
    return sorted(set(found))


# --- write-path sanitizer (§7.3) ------------------------------------------------------


def _scrub_text(value: Any, limit: int, acc: List[int]) -> Any:
    """Mask one prose value and clip it to the schema limit."""
    if isinstance(value, list):
        return [_scrub_text(item, limit, acc) for item in value]
    if not isinstance(value, str):
        return value
    masked, hits = redact_raw(value)
    acc[0] += hits
    return masked[:limit]


def _scrub_snippet(value: Any, snippets: bool, acc: List[int]) -> Optional[str]:
    """Snippets are opt-in: dropped by default, masked + clipped when on."""
    if not snippets or not isinstance(value, str):
        # §11.1: snippets are off by default.  Dropping a disabled snippet is
        # policy, not a leak, so it does not count as a redaction hit.
        return None
    masked, hits = redact_raw(value)
    acc[0] += hits
    return masked[:MAX_SNIPPET_CHARS]


def _scrub_mapping(node: Mapping[str, Any], snippets: bool,
                   acc: List[int]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in node.items():
        if not isinstance(key, str):
            continue
        if key == SNIPPET_KEY:
            snippet = _scrub_snippet(value, snippets, acc)
            if snippet is not None:
                out[key] = snippet
        elif key in FREE_TEXT_KEYS:
            out[key] = _scrub_text(value, MAX_ITEM_CHARS if isinstance(value, list)
                                   else MAX_PROSE_CHARS, acc)
        elif key == FIELDS_KEY and isinstance(value, Mapping):
            fields: Dict[str, Any] = {}
            for name, item in value.items():
                if isinstance(item, str):
                    fields[name] = _scrub_text(item, MAX_ITEM_CHARS, acc)
                else:
                    fields[name] = item
            out[key] = fields
        elif isinstance(value, Mapping):
            out[key] = _scrub_mapping(value, snippets, acc)
        elif isinstance(value, (list, tuple)):
            out[key] = [_scrub_mapping(item, snippets, acc)
                        if isinstance(item, Mapping) else item
                        for item in value]
        else:
            out[key] = value
    return out


def _mask_path(payload: Any, path: str) -> None:
    """Replace the value at ``$.a.b[0]`` (scanner path) with ``<redacted>``."""
    spec = path.rsplit(":", 1)[0]
    tokens = re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", spec)
    node: Any = payload
    for index, (key, position) in enumerate(tokens):
        last = index == len(tokens) - 1
        try:
            if key:
                if not isinstance(node, dict) or key not in node:
                    return
                if last:
                    node[key] = REDACTED
                else:
                    node = node[key]
            else:
                slot = int(position)
                if not isinstance(node, list) or slot >= len(node):
                    return
                if last:
                    node[slot] = REDACTED
                else:
                    node = node[slot]
        except (KeyError, IndexError, TypeError, ValueError):
            return


def sanitize_payload(payload: Mapping[str, Any], *,
                     snippets: bool = False) -> Tuple[Dict[str, Any], int]:
    """Mask a finding/state/coverage payload before it is written.

    Two passes: prose/free-text fields are masked with :func:`redact_raw`
    (snippets dropped unless *snippets*), then :func:`scan_secrets` re-checks
    the result and any survivor is replaced by ``<redacted>``.  Returns
    ``(clean_copy, hits)``; the input mapping is not modified.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("sanitize_payload expects a mapping")
    acc = [0]
    work = _scrub_mapping(copy.deepcopy(dict(payload)), snippets, acc)
    survivors = scan_secrets(work)
    for path in survivors:
        _mask_path(work, path)
    acc[0] += len(survivors)
    return work, acc[0]


def sanitize_finding(finding: Mapping[str, Any], *,
                     snippets: bool = False) -> Tuple[Dict[str, Any], int]:
    """Write-path hook for ``guard.finding/1``: sanitize + ``redaction_hits``.

    Idempotent: a second call returns an equal payload and ``0`` new hits.
    """
    clean, hits = sanitize_payload(finding, snippets=snippets)
    previous = clean.get("redaction_hits")
    base = previous if isinstance(previous, int) and not isinstance(previous, bool) else 0
    clean["redaction_hits"] = base + hits
    return clean, hits


def export_view(finding: Mapping[str, Any], *,
                keep_hosts: bool = False) -> Tuple[Dict[str, Any], int]:
    """Export-mode copy of a finding: hosts → ``<host>`` unless opted in.

    ``redact.hosts_in_export=true`` is the default (§7.3/§11.6); the caller
    must have an explicit ``--keep-hosts`` flag to opt out.
    """
    view, hits = sanitize_finding(finding)
    if keep_hosts:
        return view, hits
    endpoint = view.get("endpoint")
    if isinstance(endpoint, Mapping) and isinstance(endpoint.get("host"), str):
        replaced = dict(endpoint)
        replaced["host"] = HOST_PLACEHOLDER
        view["endpoint"] = replaced
        hits += 1
    for key in FREE_TEXT_KEYS:
        value = view.get(key)
        if isinstance(value, str):
            view[key], n = redact_hosts(value)
            hits += n
        elif isinstance(value, list):
            items: List[Any] = []
            for item in value:
                if isinstance(item, str):
                    item, n = redact_hosts(item)
                    hits += n
                items.append(item)
            view[key] = items
    return view, hits


def export_gate(findings: Iterable[Mapping[str, Any]],
                coverage: Optional[Mapping[str, Any]] = None) -> Tuple[int, List[str]]:
    """Export gate (SPEC §10.1): ``(0, [])`` or ``(2, hit_paths)``.

    A single survived secret blocks the bundle — the CLI prints the paths
    (never the values) and exits with code 2.
    """
    hits = scan_export(findings, coverage)
    return (2 if hits else 0), hits


__all__ = [
    "BLOB",
    "EMAIL_PLACEHOLDER",
    "FIELDS_KEY",
    "FREE_TEXT_KEYS",
    "HOST_PLACEHOLDER",
    "HMAC_KEY_FILE",
    "IP_PREFIX",
    "LINK_PLACEHOLDER",
    "MAX_ITEM_CHARS",
    "MAX_PROSE_CHARS",
    "MAX_SNIPPET_CHARS",
    "REDACTED",
    "SNIPPET_KEY",
    "TG_TOKEN_PLACEHOLDER",
    "URL_PLACEHOLDER",
    "UUID_PLACEHOLDER",
    "export_gate",
    "export_view",
    "hash_ip",
    "load_hmac_key",
    "redact_hosts",
    "redact_ips",
    "redact_raw",
    "redact_text",
    "sanitize_finding",
    "sanitize_payload",
    "scan_export",
    "scan_secrets",
]
