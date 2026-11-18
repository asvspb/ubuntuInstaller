"""Модели, коды возврата и валидация входных параметров ``vpn qr``.

Источник истины — SPEC-20261118-vpn-qr (rev.3.2, G2 PASS):
§2.2 (флаги и валидация ``--profile``), §3.3 (матрица ``--client`` × протокол),
§6 (коды возврата).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

# --- коды возврата (§6) -------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNSUPPORTED_COMBO = 3
EXIT_CONFIG_ERROR = 4
EXIT_RENDER_ERROR = 5


class QrError(Exception):
    """Ошибка ``vpn qr`` с привязкой к коду возврата (§6).

    ``exit_code`` читает CLI-обвязка (WP-QR3) и превращает в
    ``typer.Exit(code=...)``. Текст ошибки обязан быть уже санитизирован
    (``vpnm.qrgen.redact``): секреты в него не попадают никогда.
    """

    exit_code: int = 1

    def __init__(self, message: str, exit_code: Optional[int] = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class QrUsageError(QrError):
    """Невалидные флаги CLI (код 2)."""

    exit_code = EXIT_USAGE


class QrUnsupportedComboError(QrError):
    """Маршрут/протокол/клиент не поддерживаются (код 3)."""

    exit_code = EXIT_UNSUPPORTED_COMBO


class QrConfigError(QrError):
    """Локальное состояние не даёт валидного профиля (код 4)."""

    exit_code = EXIT_CONFIG_ERROR


class QrRenderError(QrError):
    """Рендер/запись не удались (код 5)."""

    exit_code = EXIT_RENDER_ERROR


# --- перечисления --------------------------------------------------------------

class Route(str, Enum):
    DIRECT = "direct"
    RELAY = "relay"


class Proto(str, Enum):
    AWG = "awg"
    REALITY = "reality"
    XHTTP = "xhttp"
    HY = "hy"


class TargetOS(str, Enum):
    ANDROID = "android"
    IOS = "ios"
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"


class OutputFormat(str, Enum):
    QR = "qr"
    URI = "uri"
    PNG = "png"
    SVG = "svg"
    JSON = "json"


class ClientName(str, Enum):
    AUTO = "auto"
    AMNEZIAWG = "amneziawg"
    AMNEZIAVPN = "amneziavpn"
    V2RAYNG = "v2rayng"
    HIDDIFY = "hiddify"
    NEKOBOX = "nekobox"
    V2RAYN = "v2rayn"
    SHADOWROCKET = "shadowrocket"
    STREISAND = "streisand"


#: Маршруты, для которых на релее есть инбаунд (§3.1).
RELAY_SUPPORTED_PROTOS = frozenset({Proto.AWG, Proto.REALITY})

#: Куда протокол релея не доходит — немедленный код 3 (§3.1, B2).
RELAY_UNSUPPORTED_PROTOS = frozenset({Proto.XHTTP, Proto.HY})


# --- валидация имён (P3) -------------------------------------------------------

#: Имя профиля/stem AWG-конфига: без точек, слэшей и «..» (P3, §2.2, §6).
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_profile_name(name: str) -> str:
    """Проверить ``--profile`` и вернуть его же.

    Отвергает всё, что может стать частью пути: пустую строку, точки
    (``.``, ``..``, ``.hidden``, ``laptop.conf``), ``/``, ``\\`` и любой
    символ вне :data:`PROFILE_NAME_RE`. Возбуждает :class:`QrUsageError` (2).
    """
    value = (name or "").strip()
    if not value:
        raise QrUsageError("--profile: пустое имя недопустимо")
    if "." in value or "/" in value or "\\" in value or ".." in value:
        raise QrUsageError(
            f"--profile '{value}': точки и разделители пути запрещены "
            "(укажите имя профиля или stem AWG-конфига без .conf)")
    if not PROFILE_NAME_RE.match(value):
        raise QrUsageError(
            f"--profile '{value}': допустимы только латиница, цифры, '_' и '-' "
            "(первый символ — буква или цифра)")
    return value


# --- матрица --client × протокол (§3.3) ---------------------------------------

#: Дефолт ``--client auto`` по протоколу и ОС (§3.3).
_CLIENT_DEFAULTS: Dict[Proto, Dict[TargetOS, ClientName]] = {
    Proto.AWG: {os_: ClientName.AMNEZIAWG for os_ in TargetOS},
    Proto.REALITY: {
        TargetOS.ANDROID: ClientName.V2RAYNG,
        TargetOS.IOS: ClientName.STREISAND,
        TargetOS.WINDOWS: ClientName.V2RAYN,
        TargetOS.MACOS: ClientName.HIDDIFY,
        TargetOS.LINUX: ClientName.NEKOBOX,
    },
    Proto.XHTTP: {
        TargetOS.ANDROID: ClientName.V2RAYNG,
        TargetOS.IOS: ClientName.STREISAND,
        TargetOS.WINDOWS: ClientName.V2RAYN,
        TargetOS.MACOS: ClientName.HIDDIFY,
        TargetOS.LINUX: ClientName.HIDDIFY,
    },
    Proto.HY: {
        TargetOS.ANDROID: ClientName.V2RAYNG,
        TargetOS.IOS: ClientName.STREISAND,
        TargetOS.WINDOWS: ClientName.V2RAYN,
        TargetOS.MACOS: ClientName.HIDDIFY,
        TargetOS.LINUX: ClientName.NEKOBOX,
    },
}

#: Клиенты, совместимые с каждым протоколом (§3.3, код 3 при нарушении).
_PROTO_CLIENTS: Dict[Proto, frozenset] = {
    Proto.AWG: frozenset({ClientName.AMNEZIAWG}),
    Proto.REALITY: frozenset({
        ClientName.V2RAYNG, ClientName.V2RAYN, ClientName.HIDDIFY,
        ClientName.NEKOBOX, ClientName.SHADOWROCKET, ClientName.STREISAND,
    }),
}


def _proto_clients(proto: Proto) -> frozenset:
    if proto in _PROTO_CLIENTS:
        return _PROTO_CLIENTS[proto]
    # XHTTP и Hysteria2 используют тот же набор Xray/hysteria-клиентов.
    return _PROTO_CLIENTS[Proto.REALITY]


def resolve_client(proto: Proto, target_os: TargetOS, client_arg: str) -> str:
    """Разрешить ``--client`` в конкретное имя клиента (или код 2/3).

    Порядок проверок фиксирован (§3.3): неизвестное значение → 2;
    ``amneziavpn`` (v2) → 3; несовместимая пара ``--client`` × протокол → 3;
    ``auto`` → дефолт по ОС.
    """
    raw = (client_arg or ClientName.AUTO.value).strip().lower()
    try:
        client = ClientName(raw)
    except ValueError as exc:
        allowed = ", ".join(c.value for c in ClientName)
        raise QrUsageError(
            f"--client '{client_arg}': неизвестное значение (допустимо: {allowed})"
        ) from exc

    if client is ClientName.AMNEZIAVPN:
        raise QrUnsupportedComboError(
            "--client amneziavpn: контейнер vpn:// отложен до v2; "
            "используйте amneziawg (нативный .conf)")

    if client is not ClientName.AUTO and client not in _proto_clients(proto):
        raise QrUnsupportedComboError(
            f"--client {client.value} несовместим с протоколом {proto.value} "
            "(см. матрицу совместимости, SPEC §3.3)")

    if client is ClientName.AUTO:
        return _CLIENT_DEFAULTS[proto][target_os].value
    return client.value


# --- запрос и результат --------------------------------------------------------

@dataclass(frozen=True)
class QrRequest:
    """Полностью разобранный запрос ``vpn qr``.

    Создаётся CLI-слоем (WP-QR3) после проверки «ровно один флаг из каждой
    группы»; резолвер считает его доверенным.
    """

    route: Route
    proto: Proto
    target_os: TargetOS
    client: str = ClientName.AUTO.value
    profile: Optional[str] = None
    output_format: OutputFormat = OutputFormat.QR
    out_path: Optional[Path] = None
    force: bool = False
    show_uri: bool = False
    ascii_mode: bool = False
    invert: bool = False
    no_tty_check: bool = False


@dataclass
class ResolvedSource:
    """Нормализованный источник профиля для builders (WP-QR2).

    ``raw_awg_ini`` заполнен для ``--awg`` (текст выбранного .conf как в
    источнике), ``raw_profile`` — для vless/hysteria2 (запись из store
    вместе с share-ссылкой). `warnings` уходят в шапку и в JSON-контракт.
    """

    profile_name: str
    route: Route
    proto: Proto
    target_os: TargetOS
    client: str
    endpoint_host: str
    endpoint_port: int
    source: str  # "profile" | "awg"
    raw_profile: Optional[Dict[str, object]] = None
    raw_awg_ini: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def endpoint(self) -> str:
        host = self.endpoint_host
        if ":" in host and not host.startswith("["):  # IPv6-литерал
            host = f"[{host}]"
        return f"{host}:{self.endpoint_port}"
