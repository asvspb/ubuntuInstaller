import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import profiles  # noqa: E402
from vpnm.links import ProfileLink  # noqa: E402


def make_link(protocol="xray-reality", name="test", host="203.0.113.10",
              port=443, credential="11111111-2222-3333-4444-555555555555"):
    return ProfileLink(protocol=protocol, host=host, port=port,
                       credential=credential, name=name,
                       params={"sni": "example-test.invalid"})


@pytest.fixture
def store(tmp_path):
    return tmp_path / "profiles.json"


def test_add_and_get(store):
    record = profiles.add(make_link(), raw_link="vless://x", path=store)
    assert record["name"] == "test"
    assert record["protocol"] == "xray-reality"
    assert record["socks_port"] == 2080
    got = profiles.get("test", path=store)
    assert got["link"] == "vless://x"


def test_default_socks_ports(store):
    hy2 = profiles.add(make_link(protocol="hysteria2"), raw_link="hysteria2://x", path=store)
    assert hy2["socks_port"] == 2083
    xh = profiles.add(make_link(protocol="xray-xhttp", name="xh"),
                      raw_link="vless://x", path=store)
    assert xh["socks_port"] == 2080


def test_duplicate_name_rejected(store):
    profiles.add(make_link(), raw_link="vless://x", path=store)
    with pytest.raises(profiles.ProfileStoreError, match="already exists"):
        profiles.add(make_link(name="test", host="203.0.113.99"),
                     raw_link="vless://y", path=store)


def test_list_and_remove(store):
    profiles.add(make_link(), raw_link="vless://x", path=store)
    profiles.add(make_link(name="second"), raw_link="vless://y", path=store)
    rows = profiles.list_profiles(path=store)
    assert [r["name"] for r in rows] == ["test", "second"]
    removed = profiles.remove("test", path=store)
    assert removed["name"] == "test"
    assert [r["name"] for r in profiles.list_profiles(path=store)] == ["second"]
    with pytest.raises(profiles.ProfileStoreError, match="not found"):
        profiles.remove("test", path=store)


def test_file_mode_0600_after_write(store):
    profiles.add(make_link(), raw_link="vless://x", path=store)
    profiles.add(make_link(name="second"), raw_link="vless://y", path=store)
    mode = store.stat().st_mode & 0o777
    assert mode == 0o600
    data = json.loads(store.read_text(encoding="utf-8"))
    assert len(data["profiles"]) == 2
    assert "created" in data["profiles"][0]


def test_empty_name_rejected(store):
    with pytest.raises(profiles.ProfileStoreError, match="name is empty"):
        profiles.add(make_link(name=""), raw_link="vless://x", path=store)
