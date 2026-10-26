"""Thread + cross-process lock for vault read-modify-write cycles (Core Memory, schedule).
Streamlit sessions share a process, but a cron/launchd `run_due` is a second one."""

import fcntl
import os
import threading
from contextlib import contextmanager

_THREAD = {}
_GUARD = threading.Lock()


@contextmanager
def locked(name: str):
    with _GUARD:
        tl = _THREAD.setdefault(name, threading.RLock())
    with tl:
        d = os.path.join(os.path.expanduser("~"), ".personal_ai")
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(d, f"{name}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
