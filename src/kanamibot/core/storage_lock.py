from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_mutex = threading.RLock()
_state = threading.local()


@contextmanager
def storage_lock(root: Path):
    key = os.path.normcase(str(root.resolve()))
    with _mutex:
        held = getattr(_state, "held", set())
        if key in held:
            yield
            return
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".storage.lock").open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                if handle.seek(0, 2) == 0:
                    handle.write(b"\0")
                    handle.flush()
                deadline = time.monotonic() + 30
                while True:
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("图库正在被其他进程使用，请稍后重试") from None
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _state.held = held | {key}
            try:
                yield
            finally:
                _state.held = held
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
