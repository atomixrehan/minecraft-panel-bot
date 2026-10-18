import sys

from .config import Config


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
    if "--install" in args:          # finish/repair the server download, no questions
        if not cfg.is_configured():
            raise SystemExit("Not configured yet – run ./setup.sh first.")
        from .wizard import install
        ok = install(cfg)
        raise SystemExit(0 if ok else 1)

    if "--setup" in args:            # change settings (menu), or continue an unfinished first setup. Never starts the bot.
        from .lock import is_running
        if is_running():
            raise SystemExit("The bot is running. Stop it first (./stop.sh), then run --setup – "
                             "otherwise it would overwrite your changes.")
        if not sys.stdin.isatty():
            raise SystemExit("--setup needs an interactive terminal.")
        from .wizard import reconfigure, run
        if cfg.is_configured():
            reconfigure(cfg)
        else:
            run(cfg)
        return

    if not cfg.is_configured():      # very first start: ask once (resumes if interrupted), then run
        if not sys.stdin.isatty():
            raise SystemExit("Not configured yet. Run ./setup.sh (or ./start.sh) in a terminal first.")
        from .wizard import run
        run(cfg)
        if not cfg.is_configured():
            raise SystemExit(1)

    from .lock import acquire
    acquire()
    from .bot import main as run_bot
    run_bot(cfg)


if __name__ == "__main__":
    main()
