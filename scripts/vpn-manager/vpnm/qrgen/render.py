"""Рендер QR и вывод результата ``vpn qr`` (SPEC-20261118-vpn-qr §5, v1.1 §9.2).

Только системный ``qrencode`` (никаких Python-зависимостей): payload уходит
ему через stdin (``-r /dev/stdin``), поэтому секрет не попадает в argv.
Файлы пишутся атомарно с правами 0600; терминал проверяется на ширину до
печати, чтобы не выпустить «рваный» (нечитаемый) QR.

v1.1 (D2): канонический каталог артефактов ``ARTIFACTS_DIR`` (0700) с
авто-сохранением sanitized ``.conf``/``.uri``/``.png`` (0600, атомарная
перезапись при смене ``payload_sha256``) и masked-вывод ``vpn conf`` с
подсказкой ``--reveal``.
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
from typing import Callable, Dict, List, Optional

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


# --- артефакты (v1.1, D2/§9.2) --------------------------------------------------

#: env-переопределение каталога артефактов (изоляция тестов, AC-QR-9c).
ARTIFACTS_DIR_ENV = "VPN_MANAGER_ARTIFACTS_DIR"

#: Права каталога артефактов: PNG = секрет уровня .conf (§9.2).
ARTIFACTS_DIR_MODE = 0o700

#: WARNING, который печатается в stderr, когда ``--out`` лежит в каталоге,
#: читаемом другими пользователями (напр. ``/tmp``) — §9.2.
WORLD_READABLE_WARNING = (
    "WARNING: родительский каталог файла {path} читается другими "
    "пользователями (world-readable); файл записан с правами 0600, но "
    "рекомендуется приватный каталог")


def artifacts_dir() -> Path:
    """Каталог артефактов: ``~/.local/share/vpn-manager/artifacts`` (D2).

    Env ``VPN_MANAGER_ARTIFACTS_DIR`` переопределяет путь (читается при
    вызове, чтобы тесты изолировали прогон от живого ноутбука).
    """
    override = os.environ.get(ARTIFACTS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "vpn-manager" / "artifacts"


def ensure_artifacts_dir(path: Optional[Path] = None) -> Path:
    """Создать каталог артефактов с правами 0700 (идемпотентно)."""
    target = path if path is not None else artifacts_dir()
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(target, ARTIFACTS_DIR_MODE)
    return target


def artifact_stem(resolved: ResolvedSource) -> str:
    """Канонический stem артефакта (D3): ``<route>-<proto>-<os>-<profile>``."""
    return (f"{resolved.route.value}-{resolved.proto.value}-"
            f"{resolved.target_os.value}-{resolved.profile_name}")


def canonical_path(resolved: ResolvedSource, ext: str) -> Path:
    """``<ARTIFACTS_DIR>/<route>-<proto>-<os>-<profile>.<ext>`` (D3)."""
    clean = ext.lstrip(".")
    return ensure_artifacts_dir() / f"{artifact_stem(resolved)}.{clean}"


def save_payload_auto(resolved: ResolvedSource, built: BuiltPayload) -> Path:
    """Авто-сохранение sanitized payload (AWG → ``.conf``, URI → ``.uri``).

    Всегда атомарно перезаписывает канонический файл (§9.3: при изменении
    ``payload_sha256`` старый артефакт не может остаться рассинхронизирован-
    ным); права результата — 0600.
    """
    ext = "conf" if built.payload_format == "conf" else "uri"
    return write_text(canonical_path(resolved, ext), built.payload, force=True)


def save_png_auto(built: BuiltPayload, path: Path) -> Path:
    """Авто-сохранение PNG (0600, атомарная перезапись)."""
    return write_image(built, path, image="png", force=True)


def parent_world_readable(path: Path) -> bool:
    """True, когда родительский каталог ``--out`` читается «другими» (§9.2)."""
    try:
        return bool(path.parent.stat().st_mode & 0o004)
    except OSError:
        return False


# --- шапка, подсказка, JSON (§5.3, §5.4, §9.2) ---------------------------------

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


#: Подсказка под masked-выводом ``vpn conf`` (§9.2).
REVEAL_HINT = "Для сырого текста с секретами: добавьте --reveal"

#: WARNING в stderr при ``--reveal``/``--show-uri`` (§9.2).
REVEAL_WARNING = ("WARNING: вывод содержит секреты — не вставляйте "
                  "в логи/чаты/git")


def save_lines(source_conf: Optional[Path], saved_png: Optional[Path]) -> List[str]:
    """Строки путей v1.1: исходный conf + сохранённый PNG (§9.1)."""
    lines: List[str] = []
    if source_conf is not None:
        lines.append(f"Source conf: {source_conf}")
    if saved_png is not None:
        lines.append(f"Saved PNG: {saved_png} (0600)")
    return lines


def conf_header_lines(resolved: ResolvedSource, built: BuiltPayload, *,
                      conf_path: Optional[Path] = None,
                      png_path: Optional[Path] = None,
                      origin: Optional[str] = None) -> List[str]:
    """Шапка ``vpn conf`` (§9.2): оси | endpoint | sha256 | origin + пути."""
    lines = [
        f"[vpn conf] route={resolved.route.value} | proto={resolved.proto.value}"
        f" | os={resolved.target_os.value} | client={resolved.client}"
        f" | profile={resolved.profile_name} | endpoint={resolved.endpoint}"
        f" | {_short_sha(built)} | origin={origin or 'local'}",
    ]
    if conf_path is not None:
        lines.append(f"Conf: {conf_path} (0600)")
    if png_path is not None:
        lines.append(f"QR PNG: {png_path} (0600)")
    return lines


def conf_preview(resolved: ResolvedSource, built: BuiltPayload, *,
                 conf_path: Optional[Path] = None,
                 png_path: Optional[Path] = None,
                 origin: Optional[str] = None,
                 reveal: bool = False) -> str:
    """Текстовый вывод ``vpn conf``: шапка + тело через ``redact`` (§9.2).

    По умолчанию тело маскируется (``PrivateKey = <redacted>``, URI целиком);
    ``reveal=True`` печатает сырой payload (пригодно для ``| xclip``) —
    WARNING в stderr печатает CLI-слой.
    """
    lines = conf_header_lines(resolved, built, conf_path=conf_path,
                              png_path=png_path, origin=origin)
    lines += warning_lines(resolved)
    lines.append("")
    if reveal:
        lines.append(built.payload.rstrip("\n"))
    else:
        lines.append(redact_text(built.payload).rstrip("\n"))
        lines.append("")
        lines.append(REVEAL_HINT)
    return "\n".join(lines) + "\n"


def hint_line(resolved: ResolvedSource) -> str:
    """Подсказка импорта под целевую ОС/клиент (§5.3, OQ-QR-3)."""
    hint = _HINTS.get(resolved.client, "Импортируйте QR-код в клиенте")
    if resolved.target_os in _DESKTOP_OS:
        hint += " (или получите ссылку: --format uri)"
    return f"Hint ({resolved.target_os.value}/{resolved.client}): {hint}"


def warning_lines(resolved: ResolvedSource) -> List[str]:
    return [f"WARNING: {warning}" for warning in resolved.warnings]


def format_terminal(resolved: ResolvedSource, built: BuiltPayload,
                    qr_text: str, *, show_uri: bool = False,
                    saved_png: Optional[Path] = None,
                    source_conf: Optional[Path] = None) -> str:
    """Полный текстовый вывод ``--format qr`` (шапка + QR + опц. payload).

    v1.1 (§9.1): ``saved_png``/``source_conf`` добавляют в шапку пути
    авто-сохранённого PNG и исходного AWG-конфига (без секретов).
    """
    lines = header_lines(resolved, built) + save_lines(source_conf, saved_png)
    lines += warning_lines(resolved)
    lines.append(hint_line(resolved))
    lines.append("")
    lines.append(qr_text.rstrip("\n"))
    if show_uri:
        lines.append("")
        lines.append(built.payload.rstrip("\n"))
    return "\n".join(lines) + "\n"


def json_payload(resolved: ResolvedSource, built: BuiltPayload,
                 *, out_path: Optional[Path] = None,
                 show_uri: bool = False,
                 saved_png: Optional[Path] = None,
                 source_conf: Optional[Path] = None) -> Dict[str, object]:
    """Контракт ``vpnm.qr/1`` (§5.4); ``payload`` — только по ``--show-uri``.

    v1.1 (аддитивно): ``png_path`` (авто-сохранение) и ``source_conf``.
    """
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
        "png_path": str(saved_png) if saved_png else None,
        "source_conf": str(source_conf) if source_conf else None,
        "warnings": list(resolved.warnings),
    }


def json_text(resolved: ResolvedSource, built: BuiltPayload,
              *, out_path: Optional[Path] = None,
              show_uri: bool = False,
              saved_png: Optional[Path] = None,
              source_conf: Optional[Path] = None) -> str:
    return json.dumps(
        json_payload(resolved, built, out_path=out_path, show_uri=show_uri,
                     saved_png=saved_png, source_conf=source_conf),
        indent=2, ensure_ascii=False) + "\n"


# --- точка входа ---------------------------------------------------------------

def emit(resolved: ResolvedSource, built: BuiltPayload,
         request: QrRequest, *,
         saved_png: Optional[Path] = None,
         source_conf: Optional[Path] = None,
         save_png_cb: Optional["Callable[[], Path]"] = None) -> Optional[str]:
    """Выполнить формат запроса; вернуть текст для stdout (или ``None``).

    Файловые форматы (``uri --out``, ``png``, ``svg``, ``json --out``) пишут
    файл и возвращают короткую строку-подтверждение; ``qr``/``uri``/``json``
    без ``--out`` возвращают готовый текст.

    v1.1: ``save_png_cb`` — колбэк авто-сохранения PNG для ``--format qr``;
    вызывается ПОСЛЕ TTY-guard (неудачный рендер не оставляет файл).
    """
    fmt = request.output_format

    if fmt is OutputFormat.QR:
        qr_text = render_qr_text(built, ascii_mode=request.ascii_mode,
                                 invert=request.invert)
        ensure_terminal_width(qr_text, no_tty_check=request.no_tty_check)
        if save_png_cb is not None:
            saved_png = save_png_cb()
        return format_terminal(resolved, built, qr_text,
                               show_uri=request.show_uri,
                               saved_png=saved_png, source_conf=source_conf)

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
                          show_uri=request.show_uri, saved_png=saved_png,
                          source_conf=source_conf),
                force=request.force)
            return f"записано: {path} (0600)"
        return json_text(resolved, built, show_uri=request.show_uri,
                         saved_png=saved_png, source_conf=source_conf)

    raise QrRenderError(f"--format {fmt}: неизвестный формат вывода")
