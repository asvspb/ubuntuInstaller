"""``vpn guard`` — client-side real-time log analysis (SPEC-20261116-vpn-guard).

v1 is **read-only**: the daemon parses journals/state, writes its own bounded
artefacts and never restarts, switches, repairs or calls ``sudo``.

WP-G0 scope of this package (schemas + frozen contract):

- :mod:`vpnm.guard.events` — enum registry (signals, rules, severities, …);
- :mod:`vpnm.guard.schema` — schema loading, canonical JSON, ``schema_hash``;
- ``vpnm/guard/schemas/*.schema.json`` — canonical ``guard.*/1`` contracts.

The runtime modules (``reader``/``parse``/``rules``/…) arrive in WP-G1+.
"""

from __future__ import annotations

from .events import (  # noqa: F401
    CATEGORIES,
    CONFIDENCES,
    EVENT_SCHEMA,
    FINDING_SCHEMA,
    RULES,
    SEVERITIES,
    SIGNALS,
    canonical_json,
    payload_hash,
)

__all__ = [
    "CATEGORIES",
    "CONFIDENCES",
    "EVENT_SCHEMA",
    "FINDING_SCHEMA",
    "RULES",
    "SEVERITIES",
    "SIGNALS",
    "canonical_json",
    "payload_hash",
]
