#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Minecraft Panel Bot – automatic setup / update / clean reinstall
#  Fresh machine : installs everything, then runs the config wizard.
#  Already set up: asks whether to UPDATE (keep world+config) or
#                  do a CLEAN REINSTALL (delete everything first).
#  (Java is downloaded by the bot automatically – nothing to install.)
# ─────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

C_G="\033[1;32m"; C_Y="\033[1;33m"; C_R="\033[1;31m"; C_B="\033[1;36m"; C_0="\033[0m"
info() { echo -e "${C_B}==>${C_0} $*"; }
ok()   { echo -e "${C_G} ✓${C_0} $*"; }
warn() { echo -e "${C_Y} !${C_0} $*"; }
die()  { echo -e "${C_R} ✗ $*${C_0}" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"
  else warn "Not root and 'sudo' not found – system packages can't be installed automatically."; fi
fi
UNIT=/etc/systemd/system/minecraft-panel.service

py_ok() {
  command -v python3 >/dev/null 2>&1 &&
  python3 -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

install_system_packages() {
  if   command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip curl ca-certificates tar gzip unzip git
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-pip curl ca-certificates tar gzip unzip git
  elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y python3 python3-pip curl ca-certificates tar gzip unzip git
  elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -Sy --noconfirm --needed python python-pip curl ca-certificates tar gzip unzip git
  elif command -v apk >/dev/null 2>&1; then
    $SUDO apk add --no-cache python3 py3-pip curl ca-certificates tar gzip unzip libstdc++ bash git
  elif command -v zypper >/dev/null 2>&1; then
    $SUDO zypper --non-interactive install python3 python3-pip curl ca-certificates tar gzip unzip git
  elif command -v brew >/dev/null 2>&1; then
    brew install python
  else
    die "Unsupported package manager. Install Python 3.9+, pip, venv, curl and tar manually, then re-run."
  fi
}

ensure_env() {
  info "Checking system packages"
  if py_ok && command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1; then
    ok "Python $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'), curl and tar are present"
  else
    info "Installing system packages…"
    install_system_packages
    py_ok || die "Python 3.9+ with venv support is required but could not be installed."
    ok "System packages installed"
  fi
  info "Preparing virtual environment"
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip wheel
  .venv/bin/python -m pip install --quiet --upgrade -r requirements.txt
  ok "Dependencies installed"
}

service_active() { command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet minecraft-panel 2>/dev/null; }

finish() {  # after update / install
  echo
  if [ -f "$UNIT" ]; then
    $SUDO systemctl daemon-reload || true
    $SUDO systemctl restart minecraft-panel
    ok "Service restarted (logs: journalctl -u minecraft-panel -f)"
    exit 0
  fi
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] && { [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; }; then
    read -r -p "Install as a systemd service (auto-start on boot, auto-restart on crash)? [y/N] " ans || ans=""
    case "$ans" in
      [Yy]*)
        RUN_USER="$(stat -c '%U' . 2>/dev/null || id -un)"
        WORKDIR="$(pwd)"
        $SUDO tee "$UNIT" >/dev/null <<UNITEOF
[Unit]
Description=Minecraft Panel Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${WORKDIR}
ExecStart=${WORKDIR}/.venv/bin/python -m panel
Restart=on-failure
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
UNITEOF
        $SUDO systemctl daemon-reload
        $SUDO systemctl enable --now minecraft-panel
        ok "Service installed and started (logs: journalctl -u minecraft-panel -f)"
        exit 0 ;;
    esac
  fi
  read -r -p "Start the bot now? [Y/n] " ans || ans=""
  case "$ans" in
    [Nn]*) ok "Done. Start it any time with ./start.sh" ;;
    *)     exec ./start.sh ;;
  esac
}

do_update() {
  info "Updating (config, world, plugins and backups are kept)"
  if service_active; then $SUDO systemctl stop minecraft-panel; ok "Stopped the running service"; fi
  if [ -d .git ] && command -v git >/dev/null 2>&1; then
    git pull --ff-only && ok "Panel code updated from git" || warn "git pull failed – continuing with the current code"
  else
    warn "Not a git checkout – replace the files with the new version yourself if you want new code."
  fi
  ensure_env
  .venv/bin/python -m panel --update
  finish
}

do_clean() {
  echo
  warn "This PERMANENTLY deletes: config.json, the server folder (including the WORLD and all plugins), Java runtime and the virtualenv."
  read -r -p "Type DELETE to continue: " c || c=""
  [ "$c" = "DELETE" ] || { info "Cancelled – nothing was changed."; exit 0; }
  read -r -p "Also delete the backups folder? [y/N] " b || b=""
  if service_active; then $SUDO systemctl stop minecraft-panel; ok "Stopped the running service"; fi
  if [ -x .venv/bin/python ]; then .venv/bin/python -m panel --cleanup || true; fi
  rm -rf config.json server data runtime .venv
  case "$b" in [Yy]*) rm -rf backups ;; esac
  ok "Old installation removed"
  fresh_install
}

fresh_install() {
  ensure_env
  info "First-time configuration"
  .venv/bin/python -m panel --setup
  finish
}

main() {
  if [ -f config.json ]; then
    echo -e "${C_Y}An existing installation was found.${C_0} What do you want to do?"
    echo "  1) Update          – keep config, world & plugins; update the panel, Paper build and plugins"
    echo "  2) Clean reinstall – delete the old install and set everything up from scratch"
    echo "  3) Cancel"
    read -r -p "Choose [1-3]: " choice || choice=3
    case "$choice" in
      1) do_update ;;
      2) do_clean ;;
      *) info "Cancelled."; exit 0 ;;
    esac
  else
    fresh_install
  fi
}

main "$@"
exit 0
