"""QR в терминале через qrencode; секрет передаётся только через stdin.

`qrencode` не умеет читать stdin напрямую, но открывает `/dev/stdin` как
файл (`-r /dev/stdin`) — поэтому share-ссылка не попадает в argv/логи.
"""

from __future__ import annotations

import shutil
import subprocess


class QrError(Exception):
    """qrencode отсутствует или не смог построить QR."""


def render(text: str, timeout: float = 15.0) -> str:
    """ANSIUTF8-QR для терминала (белые блоки на чёрном)."""
    binary = shutil.which("qrencode")
    if not binary:
        raise QrError(
            "qrencode не найден — установите: sudo apt install qrencode")
    try:
        proc = subprocess.run(
            [binary, "-t", "ANSIUTF8", "-m", "2", "-o", "-", "-r", "/dev/stdin"],
            input=text, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QrError(f"qrencode: {exc}") from exc
    if proc.returncode != 0:
        lines = (proc.stderr or "").strip().splitlines()
        reason = lines[-1] if lines else f"exit {proc.returncode}"
        raise QrError(f"qrencode: {reason}")
    return proc.stdout
