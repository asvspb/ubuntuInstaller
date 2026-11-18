"""Сборка payload для ``vpn qr`` (SPEC-20261118-vpn-qr §4.1–§4.4).

Чистые функции: вход — :class:`~vpnm.qrgen.models.ResolvedSource`, выход —
:class:`BuiltPayload` (готовая строка + метаданные для шапки/JSON). Никаких
файлов, subprocess и сети: всё, что нужно, резолвер уже прочитал в WP-QR1.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .. import links
from ..xclient import XHTTP_PADDING_BYTES, XHTTP_XMUX
from .models import Proto, QrConfigError, ResolvedSource
from .redact import redact_error

#: Порог коррекции QR (§5.1): до 350 байт — M, дальше — L.
ECC_SMALL_MAX_BYTES = 350

#: Директивы wg-quick, которые клиентские парсеры AWG не понимают (R2).
SYSTEM_ONLY_KEYS = frozenset({
    "table", "saveconfig", "preup", "postup", "predown", "postdown",
})

#: Обязательные поля клиентского AWG-конфига (§4.1).
REQUIRED_INTERFACE_KEYS = ("privatekey", "address")
REQUIRED_PEER_KEYS = ("publickey", "endpoint", "allowedips")


@dataclass(frozen=True)
class BuiltPayload:
    """Результат сборки: то, что уйдёт в QR/файл/stdout."""

    payload: str
    payload_format: str  # "uri" (vless://, hysteria2://) | "conf" (AWG INI)
    payload_bytes: int
    payload_sha256: str
    endpoint: str
    qr_ecc: str

    @classmethod
    def from_text(cls, text: str, payload_format: str,
                  endpoint: str) -> "BuiltPayload":
        data = text.encode("utf-8")
        return cls(
            payload=text,
            payload_format=payload_format,
            payload_bytes=len(data),
            payload_sha256=f"sha256:{hashlib.sha256(data).hexdigest()}",
            endpoint=endpoint,
            qr_ecc="M" if len(data) <= ECC_SMALL_MAX_BYTES else "L",
        )


# --- общие помощники ----------------------------------------------------------

def _quote(value: object) -> str:
    """Percent-encoding значения query-параметра (§4.1)."""
    return quote(str(value), safe="")


def _authority(host: str, port: int) -> str:
    """``host:port`` с квадратными скобками для IPv6-литерала."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _label(resolved: ResolvedSource) -> str:
    """Фрагмент ``#<profile>-<os>`` (§4.1)."""
    return _quote(f"{resolved.profile_name}-{resolved.target_os.value}")


def _query(pairs: Dict[str, object]) -> str:
    """Query-строка с сортировкой ключей (детерминизм, §4.1)."""
    return "&".join(f"{key}={_quote(pairs[key])}" for key in sorted(pairs))


def _profile_link(resolved: ResolvedSource) -> links.ProfileLink:
    record = resolved.raw_profile or {}
    raw = record.get("link")
    if not raw:
        raise QrConfigError(
            f"профиль '{resolved.profile_name}': в store нет share-ссылки")
    try:
        return links.parse_link(str(raw))
    except (links.LinkError, ValueError) as exc:
        raise QrConfigError(redact_error(exc)) from exc


def _require(name: str, values: Dict[str, object]) -> None:
    """Проверить обязательные непустые поля; в тексте — только имена."""
    missing: List[str] = [key for key, value in values.items() if not value]
    if missing:
        raise QrConfigError(
            f"профиль '{name}': нет обязательных полей: {', '.join(missing)}")


# --- AWG ----------------------------------------------------------------------

def _sanitize_awg(raw: str, stem: str) -> str:
    """Убрать system-only директивы wg-quick и нормализовать текст (R2).

    Значения клиентских ключей не трогаются: `AllowedIPs`, `DNS`, `MTU`,
    `Jc..H4`, `I1..I5`, `Endpoint` переносятся байт-в-байт.
    """
    kept: List[str] = []
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            kept.append(f"[{stripped[1:-1].strip()}]")
            continue
        if "=" in stripped and not stripped.startswith(("#", ";")):
            key = stripped.split("=", 1)[0].strip().lower()
            if key in SYSTEM_ONLY_KEYS:
                continue
        kept.append(line)

    normalized: List[str] = []
    for line in kept:
        if line == "":
            if normalized and normalized[-1] == "":
                continue
        normalized.append(line)
    while normalized and normalized[0] == "":
        normalized.pop(0)
    while normalized and normalized[-1] == "":
        normalized.pop()
    text = "\n".join(normalized) + "\n"

    # валидация структуры и обязательных полей — по уже очищенному тексту
    from .resolver import parse_ini  # локальный импорт: избегаем цикла модулей
    ini = parse_ini(text)
    if "interface" not in ini or "peer" not in ini:
        raise QrConfigError(f"конфиг '{stem}.conf': нет секций [Interface]/[Peer]")
    missing = [key for key in REQUIRED_INTERFACE_KEYS
               if not ini["interface"].get(key)]
    missing += [f"peer.{key}" for key in REQUIRED_PEER_KEYS
                if not ini["peer"].get(key)]
    if missing:
        raise QrConfigError(
            f"конфиг '{stem}.conf': нет обязательных полей: {', '.join(missing)}")
    return text


def build_awg(resolved: ResolvedSource) -> BuiltPayload:
    raw = resolved.raw_awg_ini
    if not raw:
        raise QrConfigError(
            f"конфиг '{resolved.profile_name}': пустой AWG INI в источнике")
    return BuiltPayload.from_text(
        _sanitize_awg(raw, resolved.profile_name), "conf", resolved.endpoint)


# --- VLESS Reality ------------------------------------------------------------

def build_reality(resolved: ResolvedSource) -> BuiltPayload:
    link = _profile_link(resolved)
    params = link.params
    _require(resolved.profile_name, {
        "uuid": link.credential,
        "host": link.host,
        "port": link.port,
        "sni": params.get("sni"),
        "pbk": params.get("pbk"),
    })

    pairs: Dict[str, object] = {
        "encryption": "none",
        "flow": params.get("flow") or "xtls-rprx-vision",
        "fp": params.get("fp") or "chrome",
        "pbk": params["pbk"],
        "security": "reality",
        "sid": params.get("sid", ""),
        "sni": params["sni"],
        "type": "tcp",
    }
    spath = params.get("spath") or params.get("spx")
    if spath:
        pairs["spx"] = spath

    uri = (f"vless://{_quote(link.credential)}@"
           f"{_authority(link.host, link.port)}?{_query(pairs)}"
           f"#{_label(resolved)}")
    return BuiltPayload.from_text(uri, "uri", resolved.endpoint)


# --- VLESS XHTTP --------------------------------------------------------------

def _gr25_extra(params: Dict[str, str]) -> Dict[str, Any]:
    """Анти-ML `extra` (GR-25) из `xclient` + переопределения профиля (§4.3)."""
    extra: Dict[str, Any] = {
        "xPaddingBytes": XHTTP_PADDING_BYTES,
        "xmux": dict(XHTTP_XMUX),
    }
    override_raw = params.get("extra")
    if override_raw:
        try:
            override = json.loads(override_raw)
        except ValueError as exc:
            raise QrConfigError(
                "профиль: параметр extra не является корректным JSON") from exc
        if not isinstance(override, dict):
            raise QrConfigError("профиль: параметр extra должен быть JSON-объектом")
        extra.update(override)
    return extra


def build_xhttp(resolved: ResolvedSource) -> BuiltPayload:
    link = _profile_link(resolved)
    params = link.params
    _require(resolved.profile_name, {
        "uuid": link.credential,
        "host": link.host,
        "port": link.port,
    })

    security = params.get("security") or "tls"
    extra = _gr25_extra(params)
    pairs: Dict[str, object] = {
        "encryption": "none",
        "extra": json.dumps(extra, separators=(",", ":"), sort_keys=True),
        "fp": params.get("fp") or "chrome",
        "mode": params.get("mode") or "auto",
        "path": params.get("path") or "/",
        "security": security,
        "sni": params.get("sni") or link.host,
        "type": "xhttp",
    }
    # flow=xtls-rprx-vision несовместим с type=xhttp (§4.3, инвариант 1) —
    # ключ не добавляется никогда, даже если он есть в профиле.
    if params.get("host"):
        pairs["host"] = params["host"]
    if security == "reality":
        pairs["pbk"] = params.get("pbk", "")
        pairs["sid"] = params.get("sid", "")
        spath = params.get("spath") or params.get("spx")
        if spath:
            pairs["spx"] = spath

    uri = (f"vless://{_quote(link.credential)}@"
           f"{_authority(link.host, link.port)}?{_query(pairs)}"
           f"#{_label(resolved)}")
    return BuiltPayload.from_text(uri, "uri", resolved.endpoint)


# --- Hysteria2 ----------------------------------------------------------------

def _normalize_mport(value: str) -> str:
    """Разделитель диапазона приводится к дефису (§4.4): ``20000:20500`` → ``20000-20500``."""
    return (value or "").strip().replace(":", "-")


def build_hy2(resolved: ResolvedSource) -> BuiltPayload:
    link = _profile_link(resolved)
    params = link.params
    _require(resolved.profile_name, {
        "auth": link.credential,
        "host": link.host,
        "port": link.port,
    })

    pairs: Dict[str, object] = {
        "sni": params.get("sni") or link.host,
    }
    obfs = params.get("obfs", "")
    if obfs:
        pairs["obfs"] = obfs
        pairs["obfs-password"] = params.get("obfs-password", "")
    mport = _normalize_mport(params.get("mport", ""))
    if mport:
        pairs["mport"] = mport
    pin = params.get("pinSHA256") or params.get("pin_sha256")
    if pin:
        pairs["pinSHA256"] = pin
    if params.get("insecure") in ("1", "true"):
        pairs["insecure"] = "1"

    uri = (f"hysteria2://{_quote(link.credential)}@"
           f"{_authority(link.host, link.port)}/?{_query(pairs)}"
           f"#{_label(resolved)}")
    return BuiltPayload.from_text(uri, "uri", resolved.endpoint)


# --- точка входа --------------------------------------------------------------

_BUILDERS = {
    Proto.AWG: build_awg,
    Proto.REALITY: build_reality,
    Proto.XHTTP: build_xhttp,
    Proto.HY: build_hy2,
}


def build_payload(resolved: ResolvedSource) -> BuiltPayload:
    """Собрать payload по протоколу из :class:`ResolvedSource`."""
    builder = _BUILDERS.get(resolved.proto)
    if builder is None:  # pragma: no cover - защита от расширения Enum'а
        raise QrConfigError(
            f"протокол '{resolved.proto.value}': сборка payload не реализована")
    return builder(resolved)
