import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Any
from dataclasses import dataclass
import requests
from requests.exceptions import RequestException, Timeout, ConnectionError

from src.core.shell import docker_exec

logger = logging.getLogger(__name__)

CONTAINER = "ztnet_zerotier"
API_BASE = "http://localhost:9993"


@dataclass
class ZTStatus:
    address: str
    online: bool
    version: str
    raw: dict


class ZeroTierAPI:
    def __init__(self, token: Optional[str] = None):
        self._token = token
        self._session = requests.Session()
        if self._token:
            self._session.headers.update({"X-ZT1-Auth": self._token})
        else:
            # Force fetching the token so headers are populated
            _ = self.token

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        if self._session:
            self._session.close()

    @property
    def token(self) -> str:
        if not self._token:
            self._token = self._get_authtoken()
            if self._token:
                self._session.headers.update({"X-ZT1-Auth": self._token})
        return self._token or ""

    def _get_authtoken(self) -> str:
        result = docker_exec(
            CONTAINER,
            "cat /var/lib/zerotier-one/authtoken.secret",
            timeout=10,
        )
        return result.output.strip() if result.ok else ""

    def healthcheck(self) -> bool:
        try:
            resp = self._session.get(f"{API_BASE}/status", timeout=5)
            data = resp.json()
            return isinstance(data, dict) and "address" in data
        except (RequestException, ValueError):
            return False

    def get_status(self) -> Optional[ZTStatus]:
        try:
            resp = self._session.get(f"{API_BASE}/status", timeout=5)
            data = resp.json()
            return ZTStatus(
                address=data.get("address", ""),
                online=data.get("online", False),
                version=data.get("version", ""),
                raw=data,
            )
        except (RequestException, ValueError):
            return None

    def get_zt_addr(self) -> str:
        result = docker_exec(CONTAINER, "zerotier-cli info", timeout=10)
        if result.ok:
            parts = result.output.split()
            return parts[2] if len(parts) >= 3 else ""
        return ""

    def controller_request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
    ) -> Optional[Any]:
        url = f"{API_BASE}{path}"
        try:
            if method.upper() == "GET":
                resp = self._session.get(url, timeout=10)
            elif method.upper() == "POST":
                resp = self._session.post(url, json=data, timeout=10)
            elif method.upper() == "DELETE":
                resp = self._session.delete(url, timeout=10)
            else:
                return None
            return resp.json()
        except (RequestException, ValueError) as e:
            logger.debug(f"API request failed: {method} {path}: {e}")
            return None

    def get_networks(self) -> list[dict]:
        result = docker_exec(CONTAINER, "zerotier-cli -j listnetworks", timeout=10)
        if result.ok:
            try:
                return json.loads(result.output)
            except json.JSONDecodeError:
                pass
        return []

    def get_network(self, nwid: str) -> Optional[dict]:
        return self.controller_request("GET", f"/controller/network/{nwid}")

    def get_members(self, nwid: str) -> dict[str, dict]:
        raw = self.controller_request("GET", f"/controller/network/{nwid}/member")
        if not raw or not isinstance(raw, dict):
            return {}

        members = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(
                    self.controller_request,
                    "GET",
                    f"/controller/network/{nwid}/member/{addr}",
                ): addr
                for addr in raw
            }
            for future in as_completed(futures):
                addr = futures[future]
                try:
                    member = future.result()
                    if member:
                        members[addr] = member
                except Exception as e:
                    logger.debug(f"Failed to fetch member {addr}: {e}")
        return members

    def authorize_member(self, nwid: str, addr: str, authorized: bool = True) -> bool:
        result = self.controller_request(
            "POST",
            f"/controller/network/{nwid}/member/{addr}",
            {"authorized": authorized},
        )
        return result is not None

    def set_member_ips(self, nwid: str, addr: str, ips: list[str]) -> bool:
        result = self.controller_request(
            "POST",
            f"/controller/network/{nwid}/member/{addr}",
            {"ipAssignments": ips},
        )
        return result is not None

    def set_member_name(self, nwid: str, addr: str, name: str) -> bool:
        result = self.controller_request(
            "POST",
            f"/controller/network/{nwid}/member/{addr}",
            {"name": name},
        )
        return result is not None

    def join_network(self, nwid: str) -> bool:
        result = docker_exec(CONTAINER, f"zerotier-cli join {nwid}", timeout=10)
        return result.ok

    def get_peers(self) -> list[dict]:
        result = docker_exec(CONTAINER, "zerotier-cli -j listpeers", timeout=10)
        if result.ok:
            try:
                return json.loads(result.output)
            except json.JSONDecodeError:
                pass
        return []
