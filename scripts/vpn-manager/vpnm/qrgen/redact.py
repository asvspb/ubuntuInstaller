"""Санитизация текстов: секреты не должны попасть в ошибки и шапку (§1.3).

Модуль намеренно консервативен: маскируются и «ключ = значение» из INI,
и параметры share-ссылок, и сами ссылки целиком. Ошибиться в сторону
«слишком много скрыто» здесь безопасно; обратное — утечка.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Ключи INI/TOML, значения которых являются секретами.
SECRET_INI_KEYS = (
    "privatekey",
    "presharedkey",
    "password",
    "obfs-password",
    "obfspassword",
    "secret",
    "token",
)

#: Query-параметры share-ссылок, значения которых являются секретами.
SECRET_QUERY_KEYS = ("pbk", "sid", "password", "obfs-password", "auth", "token")

_INI_RE = re.compile(
    r"^(?P<lead>\s*(?P<key>[A-Za-z0-9_.-]+)\s*=\s*)(?P<value>\S.*)$")
_QUERY_RE = re.compile(
    r"(?P<lead>[?&](?P<key>[A-Za-z0-9_.-]+)=)(?P<value>[^&#\s]*)")
_URI_RE = re.compile(r"\b(?:vless|vmess|trojan|ss|hysteria2|hy2)://\S+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
#: base64url-ключи (Reality pbk, 32 байта) — 43 символа.
_B64KEY_RE = re.compile(r"\b[A-Za-z0-9_-]{43}={0,2}\b")

REDACTED = "<redacted>"


def _redact_ini_line(line: str) -> str:
    match = _INI_RE.match(line)
    if not match:
        return line
    if match.group("key").lower() not in SECRET_INI_KEYS:
        return line
    return f"{match.group('lead')}{REDACTED}"


def _redact_query(match: "re.Match[str]") -> str:
    key = match.group("key").lower()
    if key in SECRET_QUERY_KEYS and match.group("value"):
        return f"{match.group('lead')}{REDACTED}"
    return match.group(0)


def redact_text(text: str) -> str:
    """Убрать секреты из произвольного текста (ошибки, логи, шапка)."""
    if not text:
        return text
    out_lines = []
    for line in text.splitlines():
        line = _URI_RE.sub(lambda m: f"{m.group(0).split('://', 1)[0]}://{REDACTED}", line)
        line = _QUERY_RE.sub(_redact_query, line)
        line = _redact_ini_line(line)
        line = _UUID_RE.sub(REDACTED, line)
        line = _B64KEY_RE.sub(REDACTED, line)
        out_lines.append(line)
    result = "\n".join(out_lines)
    if text.endswith("\n"):
        result += "\n"
    return result


def redact_error(exc: BaseException) -> str:
    """Текст исключения без секретов (для stderr и ``QrError``)."""
    return redact_text(str(exc))


def scrub_secrets(values: Iterable[str], text: str) -> str:
    """Вырезать известные значения секретов из текста (последний рубеж).

    Используется, когда точное значение известно (например, uuid выбранного
    профиля) — обычные регэкспы могут его не узнать.
    """
    result = text
    for value in values:
        if value and len(value) >= 8:
            result = result.replace(value, REDACTED)
    return result
