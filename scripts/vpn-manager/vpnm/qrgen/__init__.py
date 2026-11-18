"""`vpn qr` — офлайн-генерация клиентских профилей и QR (SPEC-20261118-vpn-qr).

WP-QR1 (этот пакет): модели, санитизация секретов и резолвер источников.
Генерация payload (``builders``), рендеринг (``render``) и Typer-обвязка
(``cli``) приходят в WP-QR2/WP-QR3.

Пакет изолирован от ``vpnm.guard`` и не трогает существующий ``vpnm/qr.py``
(терминальный рендерер для ``vpn profile qr`` / ``vpn amneziawg qr``).
"""

from __future__ import annotations

from .builders import (
    ECC_SMALL_MAX_BYTES,
    BuiltPayload,
    build_payload,
)
from .render import (
    emit,
    format_terminal,
    json_payload,
    render_qr_text,
    visible_qr_width,
)
from .models import (
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    EXIT_RENDER_ERROR,
    EXIT_UNSUPPORTED_COMBO,
    EXIT_USAGE,
    ClientName,
    OutputFormat,
    Proto,
    QrConfigError,
    QrError,
    QrRenderError,
    QrRequest,
    QrUnsupportedComboError,
    QrUsageError,
    ResolvedSource,
    Route,
    TargetOS,
    resolve_client,
    validate_profile_name,
)
from .resolver import resolve

__all__ = [
    "ECC_SMALL_MAX_BYTES",
    "BuiltPayload",
    "build_payload",
    "emit",
    "format_terminal",
    "json_payload",
    "render_qr_text",
    "visible_qr_width",
    "EXIT_CONFIG_ERROR",
    "EXIT_OK",
    "EXIT_RENDER_ERROR",
    "EXIT_UNSUPPORTED_COMBO",
    "EXIT_USAGE",
    "ClientName",
    "OutputFormat",
    "Proto",
    "QrConfigError",
    "QrError",
    "QrRenderError",
    "QrRequest",
    "QrUnsupportedComboError",
    "QrUsageError",
    "ResolvedSource",
    "Route",
    "TargetOS",
    "resolve",
    "resolve_client",
    "validate_profile_name",
]
