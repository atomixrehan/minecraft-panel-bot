# Shared helpers – sourced by setup.sh and kill.sh (not meant to be run directly)
C_G="\033[1;32m"; C_Y="\033[1;33m"; C_R="\033[1;31m"; C_B="\033[1;36m"; C_0="\033[0m"
info() { echo -e "${C_B}==>${C_0} $*"; }
ok()   { echo -e "${C_G} ✓${C_0} $*"; }
warn() { echo -e "${C_Y} !${C_0} $*"; }
die()  { echo -e "${C_R} ✗ $*${C_0}" >&2; exit 1; }

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT=/etc/systemd/system/minecraft-panel.service
STATE_FILE="$DIR/.install_state"

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

service_exists() { [ -f "$UNIT" ]; }
service_active() { command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet minecraft-panel 2>/dev/null; }

# PIDs of processes matching a command-line pattern whose working directory is inside this install.
find_pids() {
  local pid cwd
  for pid in $(pgrep -f -- "$1" 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    if [ -d /proc/self ]; then
      cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
    else
      cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n1)"
    fi
    case "$cwd" in "$DIR"|"$DIR"/*) echo "$pid" ;; esac
  done
}

pids_alive() { local p; for p in "$@"; do kill -0 "$p" 2>/dev/null && return 0; done; return 1; }

wait_gone() {  # wait_gone <seconds> <pid...>
  local t=0 max="$1"; shift
  while pids_alive "$@" && [ "$t" -lt "$max" ]; do sleep 1; t=$((t + 1)); done
  ! pids_alive "$@"
}

# Stop the systemd service, the bot and the Minecraft server of THIS install (gracefully first).
stop_everything() {
  local bots javas
  if service_exists && service_active; then
    info "Stopping the systemd service…"
    $SUDO systemctl stop minecraft-panel || true
  fi
  bots="$(find_pids '-m panel')"
  if [ -n "$bots" ]; then
    info "Stopping the panel bot (it shuts Minecraft down safely first – up to ~2 min)…"
    kill -TERM $bots 2>/dev/null || true
    if ! wait_gone 120 $bots; then warn "Bot did not exit in time – killing it"; kill -KILL $bots 2>/dev/null || true; fi
  fi
  javas="$(find_pids 'server.jar')"
  if [ -n "$javas" ]; then
    info "Stopping the Minecraft server process…"
    kill -TERM $javas 2>/dev/null || true
    if ! wait_gone 40 $javas; then warn "Server did not exit in time – killing it"; kill -KILL $javas 2>/dev/null || true; fi
  fi
  ok "Nothing from this install is running"
}
