import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict

from src.core.lock import FileLock
from src.core.logger import console, rotate_log
from src.zerotier.api import ZeroTierAPI

logger = logging.getLogger(__name__)

INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/ztnet")
TOPOLOGY_FILE = os.path.join(INSTALL_DIR, "topology.json")
ENV_FILE = os.path.join(INSTALL_DIR, ".env.info")
LOCK_FILE = "/var/run/ztnet.lock"
LOG_FILE = os.path.join(INSTALL_DIR, "zt-reconcile.log")


class MemberDesired(BaseModel):
    name: str = ""
    authorized: bool = False
    ip_assignments: list[str] = Field(default_factory=list)


class NetworkDesired(BaseModel):
    name: str = ""
    subnet: str = ""
    role: str = "mesh"
    routes: list[dict] = Field(default_factory=list)
    members: dict[str, MemberDesired] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    public_ip: str = ""
    main_iface: str = ""
    is_openvz: bool = False


class TopologyMeta(BaseModel):
    version: int = 1
    generated: str = ""
    zt_addr: str = ""
    description: str = ""


class Topology(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    meta: TopologyMeta = Field(default_factory=TopologyMeta, alias="_meta")
    server: ServerConfig = Field(default_factory=ServerConfig)
    networks: dict[str, NetworkDesired] = Field(default_factory=dict)


def load_env() -> dict[str, str]:
    env = {}
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    env[key.strip()] = val.strip()
    return env


def load_topology() -> Optional[Topology]:
    if not os.path.isfile(TOPOLOGY_FILE):
        return None
    try:
        with open(TOPOLOGY_FILE) as f:
            data = json.load(f)
        return Topology(**data)
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted topology.json: {e}")
        return None
    except Exception as e:
        logger.error(f"Invalid topology.json structure: {e}")
        return None


def save_topology(topology: Topology) -> None:
    with open(TOPOLOGY_FILE, "w") as f:
        json.dump(topology.model_dump(by_alias=True), f, indent=2, ensure_ascii=False)
    os.chmod(TOPOLOGY_FILE, 0o600)


def validate_topology(topology: Topology) -> tuple[list[str], list[str]]:
    errors = []
    warnings = []

    if not topology.networks:
        errors.append("missing 'networks' key")
        return errors, warnings

    exit_nodes = []
    for nwid, nd in topology.networks.items():
        if nd.role == "exit-node":
            has_default = any(r.get("target") == "0.0.0.0/0" for r in nd.routes)
            if not has_default:
                warnings.append(f"{nwid}: exit-node but no 0.0.0.0/0 route")
            exit_nodes.append(nwid)

        if not nd.subnet:
            warnings.append(f"{nwid}: no subnet defined")

        for addr, md in nd.members.items():
            if not md.authorized:
                warnings.append(f"{nwid}/{addr}: member not authorized")

    if len(exit_nodes) > 1:
        warnings.append(f"MULTIPLE exit-nodes: {exit_nodes}. Ensure clients don't join multiple exit-node networks at once to avoid routing loops.")

    return errors, warnings


def init_topology(api: ZeroTierAPI) -> int:
    if not api.healthcheck():
        console.print("[error]Controller API healthcheck FAILED — cannot generate topology[/error]")
        return 1

    zt_addr = api.get_zt_addr()
    env = load_env()
    networks = api.get_networks()

    console.print("[info]Generating topology.json from current state...[/info]")

    meta = TopologyMeta(
        version=1,
        generated=datetime.now(timezone.utc).isoformat(),
        zt_addr=zt_addr,
        description="Desired-state topology. Edit networks/members, then run vds zerotier reconcile --apply",
    )
    server = ServerConfig(
        public_ip=env.get("PUBLIC_IP", ""),
        main_iface=env.get("MAIN_IFACE", ""),
        is_openvz=env.get("IS_OPENVZ", "false") == "true",
    )
    topology = Topology(meta=meta, server=server, networks={})

    for net in networks:
        if net.get("status") != "OK":
            continue
        nwid = net["id"]
        net_detail = api.get_network(nwid) or {}
        members_runtime = api.get_members(nwid)

        members_desired = {}
        for addr, m in members_runtime.items():
            members_desired[addr] = MemberDesired(
                name=m.get("name", ""),
                authorized=m.get("authorized", False),
                ip_assignments=m.get("ipAssignments", []),
            )

        routes = net_detail.get("routes", [])
        has_default_route = any(r.get("target") == "0.0.0.0/0" for r in routes)

        subnet = ""
        pools = net_detail.get("ipAssignmentPools", [])
        if pools:
            start = pools[0].get("ipRangeStart", "")
            if start:
                parts = start.split(".")
                subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"

        topology.networks[nwid] = NetworkDesired(
            name=net.get("name", ""),
            subnet=subnet,
            role="exit-node" if has_default_route else "mesh",
            routes=routes,
            members=members_desired,
        )

    save_topology(topology)
    console.print(f"[success]Topology saved to {TOPOLOGY_FILE}[/success]")
    console.print(f"[success]Networks: {len(topology.networks)}[/success]")
    for nwid, nd in topology.networks.items():
        console.print(f"[info]  {nwid} ({nd.name}): {nd.role}, {len(nd.members)} members[/info]")

    return 0


def reconcile(api: ZeroTierAPI, topology: Topology, dry_run: bool = True) -> int:
    errors, warnings = validate_topology(topology)
    for e in errors:
        console.print(f"[error]VALIDATION ERROR: {e}[/error]")
    for w in warnings:
        console.print(f"[warning]VALIDATION WARN: {w}[/warning]")
    if errors:
        console.print("[error]Fix validation errors before applying[/error]")
        return 1

    if not api.healthcheck():
        console.print("[error]Controller API healthcheck FAILED — aborting[/error]")
        return 1

    zt_addr = topology.meta.zt_addr or api.get_zt_addr()
    changes = 0

    for nwid, desired in topology.networks.items():
        actual_members = api.get_members(nwid)

        for addr, desired_member in desired.members.items():
            actual = actual_members.get(addr, {})

            if actual.get("authorized") != desired_member.authorized:
                action = "authorize" if desired_member.authorized else "deauthorize"
                console.print(
                    f"[change][APPLY] {nwid}/{addr}: {action} "
                    f"(desired={desired_member.authorized}, actual={actual.get('authorized')})[/change]"
                )
                if not dry_run:
                    if api.authorize_member(nwid, addr, desired_member.authorized):
                        console.print(f"[success]  {addr}: {action}d[/success]")
                    else:
                        console.print(f"[error]  {addr}: failed to {action}[/error]")
                changes += 1

            desired_ips = desired_member.ip_assignments
            actual_ips = actual.get("ipAssignments", [])
            if desired_ips and set(desired_ips) != set(actual_ips):
                console.print(f"[change][APPLY] {nwid}/{addr}: update IPs {actual_ips} -> {desired_ips}[/change]")
                if not dry_run:
                    if api.set_member_ips(nwid, addr, desired_ips):
                        console.print(f"[success]  {addr}: IPs updated[/success]")
                    else:
                        console.print(f"[error]  {addr}: failed to update IPs[/error]")
                changes += 1

            desired_name = desired_member.name
            actual_name = actual.get("name", "")
            if desired_name and desired_name != actual_name:
                console.print(f"[change][APPLY] {nwid}/{addr}: update name '{actual_name}' -> '{desired_name}'[/change]")
                if not dry_run:
                    if api.set_member_name(nwid, addr, desired_name):
                        console.print(f"[success]  {addr}: Name updated[/success]")
                    else:
                        console.print(f"[error]  {addr}: failed to update name[/error]")
                changes += 1

        for addr in actual_members:
            if addr == zt_addr:
                continue
            if addr not in desired.members:
                console.print(f"[warning]{nwid}/{addr}: orphan (in controller, not in topology) — NOT deleting[/warning]")

    if changes == 0:
        console.print("[success]State is in sync. No changes needed.[/success]")
    elif dry_run:
        console.print(f"[info]{changes} changes would be applied. Run with --apply to execute.[/info]")
    else:
        console.print(f"[success]{changes} changes applied.[/success]")

    return 0


def run_reconcile(apply: bool = False, init: bool = False, validate: bool = False) -> int:
    rotate_log(LOG_FILE)

    lock = FileLock(LOCK_FILE)
    if not lock.acquire():
        console.print(f"[error]Lock contention — another instance holds {LOCK_FILE}[/error]")
        return 1

    try:
        api = ZeroTierAPI()

        if init:
            return init_topology(api)

        if validate:
            topology = load_topology()
            if not topology:
                console.print(f"[error]Not found: {TOPOLOGY_FILE}[/error]")
                return 1
            errors, warnings = validate_topology(topology)
            for e in errors:
                console.print(f"[error]{e}[/error]")
            for w in warnings:
                console.print(f"[warning]{w}[/warning]")
            if errors:
                return 1
            console.print("[success]Topology is valid[/success]")
            return 0

        topology = load_topology()
        if not topology:
            console.print(f"[warning]Topology not configured: {TOPOLOGY_FILE}[/warning]")
            console.print("[warning]Run: vds zerotier reconcile --init to generate[/warning]")
            return 0  # exit 0 so systemd timer does not mark service as failed

        if not topology.networks:
            console.print("[warning]No networks in topology.json — skipping reconcile[/warning]")
            console.print("[warning]Run: vds zerotier reconcile --init to populate[/warning]")
            return 0  # exit 0 so systemd timer does not mark service as failed

        return reconcile(api, topology, dry_run=not apply)
    finally:
        lock.release()
