"""One bot per install folder, and a way to ask 'is the bot running?' (used before editing config.json)."""
from __future__ import annotations

import os

from .config import DATA_DIR

_held = None  # keep the file object alive for the life of the process


def acquire() -> None:
    global _held
    try:
        import fcntl
    except ImportError:  # Windows: no locking
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = open(DATA_DIR / "bot.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("Another copy of the panel bot is already running from this folder.\n"
                         "Stop it first (./stop.sh, or systemctl stop minecraft-panel) and try again.")
    f.write(str(os.getpid()))
    f.flush()
    _held = f


def is_running() -> bool:
    try:
        import fcntl
    except ImportError:
        return False
    path = DATA_DIR / "bot.lock"
    if not path.exists():
        return False
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False
