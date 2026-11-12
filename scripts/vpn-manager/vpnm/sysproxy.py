"""GNOME system proxy control via gsettings.

- ``vpn sysproxy on``  -> reset http/https/ftp host+port (socks-only manual
  configuration) + mode 'manual' + socks host 127.0.0.1 + port of the active
  profile;
- ``vpn sysproxy off`` -> reset http/https/ftp **and socks** host+port
  + mode 'none';
- ``vpn sysproxy status`` -> read the current values.

gsettings is invoked with an argument list; when the binary is missing
(non-GNOME system) a SysproxyError with a readable message is raised.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional, Tuple

from .runners import RunCmd, run_cmd

SCHEMA = "org.gnome.system.proxy"
SCHEMA_SOCKS = "org.gnome.system.proxy.socks"
SOCKS_HOST = "127.0.0.1"


class SysproxyError(Exception):
    """Raised when gsettings is unavailable or a call fails."""


def _run(runner: RunCmd, args: list) -> Tuple[int, str, str]:
    rc, out, err = runner(args)
    if rc == 127:
        raise SysproxyError(
            "gsettings not found: this system does not look like GNOME "
            "(install gsettings / dconf-cli or use another proxy method)")
    if rc != 0:
        raise SysproxyError(f"gsettings {' '.join(args[1:])} failed: "
                            f"{(err or out).strip()}")
    return rc, out, err


def _set(runner: RunCmd, schema: str, key: str, value: str) -> None:
    _run(runner, ["gsettings", "set", schema, key, value])


# Stale http/https/ftp entries (e.g. left over from another tool) make
# Chrome and other GNOME-aware apps route manual-mode traffic through a
# dead proxy even when the socks host is correct. Reset them so the
# configuration is unambiguously socks-only.
RESET_SCHEMA_KEYS: Tuple[Tuple[str, str], ...] = tuple(
    (f"{SCHEMA}.{schema}", key)
    for schema in ("http", "https", "ftp")
    for key in ("host", "port")
)


def _reset(runner: RunCmd, schema: str, key: str,
           warn: Callable[[str], None]) -> None:
    """Reset one gsettings key; a failure here is never fatal (warn only)."""
    rc, out, err = runner(["gsettings", "reset", schema, key])
    if rc != 0:
        warn(f"gsettings reset {schema}.{key} skipped: "
             f"{(err or out).strip()}")


def _reset_http_https_ftp(runner: RunCmd,
                          warn: Optional[Callable[[str], None]]) -> None:
    """Clear http/https/ftp host+port; individual failures are warn-level."""
    warn = warn if warn is not None else _default_warn
    for schema, key in RESET_SCHEMA_KEYS:
        _reset(runner, schema, key, warn)


def _get(runner: RunCmd, schema: str, key: str) -> str:
    _rc, out, _err = _run(runner, ["gsettings", "get", schema, key])
    return out.strip().strip("'\"")


def _default_runner() -> RunCmd:
    return run_cmd


def _default_warn(message: str) -> None:
    print(f"[warning] {message}", file=sys.stderr)


# Канонические исключения sysproxy: RU-сегмент + GLM-кластер + инструменты.
# Гарантия «GLM/ру-зоны всегда напрямую» при sysproxy on с ЛЮБЫМ протоколом
# (reality/xhttp/hysteria/cdn): сливаются в gsettings ignore-hosts
# идемпотентно при каждом on() (закрывает open-gap ручного списка 2026-11-11:
# 44 RU-домена без GLM — браузер гонял z.ai через туннель).
IGNORE_HOSTS_CANONICAL = [
    "localhost", "127.0.0.0/8", "::1",
    # RU-сегмент
    "yandex.ru", "ya.ru", "vk.com", "vk.ru", "mail.ru", "ok.ru",
    "gosuslugi.ru", "nalog.gov.ru", "sberbank.ru", "sber.ru",
    "tinkoff.ru", "alfabank.ru", "vtb.ru", "ozon.ru", "wildberries.ru",
    "avito.ru", "hh.ru", "rutube.ru", "dzen.ru", "kinopoisk.ru",
    "lenta.ru", "rbc.ru", "kommersant.ru", "habr.com",
    "sbermegamarket.ru", "mts.ru", "beeline.ru", "megafon.ru",
    "rostelecom.ru", "rt.ru", "samsung.com", "x5.ru",
    # GLM/ZCode-кластер — сессия AI не должна уходить в туннель
    "z.ai", "bigmodel.cn", "chatglm.cn", "zhipuai.cn",
    # инструменты (pypi душится через AWG)
    "pypi.org", "pythonhosted.org",
]


def on(socks_port: int, run: Optional[RunCmd] = None,
       warn: Optional[Callable[[str], None]] = None) -> None:
    """Enable a clean socks-only manual proxy at the active profile's port."""
    runner: RunCmd = run or _default_runner()
    _reset_http_https_ftp(runner, warn)
    _set(runner, SCHEMA, "mode", "manual")
    _set(runner, SCHEMA_SOCKS, "host", SOCKS_HOST)
    _set(runner, SCHEMA_SOCKS, "port", str(int(socks_port)))
    _merge_ignore_hosts(runner)


def _merge_ignore_hosts(runner: RunCmd) -> None:
    """Идемпотентно дополнить ignore-hosts каноническим списком.

    Читает текущий список, добавляет недостающее, пишет одним set. Существующие
    пользовательские записи не удаляются никогда.
    """
    _rc, out, _err = runner(["gsettings", "get", SCHEMA, "ignore-hosts"])
    current: list = []
    if _rc == 0:
        text = out.strip()
        if text.startswith("['") or text == "@as []":
            import ast
            try:
                parsed = ast.literal_eval(text) if text != "@as []" else []
                if isinstance(parsed, list):
                    current = [str(x) for x in parsed]
            except (ValueError, SyntaxError):
                current = []
    merged = list(dict.fromkeys(current + IGNORE_HOSTS_CANONICAL))
    if merged == current:
        return
    rendered = "[" + ", ".join(f"'{d}'" for d in merged) + "]"
    runner(["gsettings", "set", SCHEMA, "ignore-hosts", rendered])


def off(run: Optional[RunCmd] = None,
        warn: Optional[Callable[[str], None]] = None) -> None:
    """Disable the GNOME system proxy (mode 'none') and clear socks too.

    Socks host/port are reset as well: leaving them set produces a
    half-configured proxy (values present, mode 'none') that looks enabled
    but routes traffic direct — a known confusion source.
    """
    runner: RunCmd = run or _default_runner()
    warn_fn = warn if warn is not None else _default_warn
    _reset_http_https_ftp(runner, warn_fn)
    _reset(runner, SCHEMA_SOCKS, "host", warn_fn)
    _reset(runner, SCHEMA_SOCKS, "port", warn_fn)
    _set(runner, SCHEMA, "mode", "none")


def status(run: Optional[RunCmd] = None) -> Tuple[str, str, str]:
    """Return (mode, socks host, socks port) as currently configured."""
    runner: RunCmd = run or _default_runner()
    return (_get(runner, SCHEMA, "mode"), _get(runner, SCHEMA_SOCKS, "host"),
            _get(runner, SCHEMA_SOCKS, "port"))
