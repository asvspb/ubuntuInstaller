"""Резолвер источников для ``vpn qr`` (SPEC-20261118-vpn-qr §3.2 + v1.1 §9.3).

Читает только локальное состояние и ничего не мутирует:

- ``vpnm.profiles.STORE_PATH`` — профили (share-ссылки);
- ``vpnm.mode.read_mode()`` — активный режим (``iface``/``unit``/``target``);
- ``vpnm.amneziawg.LOCAL_CONF_DIR`` — клиентские AWG-конфиги (0600);
- ``vpnm.amneziawg.AWG_SYSTEM_CONF_DIR`` — только точечное чтение
  ``<name>.conf`` через read-only ``sudo -n cat`` (без ``listdir``/``systemctl``).

Все обращения идут через атрибуты модулей (а не через ``Path.home()``), чтобы
``monkeypatch.setattr`` в тестах изолировал прогон от живой машины (P1).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .. import amneziawg, links, mode, profiles
from .models import (
    PROFILE_NAME_RE,
    RELAY_UNSUPPORTED_PROTOS,
    Proto,
    QrConfigError,
    QrRequest,
    QrUnsupportedComboError,
    ResolvedSource,
    Route,
    TargetOS,
    resolve_client,
    validate_profile_name,
)
from .redact import redact_error

#: Протоколы store → протокол запроса. CDN/ws в ``vpn qr`` не участвует.
_PROTO_BY_STORE = {
    links.PROTOCOL_XRAY_REALITY: Proto.REALITY,
    links.PROTOCOL_XRAY_XHTTP: Proto.XHTTP,
    links.PROTOCOL_HYSTERIA2: Proto.HY,
}

#: Таблица алиасов устройств (v1.1, D3/§9.3): серверные пиры не переименовываются,
#' разнобой exit (mobile/win/laptop) vs relay (android/windows) снимается здесь.
DEVICE_ALIASES: Dict[TargetOS, Tuple[str, ...]] = {
    TargetOS.ANDROID: ("android", "mobile", "phone"),
    TargetOS.IOS: ("ios", "iphone", "ipad"),
    TargetOS.WINDOWS: ("windows", "win"),
    TargetOS.MACOS: ("macos", "mac"),
    TargetOS.LINUX: ("linux", "laptop", "desktop"),
}

#: Заголовок, помечающий мёртвый AWG-конфиг (v1.1, D3/R4).
ARCHIVED_HEADER = "# vpnm:status=archived"

#: Подкаталоги namespace (v1.1, D3); archive/ резолвером не сканируется никогда.
NS_SUBDIRS: Tuple[str, ...] = ("direct", "relay")

_LEGACY_LOCATION_WARNING = (
    "файл '{stem}.conf' лежит в устаревшем расположении ~/.awg/ (без "
    "подкаталога direct//relay//archive/); выполните vpn sync или перенесите "
    "его вручную")

_ARCHIVED_EXPLICIT_WARNING = (
    "конфиг '{stem}.conf' помечен архивным ({header}); авто-резолвер его не "
    "выбирает — для работы с ним требуется --include-archived или перенос "
    "в ~/.awg/archive/")

_ACTIVE_PEER_WARNING = (
    "используется конфиг активного пира '{stem}' ({address}); одновременная "
    "работа с этим хостом вызовет коллизию handshake. Для отдельного "
    "устройства создайте пир: vpn amneziawg add-client <name>")

_MISSING_CLIENT_KEYS_WARNING = (
    "в конфиге '{stem}.conf' отсутствует DNS и/или PersistentKeepalive "
    "(профиль оптимизирован для split-туннеля хоста; на мобильном устройстве "
    "возможна потеря DNS или NAT-таймаут)")

_CLASSIFIED_BY_NAME_WARNING = (
    "профиль '{name}' классифицирован как relay по имени (host {host} "
    "отличается от домена в relay.conf)")


# --- парсинг INI и endpoint'ов ------------------------------------------------

def parse_ini(text: str) -> Dict[str, Dict[str, str]]:
    """Минимальный INI-парсер AWG/WireGuard: ``{section: {key_lower: value}}``.

    Комментарии и строки без ``=`` игнорируются; секции и ключи приводятся к
    нижнему регистру (значения — как в источнике).
    """
    sections: Dict[str, Dict[str, str]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        sections[current][key.strip().lower()] = value.strip()
    return sections


def split_endpoint(value: str, context: str = "AWG Endpoint") -> Tuple[str, int]:
    """``host:port`` (в т.ч. ``[v6]:port``) → ``(host, port)``; иначе код 4."""
    raw = (value or "").strip()
    if not raw:
        raise QrConfigError(f"{context}: пустое значение")
    if raw.startswith("["):
        close = raw.find("]")
        if close == -1:
            raise QrConfigError(f"{context}: незакрытая скобка IPv6-литерала")
        host = raw[1:close]
        rest = raw[close + 1:]
        if not rest.startswith(":") or not rest[1:].isdigit():
            raise QrConfigError(f"{context}: у IPv6-литерала нет порта")
        return host, int(rest[1:])
    if ":" not in raw:
        raise QrConfigError(f"{context}: нет порта (ожидается host:port)")
    host, _, port = raw.rpartition(":")
    if not host or not port.isdigit():
        raise QrConfigError(f"{context}: некорректный host:port")
    return host, int(port)


def _target_host(value: str) -> str:
    """Хост из ``mode.json`` ``target`` (может быть и ``host`` и ``host:port``)."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("["):
        close = raw.find("]")
        if close != -1:
            return raw[1:close]
        return raw
    if raw.count(":") == 1:
        host, _, port = raw.rpartition(":")
        if port.isdigit():
            return host
    return raw


# --- локальное состояние -------------------------------------------------------

def _relay_hosts() -> List[str]:
    """Известные хосты релея (§3.2A): ``relay.conf`` Endpoint + ``mode.target``."""
    hosts: List[str] = []
    relay_conf = Path(amneziawg.LOCAL_CONF_DIR) / "relay.conf"
    try:
        if relay_conf.is_file():
            endpoint = parse_ini(relay_conf.read_text(encoding="utf-8")).get(
                "peer", {}).get("endpoint", "")
            if endpoint:
                hosts.append(split_endpoint(endpoint)[0])
    except (OSError, UnicodeDecodeError):
        pass  # нечитаемый relay.conf не должен ломать резолв

    payload = mode.read_mode()
    if isinstance(payload, dict) and (
            payload.get("mode") == "relay" or payload.get("route") == "relay"):
        host = _target_host(str(payload.get("target") or ""))
        if host:
            hosts.append(host)
    return hosts


def _same_host(left: str, right: str) -> bool:
    return bool(left) and bool(right) and left.strip().lower() == right.strip().lower()


def _host_in(host: str, candidates: Sequence[str]) -> bool:
    return any(_same_host(host, item) for item in candidates)


# --- профили из store ----------------------------------------------------------

@dataclass
class _ProfileCandidate:
    name: str
    proto: Proto
    is_relay: bool
    relay_by_name: bool
    host: str
    port: int
    record: Dict[str, object]


def _profile_candidates(relay_hosts: Sequence[str]) -> List[_ProfileCandidate]:
    """Профили store, пригодные для ``vpn qr`` (reality/xhttp/hy)."""
    try:
        records = profiles.list_profiles()
    except profiles.ProfileStoreError as exc:
        raise QrConfigError(redact_error(exc)) from exc

    out: List[_ProfileCandidate] = []
    for record in records:
        name = str(record.get("name") or "")
        raw_link = str(record.get("link") or "")
        if not name or not raw_link:
            continue
        try:
            link = links.parse_link(raw_link)
        except (links.LinkError, ValueError):
            continue  # битая запись — не кандидат (и не повод падать)
        proto = _PROTO_BY_STORE.get(link.protocol)
        if proto is None:
            continue
        relay_by_name = name == "relay" or name.startswith("relay-")
        host_relay = _host_in(link.host, relay_hosts)
        out.append(_ProfileCandidate(
            name=name, proto=proto, is_relay=relay_by_name or host_relay,
            relay_by_name=relay_by_name, host=link.host, port=link.port,
            record=dict(record)))
    return out


def _resolve_profile(request: QrRequest, client: str, profile_name: Optional[str],
                     relay_hosts: Sequence[str]) -> ResolvedSource:
    candidates = _profile_candidates(relay_hosts)
    wanted_relay = request.route is Route.RELAY
    same_proto = [c for c in candidates if c.proto is request.proto]

    if profile_name is not None:
        chosen = next((c for c in candidates if c.name == profile_name), None)
        if chosen is None:
            known = ", ".join(c.name for c in same_proto) or "нет"
            raise QrConfigError(
                f"--profile '{profile_name}': профиль {request.proto.value} не "
                f"найден (доступны: {known})")
        if chosen.proto is not request.proto:
            raise QrConfigError(
                f"--profile '{profile_name}': протокол профиля "
                f"{chosen.proto.value}, а запрошен {request.proto.value}")
        if chosen.is_relay != wanted_relay:
            kind = "relay" if chosen.is_relay else "direct"
            want = "relay" if wanted_relay else "direct"
            raise QrConfigError(
                f"--profile '{profile_name}': это {kind}-профиль, а запрошен {want}")
    else:
        matching = [c for c in same_proto if c.is_relay == wanted_relay]
        if len(matching) != 1:
            names = ", ".join(c.name for c in matching) or "нет"
            raise QrConfigError(
                f"профиль для {request.route.value}/{request.proto.value}: "
                f"найдено {len(matching)} кандидатов ({names}); "
                "уточните --profile <NAME>")
        chosen = matching[0]

    warnings: List[str] = []
    if chosen.relay_by_name and relay_hosts and not _host_in(chosen.host, relay_hosts):
        warnings.append(_CLASSIFIED_BY_NAME_WARNING.format(
            name=chosen.name, host=chosen.host))

    return ResolvedSource(
        profile_name=chosen.name, route=request.route, proto=request.proto,
        target_os=request.target_os, client=client,
        endpoint_host=chosen.host, endpoint_port=chosen.port,
        source="profile", raw_profile=chosen.record, warnings=warnings)


# --- AWG ----------------------------------------------------------------------

@dataclass
class _AwgCandidate:
    stem: str
    text: str
    endpoint_host: str
    endpoint_port: int
    origin: str  # "local" | "system"
    interface: Dict[str, str]
    peer: Dict[str, str]
    origin_ns: str = "legacy"  # "legacy" (корень ~/.awg) | "direct" | "relay"

    @property
    def address(self) -> str:
        return self.interface.get("address", "")


def _awg_candidate(stem: str, text: str, origin: str,
                   origin_ns: str = "legacy") -> _AwgCandidate:
    ini = parse_ini(text)
    if "interface" not in ini or "peer" not in ini:
        raise QrConfigError(
            f"конфиг '{stem}.conf': нет секций [Interface]/[Peer]")
    endpoint = ini["peer"].get("endpoint", "")
    host, port = split_endpoint(endpoint, context=f"конфиг '{stem}.conf' Endpoint")
    return _AwgCandidate(stem=stem, text=text, endpoint_host=host,
                         endpoint_port=port, origin=origin,
                         interface=ini["interface"], peer=ini["peer"],
                         origin_ns=origin_ns)


def _conf_path(stem: str, ns: Optional[str] = None) -> Path:
    base = Path(amneziawg.LOCAL_CONF_DIR)
    return base / ns / f"{stem}.conf" if ns else base / f"{stem}.conf"


def _conf_is_archived(path: Path) -> bool:
    """True, когда файл начинается с заголовка ``# vpnm:status=archived`` (D3)."""
    try:
        head = path.read_text(encoding="utf-8").lstrip()
    except (OSError, UnicodeDecodeError):
        return False
    return head.startswith(ARCHIVED_HEADER)


def _read_local_conf(stem: str) -> Optional[_AwgCandidate]:
    """Read ``<stem>.conf`` из корня ``~/.awg`` или из ``direct/``/``relay/``.

    Порядок: legacy-корень (совместимость), затем namespace-подкаталоги v1.1.
    Файл с заголовком archived читается (для явного ``--profile`` резолвер
    выдаёт код 4 на нём), но ``archive/`` сюда не входит никогда.
    """
    for ns in (None,) + NS_SUBDIRS:
        path = _conf_path(stem, ns)
        try:
            if not path.is_file():
                continue
            return _awg_candidate(stem, path.read_text(encoding="utf-8"),
                                  "local", origin_ns=ns or "legacy")
        except (OSError, UnicodeDecodeError):
            continue
    return None


def _read_system_conf(stem: str) -> Optional[_AwgCandidate]:
    """Read-only чтение ``<AWG_SYSTEM_CONF_DIR>/<stem>.conf`` через ``sudo -n cat``."""
    path = Path(amneziawg.AWG_SYSTEM_CONF_DIR) / f"{stem}.conf"
    proc = subprocess.run(
        ["sudo", "-n", "cat", str(path)],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        return _awg_candidate(stem, proc.stdout, "system")
    except QrConfigError:
        return None


def _local_conf_stems() -> List[str]:
    """Стемы legacy ``*.conf`` в корне ``LOCAL_CONF_DIR`` (без ``.bak``/``.hidden``)."""
    base = Path(amneziawg.LOCAL_CONF_DIR) / ns if ns else Path(amneziawg.LOCAL_CONF_DIR)
    out: List[str] = []
    try:
        entries = sorted(base.glob("*.conf"))
    except OSError:
        return out
    for path in entries:
        stem = path.name[:-len(".conf")]
        if path.is_file() and PROFILE_NAME_RE.match(stem):
            out.append(stem)
    return out


def _local_conf_candidates() -> List[_AwgCandidate]:
    """Все валидные ``*.conf`` авто-резолвера (v1.1, D3): direct/ + relay/ + legacy-корень.

    ``archive/`` не сканируется никогда; файлы с заголовком
    ``# vpnm:status=archived`` пропускаются (мёртвый конфиг не выдаётся
    молча как рабочий). Один повреждённый файл не должен ломать выбор
    остальных: политика та же, что у битой записи в store. Namespace-копии
    имеют приоритет над legacy-корнем при том же stem.
    """
    out: List[_AwgCandidate] = []
    seen: set = set()
    for ns in NS_SUBDIRS + (None,):
        base = (Path(amneziawg.LOCAL_CONF_DIR) / ns if ns
                else Path(amneziawg.LOCAL_CONF_DIR))
        try:
            entries = sorted(base.glob("*.conf"))
        except OSError:
            continue
        for path in entries:
            stem = path.name[:-len(".conf")]
            if stem in seen or not path.is_file() or not PROFILE_NAME_RE.match(stem):
                continue
            if _conf_is_archived(path):
                continue
            try:
                candidate = _awg_candidate(stem, path.read_text(encoding="utf-8"),
                                           "local", origin_ns=ns or "legacy")
            except (QrConfigError, OSError, UnicodeDecodeError):
                continue
            seen.add(stem)
            out.append(candidate)
    return out


def _is_relay_awg(stem: str, endpoint_host: str,
                  relay_hosts: Sequence[str]) -> bool:
    return (stem == "relay" or stem.startswith("relay-")
            or _host_in(endpoint_host, relay_hosts))


def _iface_from_mode() -> Optional[str]:
    payload = mode.read_mode()
    if not isinstance(payload, dict):
        return None
    iface = str(payload.get("iface") or "").strip()
    if iface:
        return iface
    unit = str(payload.get("unit") or "").strip()
    prefix = amneziawg.AWG_UNIT_PREFIX
    if unit.startswith(prefix):
        rest = unit[len(prefix):]
        if rest.endswith(".service"):
            rest = rest[:-len(".service")]
        return rest or None
    return None


def _finish_awg(chosen: _AwgCandidate, request: QrRequest, client: str,
                warnings: List[str]) -> ResolvedSource:
    if "dns" not in chosen.interface or "persistentkeepalive" not in chosen.peer:
        warnings.append(_MISSING_CLIENT_KEYS_WARNING.format(stem=chosen.stem))
    return ResolvedSource(
        profile_name=chosen.stem, route=request.route, proto=request.proto,
        target_os=request.target_os, client=client,
        endpoint_host=chosen.endpoint_host, endpoint_port=chosen.endpoint_port,
        source="awg", raw_awg_ini=chosen.text, warnings=warnings)


def _explicit_awg(stem: str) -> Optional[_AwgCandidate]:
    """Явный ``--profile``: чтение из корня/direct//relay/ с archived-гейтом (код 4)."""
    candidate = _read_local_conf(stem) or _read_system_conf(stem)
    if candidate is None:
        return None
    if candidate.origin == "local":
        path = _conf_path(stem)
        if not path.is_file():
            for ns in NS_SUBDIRS:
                path = _conf_path(stem, ns)
                if path.is_file():
                    break
        if path.is_file() and _conf_is_archived(path):
            raise QrConfigError(
                f"--profile '{stem}': " + _ARCHIVED_EXPLICIT_WARNING.format(
                    stem=stem, header=ARCHIVED_HEADER))
    return candidate


def _resolve_awg(request: QrRequest, client: str,
                 profile_name: Optional[str]) -> ResolvedSource:
    relay_hosts = _relay_hosts()
    wanted_relay = request.route is Route.RELAY
    warnings: List[str] = []

    def relay_of(candidate: _AwgCandidate) -> bool:
        if candidate.origin_ns == "direct":
            return False
        if candidate.origin_ns == "relay":
            return True
        # legacy-корень: классификация по stem/host (поведение v1)
        return _is_relay_awg(candidate.stem, candidate.endpoint_host, relay_hosts)

    def namespace_of(candidate: _AwgCandidate) -> str:
        return "relay" if relay_of(candidate) else "direct"

    if profile_name is not None:
        chosen = _explicit_awg(profile_name)
        if chosen is None:
            raise QrConfigError(
                f"--profile '{profile_name}': AWG-конфиг не найден ни в "
                "~/.awg (direct//relay//корень), ни в системном каталоге "
                "(или недоступен без пароля sudo)")
        if relay_of(chosen) != wanted_relay:
            kind = "relay" if relay_of(chosen) else "direct"
            want = "relay" if wanted_relay else "direct"
            raise QrConfigError(
                f"--profile '{profile_name}': это {kind}-конфиг, а запрошен {want}")
        if chosen.origin_ns == "legacy":
            warnings.append(_LEGACY_LOCATION_WARNING.format(stem=chosen.stem))
        return _finish_awg(chosen, request, client, warnings)

    pool = [c for c in _local_conf_candidates()
            if namespace_of(c) == ("relay" if wanted_relay else "direct")]
    alias_stems = set(DEVICE_ALIASES[request.target_os]) | {
        request.target_os.value}
    alias_matches = [c for c in pool if c.stem in alias_stems]

    chosen: Optional[_AwgCandidate] = None
    if len(alias_matches) >= 2:
        # v1.1, D3: fail-closed — никаких тихих угадываний
        names = ", ".join(sorted(f"{c.stem}.conf" for c in alias_matches))
        raise QrConfigError(
            f"для {request.route.value}/{request.target_os.value} найдено "
            f"{len(alias_matches)} кандидатов ({names}); уточните --profile "
            "<NAME> или перенесите лишние файлы в ~/.awg/archive/")
    if len(alias_matches) == 1:
        chosen = alias_matches[0]
    elif wanted_relay:
        # legacy-фолбэк v1: единственный stem 'relay' → он; единственный
        # релейный кандидат вообще → он; иначе системный каталог.
        relay_stem = [c for c in pool if c.stem == "relay"]
        if len(relay_stem) == 1:
            chosen = relay_stem[0]
        elif len(pool) == 1:
            chosen = pool[0]
        if chosen is None:
            system = _read_system_conf("relay")
            if system is not None and relay_of(system):
                chosen = system
        if chosen is None:
            raise QrConfigError(
                "не найден релейный AWG-конфиг для "
                f"{request.target_os.value} (уточните --profile <NAME> или "
                "создайте пир: vpn amneziawg add-client <name>)")
    elif len(pool) == 1:
        chosen = pool[0]

    if chosen is not None:
        if chosen.origin_ns == "legacy":
            warnings.append(_LEGACY_LOCATION_WARNING.format(stem=chosen.stem))
        return _finish_awg(chosen, request, client, warnings)

    # приоритет 2 (direct) — активный пир из mode.json (P2: только если он direct)
    iface = _iface_from_mode()
    if iface and PROFILE_NAME_RE.match(iface) and not (
            iface == "relay" or iface.startswith("relay-")):
        active = _explicit_awg(iface)
        if active is not None and not relay_of(active):
            if active.origin_ns == "legacy":
                warnings.append(_LEGACY_LOCATION_WARNING.format(stem=active.stem))
            warnings.append(_ACTIVE_PEER_WARNING.format(
                stem=active.stem, address=active.address or "адрес неизвестен"))
            return _finish_awg(active, request, client, warnings)

    # приоритет 3 — fail-closed
    raise QrConfigError(
        "локальный direct-конфиг AWG не найден (в ~/.awg/direct/ пусто, в "
        "~/.awg только релейные профили, системный каталог пуст или "
        "недоступен без пароля sudo); выполните vpn sync, используйте "
        "--relay --awg или создайте пир: vpn amneziawg add-client <name>")


# --- точка входа ---------------------------------------------------------------

def resolve(request: QrRequest) -> ResolvedSource:
    """Разрешить запрос в конкретный источник (профиль или AWG-конфиг).

    Порядок: неподдерживаемый маршрут (код 3) → клиент (код 2/3) →
    валидация ``--profile`` (код 2) → выбор источника (код 4).
    """
    if request.route is Route.RELAY and request.proto in RELAY_UNSUPPORTED_PROTOS:
        raise QrUnsupportedComboError(
            f"--relay --{request.proto.value}: на релее нет "
            f"{request.proto.value.upper()}-инбаунда (только AWG udp/443 и "
            "Reality tcp/443)")

    client = resolve_client(request.proto, request.target_os, request.client)
    profile_name = (validate_profile_name(request.profile)
                    if request.profile is not None else None)

    if request.proto is Proto.AWG:
        return _resolve_awg(request, client, profile_name)
    return _resolve_profile(request, client, profile_name, _relay_hosts())
