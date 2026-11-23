"""vpn sync: read-only pull of authoritative server files (SPEC-20261118-vpn-qr v1.1, §9.1/§9.3).

The single network touchpoint of the qr/conf stack. Server side is never
mutated: every remote call is either ``ssh <host> vds gateway links`` or
``ssh <host> ls -1 <dir>`` or ``scp <host>:<dir>/<stem>.conf <local tmp>`` —
all reads from the server's point of view. Local writes are atomic (tmp +
``os.replace``) and always 0600. Local files are never deleted: profiles that
disappeared from the server are reported as ``STALE (absent on server)`` and
left in place, with an archival hint.

``--dry-run`` performs the same remote reads (via the same mocked runner in
tests) but writes nothing locally: fetched content is compared against the
cache and the temp files are discarded.

Route → source matrix (D1):
- ``direct``: exit_host ``AWG_CLIENTS_DIR`` (scp) + ``vds gateway links``;
  local cache ``~/.awg/direct/``.
- ``relay``: relay_host ``RELAY_CLIENTS_DIR`` (scp) + ``vds gateway links``;
  local cache ``~/.awg/relay/``.

All subprocess calls use argument lists (no ``shell=True``); tests inject
``run`` (``RunCmd``) and monkeypatch ``amneziawg.LOCAL_CONF_DIR``.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import amneziawg, profiles, pull, settings
from .qrgen.models import PROFILE_NAME_RE
from .runners import RunCmd, run_cmd

#: Server-side authoritative client dirs (my-first-vds gateway paths; D1).
AWG_CLIENTS_DIR = "/opt/my-vds/gateway/clients/amneziawg"
RELAY_CLIENTS_DIR = "/opt/my-vds/gateway/clients/relay"

#: Local cache subdirectories under ``~/.awg`` (D3 namespace).
DIRECT_CACHE_SUBDIR = "direct"
RELAY_CACHE_SUBDIR = "relay"

#: Header marking a dead AWG config (D3); the resolver skips such files and
#: sync does not report them as STALE (they are already archived locally).
ARCHIVED_HEADER = "# vpnm:status=archived"


class SyncError(Exception):
    """Raised when an ssh/scp roundtrip fails fatally."""


def _cache_dir(route: str) -> Path:
    """``~/.awg/<route>/`` (honours the ``VPN_MANAGER_AWG_CONF_DIR`` override)."""
    subdir = (RELAY_CACHE_SUBDIR if route == "relay"
              else DIRECT_CACHE_SUBDIR)
    return Path(amneziawg.LOCAL_CONF_DIR) / subdir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_remote_stem(name: str) -> bool:
    """Accept only plain ``<stem>.conf`` names (no paths/traversal tricks)."""
    if "/" in name or "\\" in name or not name.endswith(".conf"):
        return False
    return bool(PROFILE_NAME_RE.match(name[:-len(".conf")]))


def _local_conf_is_archived(path: Path) -> bool:
    try:
        first = path.read_text(encoding="utf-8", errors="strict").lstrip()
    except (OSError, UnicodeDecodeError):
        return False
    return first.startswith(ARCHIVED_HEADER)


def list_remote_confs(host: str, remote_dir: str,
                      run: Optional[RunCmd] = None,
                      skipped: Optional[List[str]] = None) -> List[str]:
    """``ssh <host> ls -1 <remote_dir>`` → sorted remote ``*.conf`` stems.

    Read-only on the server. Entries that are not ``*.conf`` (``old.conf.bak``,
    ``README``, subdirectories) are skipped — when ``skipped`` is a list, their
    names are appended there for the caller to warn about. A ``*.conf`` entry
    that is not a plain stem still raises SyncError (strict, fail-closed).
    Raises SyncError on a non-zero exit too.
    """
    runner: RunCmd = run or run_cmd
    rc, out, err = runner(["ssh", host, "ls", "-1", remote_dir])
    if rc != 0:
        detail = (err or out).strip()
        raise SyncError(
            f"ssh {host} ls {remote_dir} failed (exit {rc}): "
            f"{detail or 'no error output'}")
    stems: List[str] = []
    for line in out.splitlines():
        name = line.strip()
        if not name:
            continue
        if not name.endswith(".conf"):
            # посторонний файл (бэкап, README, подкаталог) — не повод ронять
            # весь sync: пропускаем и отдаём наверх для предупреждения.
            if skipped is not None:
                skipped.append(name)
            continue
        if not _valid_remote_stem(name):
            raise SyncError(
                f"ssh {host} ls {remote_dir}: unexpected entry {name!r}; "
                "aborting instead of guessing")
        stems.append(name[:-len(".conf")])
    return sorted(stems)


def _scp_to_temp(host: str, remote_dir: str, stem: str,
                 run: RunCmd) -> str:
    """Fetch ``<stem>.conf`` into a local temp file; return its text.

    The temp file is removed before returning; only the content survives.
    ``scp`` reads from the server (remote→local copy) and writes to the local
    temp path — no server-side mutation.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=f"vpnm-sync-{stem}.", suffix=".conf")
    tmp_path = Path(tmp_name)
    os.close(fd)
    try:
        rc, out, err = run(["scp", "-q",
                            f"{host}:{remote_dir}/{stem}.conf", str(tmp_path)])
        if rc != 0:
            detail = (err or out).strip()
            raise SyncError(
                f"scp {host}:{remote_dir}/{stem}.conf failed (exit {rc}): "
                f"{detail or 'no error output'}")
        return tmp_path.read_text(encoding="utf-8")
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def sync_confs(route: str, host: str, remote_dir: str,
               run: Optional[RunCmd] = None,
               dry_run: bool = False) -> Dict[str, List[str]]:
    """Pull ``*.conf`` from ``host:<remote_dir>`` into ``~/.awg/<route>/``.

    Report categories: ``imported`` (new locally), ``updated`` (content
    differs), ``skipped`` (identical), ``stale`` (local active file absent on
    the server — reported, never deleted) and ``skipped_remote`` (non-.conf
    entries on the server, e.g. ``old.conf.bak`` — reported, never fetched).
    ``dry_run`` still performs the remote reads but skips every local write.
    """
    runner: RunCmd = run or run_cmd
    report: Dict[str, List[str]] = {
        "imported": [], "updated": [], "skipped": [], "stale": [],
        "skipped_remote": []}
    remote_stems = list_remote_confs(host, remote_dir, runner,
                                     skipped=report["skipped_remote"])
    dest_dir = _cache_dir(route)
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(dest_dir, 0o700)

    for stem in remote_stems:
        text = _scp_to_temp(host, remote_dir, stem, runner)
        dest = dest_dir / f"{stem}.conf"
        if dest.is_file():
            try:
                same = dest.read_text(encoding="utf-8") == text
            except (OSError, UnicodeDecodeError):
                same = False
            action = "skipped" if same else "updated"
        else:
            same = False
            action = "imported"
        if same or dry_run:
            report[action].append(stem)
            continue
        fd, tmp_name = tempfile.mkstemp(dir=str(dest_dir),
                                        prefix=f"{stem}.conf.", suffix=".tmp")
        # chmod до записи (как в qrgen.render / profiles._atomic_write_0600):
        # секрет никогда не лежит в мире-читаемом tmp-файле.
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
        report[action].append(stem)

    local_stems: List[str] = []
    try:
        entries = sorted(dest_dir.glob("*.conf"))
    except OSError:
        entries = []
    for path in entries:
        stem = path.name[:-len(".conf")]
        if PROFILE_NAME_RE.match(stem) and not _local_conf_is_archived(path):
            local_stems.append(stem)
    report["stale"] = [s for s in local_stems if s not in remote_stems]
    return report


def sync_links(route: str, host: str, run: Optional[RunCmd] = None,
               include_obfs: bool = False,
               dry_run: bool = False) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Pull ``vds gateway links`` from ``host`` into the profiles store.

    Same imported/updated/skipped semantics as ``vpnm.pull``; every imported
    or updated record additionally gets the v1.1 metadata ``route``,
    ``source_host`` and ``synced_at`` (D3). Store profiles whose
    ``source_host`` matches this host but which are absent from the server
    output are reported as stale names; nothing is removed.

    Returns ``([(name, action), ...], stale_names)``.
    """
    pairs = pull.fetch_links(host, run, include_obfs=include_obfs)
    store = profiles.list_profiles()
    names = {p.get("name") for p in store}
    metadata = {"route": route, "source_host": host, "synced_at": _now()}
    actions: List[Tuple[str, str]] = []
    remote_names = {name for name, _link in pairs}
    for name, link in pairs:
        if name not in names:
            if not dry_run:
                parsed = pull.parse_link(link)
                profiles.add(parsed, raw_link=link, name=name)
                profiles.update_fields(name, metadata)
            actions.append((name, "imported"))
            continue
        existing = profiles.get(name)
        if existing.get("link") == link:
            # P2 (WP-QR8): skipped-записи без метаданных v1.1 (появились до
            # vpn sync или без него) добирают route/source_host/synced_at —
            # иначе stale-отчёт по source_host не увидит их никогда.
            if not dry_run and not (
                    existing.get("route") and existing.get("source_host")
                    and existing.get("synced_at")):
                profiles.update_fields(name, metadata)
            actions.append((name, "skipped"))
            continue
        if not dry_run:
            profiles.remove(name)
            parsed = pull.parse_link(link)
            profiles.add(parsed, raw_link=link, name=name)
            profiles.update_fields(name, metadata)
        actions.append((name, "updated"))

    stale: List[str] = []
    for record in store:
        if record.get("source_host") != host:
            continue  # legacy records without source_host: not attributable
        name = str(record.get("name") or "")
        if record.get("status") == profiles.STATUS_ARCHIVED:
            continue
        if name and name not in remote_names:
            stale.append(name)
    return actions, stale


def run_sync(exit_host: Optional[str] = None,
             relay_host: Optional[str] = None,
             routes: Tuple[str, ...] = ("direct", "relay"),
             include_obfs: bool = False,
             dry_run: bool = False,
             run: Optional[RunCmd] = None) -> Dict[str, Dict[str, Any]]:
    """Resolve hosts from settings (explicit args win) and sync the routes.

    - ``direct``: exit_host → ``AWG_CLIENTS_DIR`` scp + ``vds gateway links``.
    - ``relay``: relay_host → ``RELAY_CLIENTS_DIR`` scp + ``vds gateway links``.

    A route whose host is unknown (not passed and not saved in settings.json)
    is skipped with ``error`` text in its report instead of failing the whole
    run. A failing ``vds gateway links`` (``PullError``) does not abort the
    route either: the confs report is kept and the failure is recorded as
    ``links_error`` in the route section. Result shape:
    ``{"direct": {"confs": {...}, "links": [...], "links_stale": [...],
    "links_error": "...", "host": H}, ...}`` (missing keys when skipped or
    fine). With explicit ``--exit-host``/``--relay-host`` and
    ``dry_run=False`` the host is persisted to settings.json
    (``ssh_host``/``relay_ssh_host``) once that route syncs successfully
    (same behavior as ``vpn profile pull`` saving ``ssh_host``); the save
    happens once the route's confs were fetched (the host is proven
    reachable), even when ``links`` failed with ``links_error``.
    """
    runner: RunCmd = run or run_cmd
    result: Dict[str, Dict[str, Any]] = {}
    for route in routes:
        if route == "direct":
            host = exit_host or settings.get_host()
            remote_dir = AWG_CLIENTS_DIR
        elif route == "relay":
            host = relay_host or settings.get_relay_host()
            remote_dir = RELAY_CLIENTS_DIR
        else:
            result[route] = {"error": f"unknown route {route!r}"}
            continue
        if not host:
            result[route] = {
                "error": f"no host for route {route!r}; pass the host "
                         "explicitly or save it in settings.json"}
            continue
        section: Dict[str, Any] = {
            "host": host,
            "confs": sync_confs(route, host, remote_dir, runner,
                                dry_run=dry_run),
        }
        try:
            links_actions, links_stale = sync_links(
                route, host, runner, include_obfs=include_obfs,
                dry_run=dry_run)
        except pull.PullError as exc:
            # links — не критический путь: confs уже синхронизированы,
            # отчёт терять нельзя (WP-QR8 P1).
            section["links"] = []
            section["links_stale"] = []
            section["links_error"] = str(exc)
        else:
            section["links"] = links_actions
            section["links_stale"] = links_stale
        # хост сохраняем, когда маршрут реально отвечал (confs вытянуты);
        # сбой links этого не отменяет — хост проверен сессией scp/ls.
        if not dry_run and "confs" in section:
            if route == "direct" and exit_host:
                settings.set_host(exit_host)
            elif route == "relay" and relay_host:
                settings.set_relay_host(relay_host)
        result[route] = section
    return result
