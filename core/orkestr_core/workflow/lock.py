"""The run lock `.orkestr/core.lock`: at most one engine per project (PHASE3 §7.3, §10)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..domain.errors import OrkestrError, now


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_lock(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def lock_holder_alive(path: Path) -> bool:
    info = read_lock(path)
    return bool(info and pid_alive(int(info.get("pid", 0))))


class RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = read_lock(self.path)
                if info and pid_alive(int(info.get("pid", 0))):
                    raise OrkestrError(
                        f"Another orkestr-core run is active (pid {info.get('pid')}, since {info.get('started_at')}). "
                        "Use `orkestr-core pause` or `orkestr-core cancel` to stop it.",
                        "E_LOCKED",
                    ) from None
                self.path.unlink(missing_ok=True)  # stale: its process is gone
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "started_at": now()}, f)
            self.held = True
            return self
        raise OrkestrError("Could not acquire the run lock.", "E_LOCKED")

    def release(self):
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
