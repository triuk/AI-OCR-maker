"""Exclusive, non-blocking file locks that the OS releases even after a crash."""
from __future__ import annotations

import errno
import fcntl
from pathlib import Path


class FileLock:
    """Held by at most one open handle, also within one process (flock locks the open file description)."""

    def __init__(self, path: Path, message: str = "Soubor je uzamčený jiným během aplikace."):
        self.path = path
        self.message = message
        self.handle = None

    def acquire(self) -> "FileLock":
        handle = self.path.open("a+b")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise BlockingIOError(self.message) from exc
            raise
        self.handle = handle
        return self

    def release(self) -> None:
        if self.handle:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *args) -> None:
        self.release()
