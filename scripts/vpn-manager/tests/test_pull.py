"""Tests for profile pull: gateway-links parser, ssh mock, store sync."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vpnm import profiles, pull, settings  # noqa: E402
from vpnm.links import parse_link  # noqa: E402


def add_profile(link: str, name: str) -> None:
    profiles.add(parse_link(link), raw_link=link, name=name)

FIXTURE_VLESS = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.10:443"
    "?security=reality&sni=example-test.invalid&pbk=FIXTURE_PBK&sid=ab"
    "#srv-reality"
)
FIXTURE_VLESS_V2 = FIXTURE_VLESS.replace("sid=ab", "sid=cd")
FIXTURE_HY2 = "hysteria2://FIXTURE_PW_hy2@203.0.113.11:8443?insecure=1#srv-hy2"
FIXTURE_XHTTP = (
    "vless://11111111-2222-3333-4444-555555555555@203.0.113.12:443"
    "?security=tls&network=xhttp&path=/xhttp-test#srv-xhttp"
)

SAMPLE_OUTPUT = "\n".join([
    f"srv-reality: {FIXTURE_VLESS}",
    f"srv-hy2: {FIXTURE_HY2}",
    f"srv-xhttp: {FIXTURE_XHTTP}",
])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    return tmp_path


# --- parser -------------------------------------------------------------------

def test_parser_extracts_three_protocols():
    got = pull.parse_gateway_links(SAMPLE_OUTPUT)
    names = [n for n, _ in got]
    assert names == ["srv-reality", "srv-hy2", "srv-xhttp"]


def test_parser_ignores_garbage_lines():
    text = "\n".join([
        "Gateway links:",
        f"srv-reality: {FIXTURE_VLESS}",
        "no-colon-line",
        ": empty-name",
        "bad-name: not-a-link",
        "",
        f"srv-hy2: {FIXTURE_HY2}",
        "  srv-spaced:   " + FIXTURE_XHTTP + "  ",
    ])
    got = pull.parse_gateway_links(text)
    assert [n for n, _ in got] == ["srv-reality", "srv-hy2", "srv-spaced"]


# --- ssh roundtrip ---------------------------------------------------------------

def make_run_mock(outputs):
    """Return a run stub keyed by command substring; records invocations."""
    calls = []

    def run(cmd):
        calls.append(cmd)
        for needle, result in outputs.items():
            if needle in " ".join(cmd):
                return result
        return 0, "", ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_fetch_links_parses_ssh_output():
    run = make_run_mock({"ssh": (0, SAMPLE_OUTPUT, "")})
    got = pull.fetch_links("root@asv1", run=run)
    assert len(got) == 3
    assert run.calls[0][:2] == ["ssh", "root@asv1"]
    assert "vds gateway links" in run.calls[0][2]


def test_fetch_links_unreachable_raises():
    run = make_run_mock({
        "ssh": (255, "", "ssh: Could not resolve hostname x.invalid")})
    with pytest.raises(pull.PullError, match="ssh root@asv1 failed"):
        pull.fetch_links("root@asv1", run=run)


def test_fetch_links_no_vds_on_server_raises():
    run = make_run_mock({"ssh": (127, "", "vds: command not found")})
    with pytest.raises(pull.PullError, match="command not found"):
        pull.fetch_links("root@asv1", run=run)


def test_fetch_links_empty_output_raises():
    run = make_run_mock({"ssh": (0, "Gateway links:\n", "")})
    with pytest.raises(pull.PullError, match="no share links"):
        pull.fetch_links("root@asv1", run=run)


# --- sync behaviour ---------------------------------------------------------------

def test_pull_imports_updates_and_skips(env):
    add_profile(FIXTURE_VLESS, "srv-reality")
    # srv-hy2 exists with the same link -> skip; srv-xhttp is new -> import
    add_profile(FIXTURE_HY2, "srv-hy2")
    run = make_run_mock({"ssh": (0, SAMPLE_OUTPUT.replace(
        FIXTURE_VLESS, FIXTURE_VLESS_V2), "")})
    actions = pull.pull("root@asv1", run=run)
    by_name = dict(actions)
    assert by_name["srv-reality"] == "updated"
    assert by_name["srv-hy2"] == "skipped"
    assert by_name["srv-xhttp"] == "imported"
    assert profiles.get("srv-reality")["link"] == FIXTURE_VLESS_V2


def test_pull_invalid_link_line_is_ignored_not_fatal(env):
    run = make_run_mock({"ssh": (0, f"broken: garbage-line\nsrv-hy2: {FIXTURE_HY2}", "")})
    actions = pull.pull("root@asv1", run=run)
    assert actions == [("srv-hy2", "imported")]


def _wrap(text: str, width: int = 80) -> str:
    """Split text into physical lines of ~width chars (rich-wrap simulation)."""
    return "\n".join(text[i:i + width] for i in range(0, len(text), width))


def test_parser_reassembles_wrapped_record():
    """A link hard-wrapped into 3 lines of ~80 chars (as rich does on the
    server side) must be reassembled into a single record."""
    record = f"srv-reality: {FIXTURE_VLESS}"
    record = record.replace(
        "sni=example-test.invalid", "sni=" + "sub.example-test.invalid." * 3)
    assert len(record) > 160  # ensures the wrap actually splits the link
    wrapped = "\n".join(record[i:i + 80] for i in range(0, len(record), 80))
    assert wrapped.count("\n") >= 2  # 3+ physical lines

    expected_link = record.split(": ", 1)[1]
    got = pull.parse_gateway_links(wrapped)
    assert got == [("srv-reality", expected_link)]


def test_parser_reassembles_mixed_wrapped_and_single_lines():
    wrapped = _wrap(f"srv-reality: {FIXTURE_VLESS}") + "\n" + \
        f"srv-hy2: {FIXTURE_HY2}\n" + _wrap(f"srv-xhttp: {FIXTURE_XHTTP}")
    got = pull.parse_gateway_links(wrapped)
    assert got == [
        ("srv-reality", FIXTURE_VLESS),
        ("srv-hy2", FIXTURE_HY2),
        ("srv-xhttp", FIXTURE_XHTTP),
    ]


def test_parser_ignores_garbage_before_first_record():
    text = "\n".join([
        "Gateway links:",
        "Installed protocols: xray, hysteria2",
        f"srv-reality: {FIXTURE_VLESS}",
    ])
    got = pull.parse_gateway_links(text)
    assert got == [("srv-reality", FIXTURE_VLESS)]
