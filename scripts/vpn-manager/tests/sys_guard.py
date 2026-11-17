"""Global anti-leak guard: тесты не должны дёргать живую систему.

Инцидент 2026-11-18 (journalctl, подтверждён): прогон ``pytest`` реально
выполнил ``sudo -n bash 17_xray-split.sh --off`` и
``sudo -n systemctl stop vpn-awg-split@relay.service`` — то есть **снёс
живой VPN-туннель** из юнит-теста. Нарушен AGENTS §6 («в тестах не трогать
production-состояние: только моки, temp-каталоги»).

Этот модуль ставит глобальный предохранитель на ``subprocess.run`` и
``subprocess.Popen``: любой вызов с системно-мутирующей командой падает
тестом с понятным сообщением, а не меняет машину разработчика.

Правило (по первому токену после префикса ``sudo``/``sudo -n``):

- первый токен ∈ ``FORBIDDEN_BINARIES`` → блок;
- команда содержит путь ``17_xray-split.sh`` → блок (кроме ``bash -n`` —
  синтаксическая проверка скрипта ничего не исполняет).

Осознанный обход — только явным env ``VPNM_TEST_ALLOW_SYSTEM=1``
(по умолчанию запрещено). env читается на каждом вызове, поэтому тест может
включить обход через ``monkeypatch.setenv``.

Реальные callables держим ссылками (``_REAL_RUN``/``_REAL_POPEN``), чтобы
тест-доказательство могло подменить их и проверить «пропускает — не падая».
"""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Any, Sequence, Union

#: Бинарники, которые меняют (или по контракту могут менять) систему.
FORBIDDEN_BINARIES = frozenset({
    "systemctl",
    "awg",
    "awg-quick",
    "nft",
    "ip",
    "resolvectl",
    "dnsmasq",
    "cloudflared-proxy-dns",
})

#: Корневой скрипт split-схемы: запуск == перестройка маршрутов/DNS.
SPLIT_SCRIPT_MARKER = "17_xray-split.sh"

#: Мутирующие verb'ы под sudo: тест не должен писать в корневые пути.
#: Инцидент 2026-11-18 включал `sudo -n rm ...` — rm не в FORBIDDEN_BINARIES,
#: поэтому ловим его отдельно (только с префиксом sudo, где он = root-write).
SUDO_MUTATION_VERBS = frozenset({
    "rm", "mv", "cp", "install", "chmod", "chown", "tee", "mkdir", "ln", "dd",
})

#: Env-обход для осознанного «живого» прогона.
ALLOW_ENV = "VPNM_TEST_ALLOW_SYSTEM"

#: Префиксные токены sudo, которые не считаются «командой».
_SUDO_PREFIX = frozenset({"sudo", "-n", "--"})

_REAL_RUN = subprocess.run
_REAL_POPEN = subprocess.Popen


class SystemCommandBlocked(RuntimeError):
    """Тест пытался выполнить систему-мутирующую команду."""


def _as_tokens(cmd: Union[str, Sequence[Any], None]) -> list[str]:
    """Нормализовать argv (list/tuple/str) в список строк."""
    if cmd is None:
        return []
    if isinstance(cmd, (str, bytes)):
        text = cmd.decode() if isinstance(cmd, bytes) else cmd
        return shlex.split(text)
    return [str(part) for part in cmd]


def _effective_tokens(tokens: list[str]) -> list[str]:
    """Снять ведущий префикс sudo, вернуть «настоящую» команду."""
    idx = 0
    while idx < len(tokens) and tokens[idx] in _SUDO_PREFIX:
        idx += 1
    return tokens[idx:]


def check_command(cmd: Union[str, Sequence[Any], None]) -> None:
    """Бросить :class:`SystemCommandBlocked`, если команда трогает систему.

    Ничего не исполняет — только инспекция argv. При ``VPNM_TEST_ALLOW_SYSTEM=1``
    всегда возвращает управление (осознанный «живой» прогон).
    """
    if os.environ.get(ALLOW_ENV) == "1":
        return
    tokens = _as_tokens(cmd)
    effective = _effective_tokens(tokens)
    if not effective:
        return
    binary = os.path.basename(effective[0])
    if binary in FORBIDDEN_BINARIES:
        raise SystemCommandBlocked(
            "Тест пытается выполнить системно-мутирующую команду "
            f"{effective[0]!r} (argv: {tokens!r}). "
            "Юнит-тесты не должны трогать живую систему (AGENTS §6): "
            "замокайте run_cmd/модуль. Осознанный обход: "
            f"{ALLOW_ENV}=1."
        )
    joined = " ".join(tokens)
    if tokens and tokens[0] == "sudo" and binary in SUDO_MUTATION_VERBS:
        raise SystemCommandBlocked(
            f"Тест пытается выполнить `sudo {effective[0]}` — запись в "
            f"корневые пути (argv: {tokens!r}). Юнит-тесты не должны менять "
            "систему (AGENTS §6). Осознанный обход: "
            f"{ALLOW_ENV}=1."
        )
    if SPLIT_SCRIPT_MARKER in joined:
        # `bash -n <script>` — только проверка синтаксиса, ничего не исполняет.
        syntax_only = (binary in ("bash", "sh") and "-n" in effective[1:])
        if not syntax_only:
            raise SystemCommandBlocked(
                f"Тест пытается запустить {SPLIT_SCRIPT_MARKER} "
                f"(argv: {tokens!r}) — это перестраивает маршруты/DNS на живой "
                "машине. Замокайте xsplit. Осознанный обход: "
                f"{ALLOW_ENV}=1."
            )


def guarded_run(*args: Any, **kwargs: Any) -> Any:
    """Замена ``subprocess.run``: сперва проверка argv, затем реальный вызов."""
    cmd = args[0] if args else kwargs.get("args")
    check_command(cmd)
    return _REAL_RUN(*args, **kwargs)


def guarded_popen(*args: Any, **kwargs: Any) -> Any:
    """Замена ``subprocess.Popen``: та же проверка argv."""
    cmd = args[0] if args else kwargs.get("args")
    check_command(cmd)
    return _REAL_POPEN(*args, **kwargs)


def install(monkeypatch: Any) -> None:
    """Поставить предохранитель на процессы (для autouse-фикстуры)."""
    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
