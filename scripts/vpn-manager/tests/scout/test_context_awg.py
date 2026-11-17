"""AWG route classification: by ``Endpoint`` host, not by the file name.

Live defect being pinned down: ``android.conf`` / ``windows.conf`` pointed at
the relay host but were reported as ``direct/awg`` (a "primary" that was
physically the relay) while ``relay.conf`` produced the fallback — the same
tunnel twice.  The relay host is discovered from ``relay.conf`` (and the
canonical host), and all client copies of one endpoint collapse into a single
``relay/awg`` candidate.

Fixtures use only reserved addresses (RFC 5737) and ``FIXTURE_*`` placeholders.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vpnm import amneziawg as awg_mod  # noqa: E402
from vpnm import settings as settings_mod  # noqa: E402
from vpnm.scout import context as scout_context  # noqa: E402
from vpnm.scout.models import (  # noqa: E402
    PROTO_AWG,
    ROUTE_DIRECT,
    ROUTE_RELAY,
)

RELAY_HOST = "relay.asvpersonal.ru"
DIRECT_IP = "213.108.23.119"
RESERVED_RELAY_IP = "203.0.113.10"


def write_conf(path: Path, endpoint: str) -> None:
    """Minimal AWG client config — only the Endpoint is semantically used."""
    path.write_text(
        "[Interface]\n"
        "PrivateKey = FIXTURE_PW_awg_private\n"
        "[Peer]\n"
        f"Endpoint = {endpoint}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def awg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch
            ) -> Tuple[Path, Path]:
    user_dir = tmp_path / "awg"
    system_dir = tmp_path / "awg-system"
    user_dir.mkdir()
    system_dir.mkdir()
    monkeypatch.setattr(awg_mod, "LOCAL_CONF_DIR", user_dir)
    monkeypatch.setattr(awg_mod, "AWG_SYSTEM_CONF_DIR", str(system_dir))
    # Never read the developer's real ~/.config/vpn-manager/settings.json.
    monkeypatch.setattr(settings_mod, "SETTINGS_PATH",
                        tmp_path / "settings.json")
    return user_dir, system_dir


def routes_by_profile(candidates) -> Dict[str, str]:
    return {str(candidate.profile): candidate.route
            for candidate in candidates}


def test_awg_endpoint_on_relay_host_is_relay(awg_env) -> None:
    user_dir, _ = awg_env
    write_conf(user_dir / "phone.conf", f"{RELAY_HOST}:443")

    candidates, notes = scout_context.awg_candidates(user_dir)

    assert len(candidates) == 1
    assert candidates[0].route == ROUTE_RELAY
    assert candidates[0].proto == PROTO_AWG
    assert candidates[0].profile == "phone"
    assert notes == []


def test_awg_endpoint_on_direct_ip_is_direct(awg_env) -> None:
    user_dir, _ = awg_env
    write_conf(user_dir / "laptop.conf", f"{DIRECT_IP}:51825")

    candidates, _ = scout_context.awg_candidates(user_dir)

    assert len(candidates) == 1
    assert candidates[0].route == ROUTE_DIRECT
    assert (candidates[0].host, candidates[0].port) == (DIRECT_IP, 51825)


def test_relay_conf_defines_the_relay_host(awg_env) -> None:
    """The relay host is a fact from relay.conf, not a hardcoded name."""
    user_dir, _ = awg_env
    write_conf(user_dir / "relay.conf", f"{RESERVED_RELAY_IP}:443")
    write_conf(user_dir / "android.conf", f"{RESERVED_RELAY_IP}:443")
    write_conf(user_dir / "phone.conf", f"{DIRECT_IP}:51825")

    candidates, _ = scout_context.awg_candidates(user_dir)

    assert routes_by_profile(candidates) == {
        "relay": ROUTE_RELAY,
        "phone": ROUTE_DIRECT,
    }


def test_three_relay_awg_copies_collapse_into_one_relay_candidate(
        awg_env) -> None:
    user_dir, _ = awg_env
    for name in ("android", "windows", "relay"):
        write_conf(user_dir / f"{name}.conf", f"{RELAY_HOST}:443")

    candidates, notes = scout_context.awg_candidates(user_dir)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.route, candidate.proto) == (ROUTE_RELAY, PROTO_AWG)
    assert candidate.profile == "relay"  # the relay instance names the leg
    assert candidate.key == f"relay:awg:{RELAY_HOST}:443"
    assert not any("дубль" in note for note in notes)


def test_system_awg_config_is_added_as_direct_candidate(awg_env) -> None:
    """A readable /etc config is the real direct AWG leg, not a mirror."""
    user_dir, system_dir = awg_env
    write_conf(system_dir / "laptop.conf", f"{DIRECT_IP}:51825")

    candidates, _ = scout_context.awg_candidates(user_dir)

    assert len(candidates) == 1
    assert candidates[0].route == ROUTE_DIRECT
    assert candidates[0].profile == "system:laptop"


def test_missing_system_dir_is_skipped_silently(awg_env, tmp_path,
                                                monkeypatch) -> None:
    user_dir, _ = awg_env
    monkeypatch.setattr(awg_mod, "AWG_SYSTEM_CONF_DIR",
                        str(tmp_path / "missing-awg-system"))

    candidates, notes = scout_context.awg_candidates(user_dir)

    assert candidates == []
    assert notes == []


def test_relay_host_match_ignores_case_and_trailing_dot(awg_env) -> None:
    user_dir, _ = awg_env
    write_conf(user_dir / "phone.conf", f"{RELAY_HOST.upper()}.:443")

    candidates, _ = scout_context.awg_candidates(user_dir)

    assert len(candidates) == 1
    assert candidates[0].route == ROUTE_RELAY


def _write_profiles(path: Path) -> None:
    def link(host: str) -> str:
        return ("vless://FIXTURE_UUID_11111111-2222-3333-4444-555555555555"
                f"@{host}:443?security=reality&sni=example.com"
                "&pbk=FIXTURE_PBK_public_key&sid=ab&type=tcp#p")

    profiles: List[Dict[str, str]] = [
        {"name": "relay-reality", "protocol": "xray-reality",
         "link": link("203.0.113.20")},
        {"name": "asv2-reality", "protocol": "xray-reality",
         "link": link("203.0.113.21")},
    ]
    path.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")


def test_store_profiles_keep_name_based_routes(tmp_path) -> None:
    """Regression: relay-reality → relay, asv2-reality → direct."""
    store = tmp_path / "profiles.json"
    _write_profiles(store)

    candidates, notes = scout_context.profile_candidates(store)

    assert notes == []
    assert routes_by_profile(candidates) == {
        "relay-reality": ROUTE_RELAY,
        "asv2-reality": ROUTE_DIRECT,
    }
