import fcntl
import os
from typing import Optional
from pathlib import Path


class LockError(Exception):
    pass


class FileLock:
    def __init__(self, lock_file: str = "/var/run/vds.lock"):
        self.lock_file = lock_file
        self._fd: Optional[int] = None

    def acquire(self, nonblock: bool = True) -> bool:
        try:
            Path(self.lock_file).parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
            flags = fcntl.LOCK_EX
            if nonblock:
                flags |= fcntl.LOCK_NB
            fcntl.flock(self._fd, flags)
            return True
        except (OSError, IOError):
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except (OSError, IOError):
                pass
            self._fd = None

    def __enter__(self):
        if not self.acquire():
            raise LockError(f"Не удалось получить блокировку: {self.lock_file}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
