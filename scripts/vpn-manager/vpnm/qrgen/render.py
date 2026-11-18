"""Рендер QR и вывод результата ``vpn qr`` (SPEC-20261118-vpn-qr §5).

Только системный ``qrencode`` (никаких Python-зависимостей): payload уходит
ему через stdin (``-r /dev/stdin``), поэтому секрет не попадает в argv.
Файлы пишутся атомарно с правами 0600; терминал проверяется на ширину до
печати, чтобы не выпустить «рваный» (нечитаемый) QR.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from .builders import BuiltPayload
from .models import (
    OutputFormat,
    QrRenderError,
    QrRequest,
    ResolvedSource,
    TargetOS,
)
from .redact import redact_text, scrub_secrets

#: ANSI escape-последовательности, которые qrencode добавляет в ANSIUTF8*.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: Подсказки импорта по клиенту (§5.3); ключ — имя клиента из матрицы §3.3.
_HINTS: Dict[str, str] = {
    "amneziawg": "Откройте AmneziaWG → «+» → Сканировать QR-код",
    "v2rayng": "v2rayNG → «+» → Импорт из QR-кода",
    "v2rayn": "v2rayN → Серверы → Сканировать QR-код с экрана",
    "hiddify": "Hiddify → «+» → Добавить из QR-кода",
    "nekobox": "NekoBox → «+» → Импорт из QR-кода",
    "shadowrocket": "Shadowrocket → «+» → Сканировать QR-код",
    "streisand": "Streisand → «+» → Scan QR code",
}

_DESKTOP_OS = frozenset({TargetOS.WINDOWS, TargetOS.MACOS, TargetOS.LINUX})


# --- геометрия (R5) -----------------------------------------------------------

def visible_qr_width(qr_output: str) -> int:
    """Видимая ширина QR в колонках (ANSI-коды снимаются, §5.2)."""
    lines = [ANSI_RE.sub("", line.rstrip("\r\n"))
             for line in qr_output.splitlines()]
    return max((len(line) for line in lines if line), default=0)


def ensure_terminal_width(qr_output: str, *, no_tty_check: bool) -> None:
    """Не печатать QR, который не влезает в окно (код 5, §5.2)."""
    if no_tty_check or not sys.stdout.isatty():
        return
    width = visible_qr_width(qr_output)
    columns = shutil.get_terminal_size().columns
    if width > columns:
        raise QrRenderError(
            f"терминал слишком узкий: нужно ≥ {width} колонок, сейчас {columns}. "
            "Увеличьте окно, уменьшите шрифт или используйте --format uri / "
            "--format png --out <file> / --no-tty-check")


# --- qrencode -----------------------------------------------------------------

def qr_type(*, ascii_mode: bool, invert: bool) -> str:
    """Тип вывода qrencode (§5.1): инверсия — суффикс ``i``, не флаг ``-i``."""
    if ascii_mode:
        return "ASCIIi" if invert else "ASCII"
    return "ANSIUTF8i" if invert else "ANSIUTF8"


def build_args(built: BuiltPayload, *, ascii_mode: bool = False,
               invert: bool = False, binary: str = "qrencode",
               out: Optional[Path] = None,
               image: Optional[str] = None) -> List[str]:
    """Аргументы qrencode для терминала/PNG/SVG (§5.1, таблица).

    ``-r /dev/stdin`` присутствует всегда; ``-i`` не передаётся никогда.
    """
    if image == "png":
        args = [binary, "-t", "PNG", "-s", "8", "-m", "4", "-l", built.qr_ecc,
                "-o", str(out)]
    elif image == "svg":
        args = [binary, "-t", "SVG", "-m", "4", "-l", built.qr_ecc,
                "-o", str(out)]
    else:
        args = [binary, "-t", qr_type(ascii_mode=ascii_mode, invert=invert),
                "-m", "4", "-l", built.qr_ecc, "-o", "-"]
    return args + ["-r", "/dev/stdin"]


def _ensure_qrencode() -> str:
    binary = shutil.which("qrencode")
    if not binary:
        raise QrRenderError(
            "qrencode не установлен в системе — установите: sudo apt install qrencode")
    return binary


def _failure_detail(proc: "subprocess.CompletedProcess[str]",
                    payload: str) -> str:
    lines = (proc.stderr or "").strip().splitlines()
    reason = lines[-1] if lines else f"exit {proc.returncode}"
    return scrub_secrets([payload], redact_text(reason))


def _run(cmd: List[str], payload: str) -> "subprocess.CompletedProcess[str]":
    try:
        proc = subprocess.run(cmd, input=payload, text=True,
                              capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QrRenderError(f"qrencode: {exc}") from exc
    if proc.returncode != 0:
        raise QrRenderError(f"qrencode: {_failure_detail(proc, payload)}")
    return proc


def render_qr_text(built: BuiltPayload, *, ascii_mode: bool = False,
                   invert: bool = False) -> str:
    """QR-матрица в терминал (ANSIUTF8/ANSIUTF8i/ASCII/ASCIIi)."""
    binary = _ensure_qrencode()
    cmd = build_args(built, ascii_mode=ascii_mode, invert=invert, binary=binary)
    return _run(cmd, built.payload).stdout


# --- атомарная запись 0600 (§1.3) ---------------------------------------------

def _replace_atomically(tmp_name: str, path: Path) -> Path:
    os.chmod(tmp_name, 0o600)
    os.replace(tmp_name, path)
    os.chmod(path, 0o600)  # гарантия и при перезаписи (--force)
    return path


def write_text(path: Path, text: str, *, force: bool = False) -> Path:
    """Записать текст (URI/INI/JSON) атомарно с правами 0600."""
    if path.exists() and not force:
        raise QrRenderError(f"файл {path} уже существует — используйте --force")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=path.name + ".", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            return _replace_atomically(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise QrRenderError(f"не удалось записать {path}: {exc}") from exc


def write_image(built: BuiltPayload, path: Path, *, image: str,
                force: bool = False) -> Path:
    """PNG/SVG через qrencode с атомарной подменой и правами 0600."""
    binary = _ensure_qrencode()
    if path.exists() and not force:
        raise QrRenderError(f"файл {path} уже существует — используйте --force")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=path.name + ".", suffix=".tmp")
        os.close(fd)
        try:
            cmd = build_args(built, binary=binary, out=Path(tmp_name), image=image)
            _run(cmd, built.payload)
            if not os.path.getsize(tmp_name):
                raise QrRenderError(f"qrencode вернул пустой файл для {path.name}")
            return _replace_atomically(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise QrRenderError(f"не удалось записать {path}: {exc}") from exc


# --- шапка, подсказка, JSON (§5.3, §5.4) --------------------------------------

def _short_sha(built: BuiltPayload) -> str:
    digest = built.payload_sha256.split(":", 1)[-1]
    return f"sha256:{digest[:4]}…{digest[-4:]}"


def header_lines(resolved: ResolvedSource, built: BuiltPayload) -> List[str]:
    """Безопасная шапка: ни одного секрета (§5.3)."""
    return [
        f"[vpn qr] profile={resolved.profile_name} | route={resolved.route.value}"
        f" | proto={resolved.proto.value} | os={resolved.target_os.value}"
        f" | client={resolved.client}",
        f"Endpoint: {resolved.endpoint} | Payload: {built.payload_bytes} B"
        f" ({_short_sha(built)}) | ECC: {built.qr_ecc}",
    ]


def hint_line(resolved: ResolvedSource) -> str:
    """Подсказка импорта под целевую ОС/клиент (§5.3, OQ-QR-3)."""
    hint = _HINTS.get(resolved.client, "Импортируйте QR-код в клиенте")
    if resolved.target_os in _DESKTOP_OS:
        hint += " (или получите ссылку: --format uri)"
    return f"Hint ({resolved.target_os.value}/{resolved.client}): {hint}"


def warning_lines(resolved: ResolvedSource) -> List[str]:
    return [f"WARNING: {warning}" for warning in resolved.warnings]


def format_terminal(resolved: ResolvedSource, built: BuiltPayload,
                    qr_text: str, *, show_uri: bool = False) -> str:
    """Полный текстовый вывод ``--format qr`` (шапка + QR + опц. payload)."""
    lines = header_lines(resolved, built) + warning_lines(resolved)
    lines.append(hint_line(resolved))
    lines.append("")
    lines.append(qr_text.rstrip("\n"))
    if show_uri:
        lines.append("")
        lines.append(built.payload.rstrip("\n"))
    return "\n".join(lines) + "\n"


def json_payload(resolved: ResolvedSource, built: BuiltPayload,
                 *, out_path: Optional[Path] = None,
                 show_uri: bool = False) -> Dict[str, object]:
    """Контракт ``vpnm.qr/1`` (§5.4); ``payload`` — только по ``--show-uri``."""
    return {
        "schema": "vpnm.qr/1",
        "profile": resolved.profile_name,
        "route": resolved.route.value,
        "proto": resolved.proto.value,
        "os": resolved.target_os.value,
        "client": resolved.client,
        "endpoint": resolved.endpoint,
        "payload_format": built.payload_format,
        "payload": built.payload if show_uri else None,
        "payload_bytes": built.payload_bytes,
        "payload_sha256": built.payload_sha256,
        "qr_ecc": built.qr_ecc,
        "out_path": str(out_path) if out_path else None,
        "warnings": list(resolved.warnings),
    }


def json_text(resolved: ResolvedSource, built: BuiltPayload,
              *, out_path: Optional[Path] = None,
              show_uri: bool = False) -> str:
    return json.dumps(
        json_payload(resolved, built, out_path=out_path, show_uri=show_uri),
        indent=2, ensure_ascii=False) + "\n"


# --- точка входа ---------------------------------------------------------------

def emit(resolved: ResolvedSource, built: BuiltPayload,
         request: QrRequest) -> Optional[str]:
    """Выполнить формат запроса; вернуть текст для stdout (или ``None``).

    Файловые форматы (``uri --out``, ``png``, ``svg``, ``json --out``) пишут
    файл и возвращают короткую строку-подтверждение; ``qr``/``uri``/``json``
    без ``--out`` возвращают готовый текст.
    """
    fmt = request.output_format

    if fmt is OutputFormat.QR:
        qr_text = render_qr_text(built, ascii_mode=request.ascii_mode,
                                 invert=request.invert)
        ensure_terminal_width(qr_text, no_tty_check=request.no_tty_check)
        return format_terminal(resolved, built, qr_text,
                               show_uri=request.show_uri)

    if fmt is OutputFormat.URI:
        if request.out_path is not None:
            path = write_text(request.out_path, built.payload,
                              force=request.force)
            return f"записано: {path} (0600)"
        return built.payload + "\n"

    if fmt in (OutputFormat.PNG, OutputFormat.SVG):
        if request.out_path is None:  # защита: CLI уже отдаёт код 2
            raise QrRenderError("--format png|svg требует --out <path>")
        image = "png" if fmt is OutputFormat.PNG else "svg"
        path = write_image(built, request.out_path, image=image,
                           force=request.force)
        return f"записано: {path} (0600)"

    if fmt is OutputFormat.JSON:
        if request.out_path is not None:
            path = write_text(
                request.out_path,
                json_text(resolved, built, out_path=request.out_path,
                          show_uri=request.show_uri),
                force=request.force)
            return f"записано: {path} (0600)"
        return json_text(resolved, built, show_uri=request.show_uri)

    raise QrRenderError(f"--format {fmt}: неизвестный формат вывода")
