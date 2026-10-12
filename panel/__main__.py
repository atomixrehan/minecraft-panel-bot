import sys

from .config import Config


def main() -> None:
    cfg = Config()
    args = sys.argv[1:]
    if "--cleanup" in args:          # used by setup.sh before a clean reinstall
        from .wizard import cleanup
        return cleanup(cfg)
    if "--update" in args:           # used by setup.sh "Update" option
        if not cfg.is_configured():
            raise SystemExit("Nothing to update – not configured yet.")
        from .wizard import update
        return update(cfg)
    if "--setup" in args or not cfg.is_configured():
        if not sys.stdin.isatty():
            raise SystemExit("Not configured yet. Run ./setup.sh (or ./start.sh --setup) in a terminal first.")
        from .wizard import run
        run(cfg)
        if not cfg.is_configured():
            raise SystemExit(1)
    from .bot import main as run_bot
    run_bot(cfg)


if __name__ == "__main__":
    main()
