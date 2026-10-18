#!/usr/bin/env bash
# ./start.sh          run in this terminal (Ctrl+C stops it)
# ./start.sh --bg     run in the background (keeps running after you close the terminal); logs: logs/bot.log
# ./stop.sh           stop it (and the Minecraft server) safely
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if [ ! -x .venv/bin/python ]; then echo "Run ./setup.sh first."; exit 1; fi

if [ "${1:-}" = "--bg" ] || [ "${1:-}" = "--background" ]; then
  . ./lib.sh
  [ -f config.json ] || { echo "Not configured yet – run ./setup.sh first."; exit 1; }
  running="$(find_pids '-m panel' | tr '\n' ' ')"
  if [ -n "${running// /}" ]; then ok "Already running (PID ${running}). Logs: tail -f logs/bot.log"; exit 0; fi
  mkdir -p logs
  [ -f logs/bot.log ] && [ "$(wc -c < logs/bot.log)" -gt 10485760 ] && mv logs/bot.log logs/bot.log.1
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup .venv/bin/python -m panel >> logs/bot.log 2>&1 < /dev/null &
  else
    nohup .venv/bin/python -m panel >> logs/bot.log 2>&1 < /dev/null &
  fi
  sleep 3
  pids="$(find_pids '-m panel' | tr '\n' ' ')"
  if [ -n "${pids// /}" ]; then
    ok "Started in the background (PID ${pids})"
    echo "   logs:  tail -f logs/bot.log"
    echo "   stop:  ./stop.sh"
  else
    warn "The bot exited right away – last log lines:"; tail -n 15 logs/bot.log; exit 1
  fi
  exit 0
fi
exec .venv/bin/python -m panel "$@"
