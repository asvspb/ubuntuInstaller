"""WP-G2 tests: client redaction (IP HMAC, secrets, export hosts) + scanner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm.guard import events  # noqa: E402
from vpnm.guard import redact  # noqa: E402

FIXTURE_UUID = "11111111-2222-3333-4444-555555555555"  # reserved (AGENTS §6)
FIXTURE_KEY = b"FIXTURE_PW_guard-hmac-key"
FIXTURE_HASH = "sha256:" + "0" * 64


# --- IP pseudonymisation ---------------------------------------------------------------


def test_hash_ip_is_deterministic_and_prefixed() -> None:
    first = redact.hash_ip("203.0.113.10", FIXTURE_KEY)
    second = redact.hash_ip("203.0.113.10", FIXTURE_KEY)
    other = redact.hash_ip("203.0.113.11", FIXTURE_KEY)
    assert first == second
    assert first != other
    assert first.startswith("ip:")
    assert len(first) == len("ip:") + 12
    int(first[3:], 16)  # 12 hex chars


def test_hash_ip_changes_with_key() -> None:
    assert (redact.hash_ip("203.0.113.10", b"FIXTURE_PW_a")
            != redact.hash_ip("203.0.113.10", b"FIXTURE_PW_b"))


def test_redact_ips_replaces_hosts_in_text() -> None:
    text = "dial tcp 203.0.113.10:443 failed, retry via 203.0.113.20:8443"
    out, hits = redact.redact_ips(text, FIXTURE_KEY)
    assert hits == 2
    assert "203.0.113" not in out
    assert redact.hash_ip("203.0.113.10", FIXTURE_KEY) in out


def test_load_hmac_key_missing_returns_none(tmp_path: Path) -> None:
    assert redact.load_hmac_key(tmp_path / "absent.key") is None


def test_load_hmac_key_reads_stripped_value(tmp_path: Path) -> None:
    key_file = tmp_path / "hmac.key"
    key_file.write_text("  FIXTURE_PW_key-material  \n", encoding="utf-8")
    assert redact.load_hmac_key(key_file) == b"FIXTURE_PW_key-material"


def test_load_hmac_key_empty_file_returns_none(tmp_path: Path) -> None:
    key_file = tmp_path / "hmac.key"
    key_file.write_text("   \n", encoding="utf-8")
    assert redact.load_hmac_key(key_file) is None


# --- free-text redaction -----------------------------------------------------------------


def test_share_links_are_cut() -> None:
    text = f"config uses vless://{FIXTURE_UUID}@203.0.113.10:443?pbk=x#frag"
    out, hits = redact.redact_text(text)
    assert hits >= 1
    assert "vless://" not in out
    assert FIXTURE_UUID not in out
    assert redact.LINK_PLACEHOLDER in out


@pytest.mark.parametrize("scheme", ["vless", "hysteria2", "hy2", "ss", "trojan", "awg"])
def test_all_transport_schemes_are_cut(scheme: str) -> None:
    out, hits = redact.redact_text(f"{scheme}://user@example.net:443?s=1")
    assert hits >= 1
    assert f"{scheme}://" not in out


def test_uuid_is_cut_alone() -> None:
    out, hits = redact.redact_text(f"client id {FIXTURE_UUID} logged in")
    assert hits == 1
    assert FIXTURE_UUID not in out
    assert redact.UUID_PLACEHOLDER in out


@pytest.mark.parametrize("marker", [
    "password", "privateKey", "publicKey", "shortId", "obfs", "obfs-password",
    "auth", "psk", "preSharedKey",
])
def test_context_secret_markers_are_redacted(marker: str) -> None:
    text = f"{marker}=FIXTURE_PW_super-secret-value rest of line"
    out, hits = redact.redact_text(text)
    assert hits >= 1, marker
    assert "FIXTURE_PW_super-secret-value" not in out, marker


def test_base64_and_hex_blobs_only_in_raw_mode() -> None:
    secret_b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWQ=="  # base64 of ABCDEFGHI…
    text = f"payload {secret_b64} and key material"
    soft, soft_hits = redact.redact_text(text)
    raw, raw_hits = redact.redact_raw(text)
    assert secret_b64 in soft  # soft mode keeps blobs (JSON hashes survive)
    assert secret_b64 not in raw
    assert raw_hits > soft_hits
    assert redact.BLOB in raw


def test_url_with_userinfo_and_token_is_masked() -> None:
    out, hits = redact.redact_text(
        "fetch https://user:FIXTURE_PW_pw@example.net/path?token=FIXTURE_PW_t")
    assert "FIXTURE_PW_pw" not in out
    assert "FIXTURE_PW_t" not in out
    assert hits >= 1


def test_email_is_masked() -> None:
    out, hits = redact.redact_text("contact owner fixture@example.net please")
    assert "fixture@example.net" not in out
    assert redact.EMAIL_PLACEHOLDER in out


# --- export mode --------------------------------------------------------------------------


def test_export_mode_replaces_hosts() -> None:
    text = "endpoint asv2.example.net:443 and 203.0.113.10 unreachable"
    out, hits = redact.redact_hosts(text)
    assert hits == 2
    assert "asv2.example.net" not in out
    assert "203.0.113.10" not in out
    assert text.count(redact.HOST_PLACEHOLDER) == 0
    assert out.count(redact.HOST_PLACEHOLDER) == 2


# --- post-scanner --------------------------------------------------------------------------


def clean_finding() -> dict:
    return {
        "schema": "guard.finding/1",
        "id": "7c1d2f0a9b3e",
        "rule": "G-03",
        "severity": "high",
        "confidence": "high",
        "category": "censorship_suspect",
        "stage_hint": "data_phase",
        "signal": "conn_reset",
        "unit": "vpnm-asv2.service",
        "endpoint": {"host": redact.hash_ip("203.0.113.10", FIXTURE_KEY),
                     "port": 443},
        "count": 4,
        "template": "3f9a1c02be77",
        "incident_id": "i-2f81c0",
        "evidence_hash": FIXTURE_HASH,
        "schema_hash": FIXTURE_HASH,
        "rules_hash": FIXTURE_HASH,
        "config_hash": FIXTURE_HASH,
        "raw_sha256": "0" * 64,
        "hypothesis": "data-phase impact on the path",
        "action": "do not restart; run vpn probe",
    }


def test_clean_finding_has_zero_hits() -> None:
    assert redact.scan_secrets(clean_finding()) == []


def test_contract_hash_fields_survive_scanner() -> None:
    finding = clean_finding()
    finding["some_new_field_hash"] = "deadbeef" * 8  # bare 64-hex in *_hash key
    finding["template"] = "a" * 12  # placeholder length, still exempt by key
    assert redact.scan_secrets(finding) == []


def test_planted_uuid_is_detected_with_field_path() -> None:
    finding = clean_finding()
    finding["hypothesis"] = f"client {FIXTURE_UUID} was reset"
    hits = redact.scan_secrets(finding)
    assert hits
    assert any(h.startswith("$.hypothesis:uuid") for h in hits)
    for hit in hits:
        assert FIXTURE_UUID not in hit  # paths only, never values


def test_planted_link_and_password_are_detected() -> None:
    finding = clean_finding()
    finding["evidence"] = [
        {"kind": "signal_count", "signal": "conn_reset", "value": 4,
         "snippet": f"vless://{FIXTURE_UUID}@203.0.113.10:443"},
    ]
    finding["action"] = "check password=FIXTURE_PW_leaked in config"
    hits = redact.scan_secrets(finding)
    assert any(":share_link" in h for h in hits)
    assert any(":secret_marker" in h for h in hits)


def test_masked_placeholders_do_not_trigger_scanner() -> None:
    finding = clean_finding()
    finding["hypothesis"] = (
        f"client {redact.UUID_PLACEHOLDER} from {redact.HOST_PLACEHOLDER}:443 "
        f"with {redact.REDACTED}")
    assert redact.scan_secrets(finding) == []


def test_scan_export_aggregates_findings() -> None:
    dirty = clean_finding()
    dirty["alternatives"] = [f"leaked {FIXTURE_UUID}"]
    coverage = {
        "schema": events.COVERAGE_SCHEMA,
        "note": "fine",
    }
    hits = redact.scan_export([clean_finding(), dirty], coverage)
    assert hits
    assert any(h.startswith("$findings[1]") for h in hits)
    assert "$coverage" not in hits


def test_redacted_text_is_idempotent() -> None:
    text = f"vless://{FIXTURE_UUID}@203.0.113.10:443 password=FIXTURE_PW_x"
    once, _ = redact.redact_text(text)
    twice, hits = redact.redact_text(once)
    assert once == twice
    assert hits == 0


# --- scanner strictness (AC-13 allowlist vs a real leak) -------------------------------


def test_bare_hex_is_legal_only_under_a_contract_key() -> None:
    """A bare 64-hex is a hash *in an allowlist key*, a blob anywhere else."""
    legal = clean_finding()
    legal["raw_sha256"] = "b" * 64          # exempt key (suffix `_sha256`)
    legal["evidence_hash"] = "c" * 64       # exempt key (suffix `_hash`)
    assert redact.scan_secrets(legal) == []

    leaked = clean_finding()
    leaked["hypothesis"] = "key material " + "d" * 64
    hits = redact.scan_secrets(leaked)
    assert any(h.startswith("$.hypothesis:") for h in hits)


def test_prefixed_sha256_is_legal_anywhere() -> None:
    """``sha256:<64 hex>`` is the canonical contract shape, not a secret."""
    finding = clean_finding()
    finding["action"] = "compare " + FIXTURE_HASH
    assert redact.scan_secrets(finding) == []


def test_coverage_note_is_scanned() -> None:
    coverage = {"schema": events.COVERAGE_SCHEMA,
                "notes": ["repair used password=FIXTURE_PW_leak"]}
    assert any(h.startswith("$.notes[0]") for h in redact.scan_secrets(coverage))


# --- write-path sanitizer (§7.3) ---------------------------------------------------------


def test_clean_finding_survives_sanitize_untouched() -> None:
    finding = clean_finding()
    clean, hits = redact.sanitize_finding(finding)
    assert hits == 0
    assert clean["redaction_hits"] == 0
    assert redact.scan_secrets(clean) == []
    assert clean["hypothesis"] == finding["hypothesis"]
    assert finding.get("redaction_hits", 0) == 0  # input is not mutated


def test_snippets_are_dropped_by_default() -> None:
    """§11.1: snippets are off by default — dropping them is not a leak."""
    finding = clean_finding()
    finding["evidence"] = [{"kind": "signal_count", "signal": "conn_reset",
                            "value": 4, "snippet": "connection reset by peer"}]
    clean, hits = redact.sanitize_finding(finding)
    assert hits == 0
    assert "snippet" not in clean["evidence"][0]
    assert clean["redaction_hits"] == 0


def test_secret_in_snippet_is_dropped_and_counted() -> None:
    finding = clean_finding()
    finding["evidence"] = [{"kind": "signal_count", "signal": "conn_reset",
                            "value": 4,
                            "snippet": "password=FIXTURE_PW_leak"}]
    clean, hits = redact.sanitize_finding(finding, snippets=True)
    snippet = clean["evidence"][0].get("snippet", "")
    assert "FIXTURE_PW_leak" not in snippet
    assert hits >= 1
    assert clean["redaction_hits"] >= 1
    assert redact.scan_secrets(clean) == []


def test_prose_secret_is_masked_on_write() -> None:
    finding = clean_finding()
    finding["hypothesis"] = f"client {FIXTURE_UUID} was reset"
    finding["action"] = "reuse vless://" + FIXTURE_UUID + "@203.0.113.10:443"
    clean, hits = redact.sanitize_finding(finding)
    assert hits >= 2
    assert FIXTURE_UUID not in clean["hypothesis"]
    assert FIXTURE_UUID not in clean["action"]
    assert "<uuid>" in clean["hypothesis"]
    assert clean["redaction_hits"] == hits
    assert redact.scan_secrets(clean) == []


def test_scanner_survivor_is_masked_not_written() -> None:
    """A value the masker keeps but the scanner flags is replaced, not leaked."""
    finding = clean_finding()
    finding["fp_checks"] = ["noise QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWQ==", "ok"]
    assert redact.scan_secrets(finding)  # detected before sanitizing
    clean, hits = redact.sanitize_finding(finding)
    assert hits == 1
    assert clean["fp_checks"] == [redact.REDACTED, "ok"]
    assert redact.scan_secrets(clean) == []


def test_sanitize_is_idempotent_and_accumulates_hits() -> None:
    finding = clean_finding()
    finding["hypothesis"] = f"client {FIXTURE_UUID} reset"
    finding["redaction_hits"] = 5
    once, first_hits = redact.sanitize_finding(finding)
    twice, second_hits = redact.sanitize_finding(once)
    assert first_hits >= 1
    assert second_hits == 0
    assert once == twice
    assert once["redaction_hits"] == 5 + first_hits


def test_state_and_coverage_notes_are_sanitized() -> None:
    coverage = {
        "schema": events.COVERAGE_SCHEMA,
        "note": "resolver said password=FIXTURE_PW_x",
        "notes": ["fine", f"link vless://{FIXTURE_UUID}@203.0.113.10:443"],
    }
    clean, hits = redact.sanitize_payload(coverage)
    assert hits >= 2
    assert "FIXTURE_PW_x" not in clean["note"]
    assert FIXTURE_UUID not in clean["notes"][1]
    assert redact.scan_secrets(clean) == []


def test_sanitize_rejects_non_mapping() -> None:
    with pytest.raises(TypeError):
        redact.sanitize_payload([1, 2, 3])  # type: ignore[arg-type]


# --- export path: hosts and the blocking gate (AC-13) -------------------------------------


def test_export_view_masks_endpoint_host() -> None:
    view, hits = redact.export_view(clean_finding())
    assert view["endpoint"]["host"] == redact.HOST_PLACEHOLDER
    assert view["endpoint"]["port"] == 443
    assert hits >= 1
    assert "asv2" not in view["endpoint"]["host"]


def test_export_view_keep_hosts_is_explicit() -> None:
    finding = clean_finding()
    finding["endpoint"] = {"host": "asv2.example.net", "port": 443}
    view, hits = redact.export_view(finding, keep_hosts=True)
    assert view["endpoint"]["host"] == "asv2.example.net"
    assert hits == 0


def test_export_view_masks_hosts_in_prose() -> None:
    finding = clean_finding()
    finding["action"] = "check asv2.example.net then 203.0.113.10"
    view, _ = redact.export_view(finding)
    assert "asv2.example.net" not in view["action"]
    assert "203.0.113.10" not in view["action"]


def test_export_gate_passes_a_reference_finding() -> None:
    """AC-13: schema_hash/rules_hash/config_hash/raw_sha256 are not secrets."""
    code, hits = redact.export_gate([clean_finding()],
                                    {"schema": events.COVERAGE_SCHEMA,
                                     "note": "ok"})
    assert (code, hits) == (0, [])


def test_export_gate_blocks_with_code_2_on_a_real_secret() -> None:
    """AC-13: a real secret in a snippet blocks the bundle (exit code 2)."""
    dirty = clean_finding()
    dirty["evidence"] = [{"kind": "signal_count", "signal": "conn_reset",
                          "value": 4,
                          "snippet": "password=FIXTURE_PW_leak"}]
    code, hits = redact.export_gate([clean_finding(), dirty])
    assert code == 2
    assert any(h.startswith("$.evidence[0].snippet") for h in hits)
    assert any(h.startswith("$findings[1]") for h in hits)
    assert all("FIXTURE_PW_leak" not in hit for hit in hits)


# --- AC-6: the whole pipeline leaves no secret in the artefact ------------------------------


def _entry(message: str) -> dict:
    return {
        "MESSAGE": message,
        "PRIORITY": "6",
        "__REALTIME_TIMESTAMP": "1794830403412345",
        "__MONOTONIC_TIMESTAMP": "812345678",
        "_SYSTEMD_USER_UNIT": "vpnm-asv2.service",
        "_SYSTEMD_INVOCATION_ID": "fixture-invocation",
    }


SECRET_CORPUS = [
    f"failed to dial to 203.0.113.10:443: dial tcp: connection refused "
    f"uuid={FIXTURE_UUID}",
    "auth error: invalid password FIXTURE_PW_leak",
    f"config loaded from vless://{FIXTURE_UUID}@203.0.113.11:443?pbk=x",
    "timeout: no recent network activity",
    '{"level":"error","msg":"connection reset by peer",'
    '"user":"fixture@example.net"}',
]


def test_ac6_pipeline_leaks_no_secret_into_the_finding() -> None:
    """Parser → finding assembly → sanitize → scanner: zero hits (AC-6)."""
    from vpnm.guard.parse import JournalParser

    parser = JournalParser(proto_hints={"vpnm-asv2.service": "hysteria2"})
    parsed = []
    for message in SECRET_CORPUS:
        event = parser.parse(_entry(message))
        assert event is not None
        dumped = json.dumps(event, ensure_ascii=False)
        for secret in (FIXTURE_UUID, "FIXTURE_PW_leak", "fixture@example.net",
                       "vless://"):
            assert secret not in dumped, f"{secret} leaked into the event"
        parsed.append(event)

    # What WP-G3 will assemble: counters, templates, no text.
    finding = clean_finding()
    finding["endpoint"] = {"host": redact.hash_ip("203.0.113.10", FIXTURE_KEY),
                           "port": 443}
    finding["evidence"] = [
        {"kind": "signal_count", "signal": event["signal"], "value": 1}
        for event in parsed
    ]
    finding["evidence"].append({"kind": "signal_count", "signal": "conn_reset",
                                "value": len(parsed)})

    clean, hits = redact.sanitize_finding(finding)
    assert hits == 0
    assert redact.scan_secrets(clean) == []
    assert redact.export_gate([redact.export_view(clean)[0]])[0] == 0
