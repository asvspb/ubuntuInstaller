"""Profile pull: import share links from a VDS server over ssh.

Mechanics: ``ssh <host> "vds gateway links"`` returns lines of
``<name>: <link>``. For each line:
- name not in the store -> import;
- name exists and link differs -> replace (with a notice);
- link identical -> skip.

ssh failures (unreachable host, missing ``vds`` command) raise PullError
with a readable message. All subprocess calls use argument lists.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

from . import profiles
from .links import LinkError, parse_link
from .runners import RunCmd, run_cmd

SETTINGS_PATH = profiles.CONFIG_DIR / "settings.json"  # re-export convenience

ParseResult = List[Tuple[str, str]]  # (name, link) pairs

# A new record starts with ``<name>: ``. Rich on the server side may also
# wrap right after the colon, leaving a bare ``name:`` line; that is a record
# start too. A link continuation (e.g. ``hysteria2://…``) has text right
# after its colon, so it is NOT matched as a record start.
_NAME_LINE = re.compile(r"^\S+:\s|^\S+:$")


class PullError(Exception):
    """Raised when the ssh roundtrip or the parsing fails fatally."""


def _flush_entry(header: str, tail: str, out: ParseResult) -> None:
    """Validate one assembled record (``header + tail``) and append if valid.

    If the joined record fails to parse, fall back to the header line alone:
    some junk lines carry a colon but no link, and attaching them must not
    destroy the preceding valid record.
    """
    for candidate in (header + tail, header):
        candidate = candidate.strip()
        if ":" not in candidate:
            continue
        name, _, link = candidate.partition(":")
        name = name.strip()
        link = link.strip()
        if not name or not link:
            continue
        try:
            parse_link(link)
        except LinkError:
            continue
        out.append((name, link))
        return


def parse_gateway_links(text: str) -> ParseResult:
    """Parse ``<name>: <link>`` records from the server output.

    Defensive against wrapped output: lines that do not start a new
    ``<name>: `` record are glued to the previous accumulated record before
    validation; lines before the first record are ignored. Unparseable
    records are skipped.
    """
    out: ParseResult = []
    header = ""
    tail = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _NAME_LINE.match(line):
            if header:
                _flush_entry(header, tail, out)
            header = line
            tail = ""
        elif header:
            tail += line
        # lines before the first record: ignored
    if header:
        _flush_entry(header, tail, out)
    return out


def fetch_links(host: str, run: Optional[RunCmd] = None,
                include_obfs: bool = False) -> ParseResult:
    """Run ssh, parse the output; raise PullError on any failure.

    include_obfs asks the server for the extra obfs links of every hysteria2
    client (``vds gateway links --include-obfs`` -> rows named
    ``<name>@obfs``); without it only the plain hysteria2 link is returned.
    """
    runner: RunCmd = run or run_cmd
    remote_cmd = "vds gateway links" + (" --include-obfs" if include_obfs else "")
    rc, out, err = runner(["ssh", host, remote_cmd])
    if rc != 0:
        detail = (err or out).strip()
        raise PullError(
            f"ssh {host} failed (exit {rc}): {detail or 'no error output'}")
    links = parse_gateway_links(out)
    if not links:
        raise PullError(
            f"ssh {host} succeeded but 'vds gateway links' returned no "
            "share links; is the vpn-manager server module installed there?")
    return links


def pull(host: str, run: Optional[RunCmd] = None,
         path: Optional[object] = None,
         include_obfs: bool = False) -> List[Tuple[str, str]]:
    """Sync links into the store. Returns [(name, action), ...].

    action is 'imported' | 'updated' | 'skipped'. include_obfs also imports the
    obfs (salamander) profile of each hysteria2 client as ``<name>@obfs``.
    """
    store = profiles.list_profiles()
    names = {p.get("name") for p in store}
    actions: List[Tuple[str, str]] = []
    for name, link in fetch_links(host, run, include_obfs=include_obfs):
        if name not in names:
            parsed = parse_link(link)
            profiles.add(parsed, raw_link=link, name=name)
            names.add(name)
            actions.append((name, "imported"))
        else:
            existing = profiles.get(name)
            if existing.get("link") == link:
                actions.append((name, "skipped"))
            else:
                profiles.remove(name)
                parsed = parse_link(link)
                profiles.add(parsed, raw_link=link, name=name)
                actions.append((name, "updated"))
    return actions
