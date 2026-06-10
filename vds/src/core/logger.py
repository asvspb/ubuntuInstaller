import logging
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

THEME = Theme({
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "red bold",
    "change": "green bold",
})

console = Console(theme=THEME)


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()

    console_handler = RichHandler(
        console=console,
        show_time=False,
        show_path=False,
        markup=True,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console_handler)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(file_handler)

    return logger


def rotate_log(log_file: str, max_size: int = 512 * 1024, keep_lines: int = 200) -> None:
    path = Path(log_file)
    if not path.exists():
        return
    if path.stat().st_size <= max_size:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[-keep_lines:]) + "\n", encoding="utf-8")
