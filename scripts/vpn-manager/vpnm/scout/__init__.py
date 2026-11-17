"""``vpn scout`` — active route reconnaissance for the client (WP-S1…WP-S4).

The guard daemon (``vpn-guard.service``) is read-only and **never opens a
socket**; it only reads what is already on disk.  Active reconnaissance is a
separate, explicitly user-invoked CLI surface:

- :mod:`vpnm.scout.models` — :class:`Candidate` (one reachable route) and
  :class:`ProbeResult` (what one probe observed);
- :mod:`vpnm.scout.context` — candidates built from the **real** profile
  store, the fresh ``server-verdict.json`` and recent findings;
- :mod:`vpnm.scout.probes` — bounded, read-only probes (TCP/TLS handshake,
  QUIC/UDP reachability, AWG UDP reachability);
- :mod:`vpnm.scout.rank` — the scoring matrix (RECOMMENDED / FALLBACK /
  rejected with reasons);
- :mod:`vpnm.scout.cli` — ``vpn scout [--json] [--sync]`` and the
  ``vpn up --scout`` planner.

Scout never writes ``findings.jsonl``, ``guard.json`` or
``server-verdict.json``; the only artefact it may touch is
``server-verdict.json`` and only through the existing explicit
``vpn guard sync`` call under ``--sync``.
"""

from __future__ import annotations

from .models import Candidate, ProbeResult

__all__ = ["Candidate", "ProbeResult"]
