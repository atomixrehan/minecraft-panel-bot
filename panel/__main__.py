import os
import sys

from .config import DATA_DIR, Config

_lock_file = None  # keep a reference so the lock lives as long as the process


def acquire_lock() -> None:
    """Only ONE bot per install folder (two copies would each post their own panel)."""
    global _lock_file
    try:
        import fcntl
    except ImportError:  # Windows
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _lock_file = open(DATA_DIR / "bot.lock", "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("Another copy of the panel bot is already running from this folder.\n"
                         "Stop it first (systemctl stop minecraft-panel, or ./kill.sh) and try again.")
    _lock_file.write(str(os.getpid()))
    _lock_file.flush()


def main() -> None:
    cfg = Config()
    args = sys.argv[1:]
    if "--cleanup" in args:          # used by setup.sh / kill.sh: delete the panel message from Discord
        from .wizard import cleanup
        return cleanup(cfg)
    if "--update" in args:           # used by setup.sh: read-only summary after the code was updated
        if not cfg.is_configured():
            raise SystemExit("Nothing to update – not configured yet.")
        from .wizard import update
        return update(cfg)
    if "--update-server" in args:    # OPTIONAL: newest Paper build + plugins (touches server/)
        if not cfg.is_configured():
            raise SystemExit("Nothing to update – not configured yet.")
        from .wizard import update_server
        return update_server(cfg)
    if "--setup" in args or not cfg.is_configured():
        if not sys.stdin.isatty():
            raise SystemExit("Not configured yet. Run ./setup.sh (or ./start.sh --setup) in a terminal first.")
        from .wizard import run
        run(cfg)
        if not cfg.is_configured():
            raise SystemExit(1)
    acquire_lock()
    from .bot import main as run_bot
    run_bot(cfg)


if __name__ == "__main__":
    main()
