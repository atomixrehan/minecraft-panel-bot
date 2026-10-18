#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Minecraft Panel Bot – automatic setup / update / clean reinstall
#    ./setup.sh            fresh install, or (if already installed) asks: update / clean reinstall
#    ./setup.sh --update   update ONLY the bot code/scripts/dependencies (config, world, plugins, backups untouched)
#        add --with-server to ALSO update Paper + plugins (optional, changes files inside server/)
#    ./setup.sh --clean    clean reinstall         (asks you to type DELETE)
#    ./setup.sh --service  OPTIONAL: install the systemd service (auto-start on boot)   [never done automatically]
#    ./setup.sh --remove-service   remove that service again
#  To remove EVERYTHING from the machine use ./kill.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
. ./lib.sh

[ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ] && warn "Not root and 'sudo' not found – system packages can't be installed automatically."

# ── system packages (we remember which ones WE installed, so ./kill.sh can remove exactly those) ──
PM=""
detect_pm() {
  local c
  for c in apt-get dnf yum pacman apk zypper brew; do command -v "$c" >/dev/null 2>&1 && { PM="$c"; return; }; done
}
pkg_list() {
  case "$PM" in
    apt-get)     echo "python3 python3-venv python3-pip curl ca-certificates tar gzip unzip git" ;;
    dnf|yum|zypper) echo "python3 python3-pip curl ca-certificates tar gzip unzip git" ;;
    pacman)      echo "python python-pip curl ca-certificates tar gzip unzip git" ;;
    apk)         echo "python3 py3-pip curl ca-certificates tar gzip unzip libstdc++ bash git" ;;
    brew)        echo "python" ;;
  esac
}
pkg_installed() {
  case "$PM" in
    apt-get) dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed" ;;
    dnf|yum|zypper) rpm -q "$1" >/dev/null 2>&1 ;;
    pacman)  pacman -Qi "$1" >/dev/null 2>&1 ;;
    apk)     apk info -e "$1" >/dev/null 2>&1 ;;
    brew)    brew list "$1" >/dev/null 2>&1 ;;
  esac
}
save_state() {  # save_state "<newly installed packages>"
  [ "$PM" = "brew" ] && return 0
  local old="" merged
  [ -f "$STATE_FILE" ] && old="$(. "$STATE_FILE"; echo "${PKGS:-}")"
  merged="$(echo "$old $1" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/ $//')"
  printf 'PM=%s\nPKGS="%s"\n' "$PM" "$merged" > "$STATE_FILE"
}

py_ok() {
  command -v python3 >/dev/null 2>&1 &&
  python3 -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

install_system_packages() {
  detect_pm
  [ -n "$PM" ] || die "Unsupported package manager. Install Python 3.9+, pip, venv, curl, tar and git manually, then re-run."
  local all new="" p
  all="$(pkg_list)"
  for p in $all; do pkg_installed "$p" || new="$new $p"; done
  save_state "$new"
  case "$PM" in
    apt-get) $SUDO apt-get update -y; $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y $all ;;
    dnf)     $SUDO dnf install -y $all ;;
    yum)     $SUDO yum install -y $all ;;
    pacman)  $SUDO pacman -Sy --noconfirm --needed $all ;;
    apk)     $SUDO apk add --no-cache $all ;;
    zypper)  $SUDO zypper --non-interactive install $all ;;
    brew)    brew install $all ;;
  esac
}

ensure_system() {
  info "Checking system packages"
  if py_ok && command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
    ok "Python $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'), curl, tar and git are present"
  else
    info "Installing system packages…"
    install_system_packages
    py_ok || die "Python 3.9+ with venv support is required but could not be installed."
    ok "System packages installed"
  fi
}

ensure_venv() {
  info "Preparing virtual environment"
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip wheel
  .venv/bin/python -m pip install --quiet --upgrade -r requirements.txt
  ok "Dependencies installed"
}

# ── systemd: strictly OPT-IN (only with ./setup.sh --service) ──
install_service() {
  command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] || die "systemd is not available on this machine."
  [ -x .venv/bin/python ] || die "Run ./setup.sh first."
  local run_user workdir
  run_user="$(stat -c '%U' . 2>/dev/null || id -un)"
  workdir="$(pwd)"
  stop_everything
  $SUDO tee "$UNIT" >/dev/null <<UNITEOF
[Unit]
Description=Minecraft Panel Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${run_user}
WorkingDirectory=${workdir}
ExecStart=${workdir}/.venv/bin/python -m panel
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
  echo "   remove it any time with: ./setup.sh --remove-service"
}

remove_service() {
  service_exists || { ok "No systemd service is installed – nothing to remove."; return 0; }
  info "Removing the systemd service…"
  $SUDO systemctl stop minecraft-panel 2>/dev/null || true
  $SUDO systemctl disable minecraft-panel 2>/dev/null || true
  $SUDO rm -f "$UNIT"
  $SUDO systemctl daemon-reload 2>/dev/null || true
  $SUDO systemctl reset-failed minecraft-panel 2>/dev/null || true
  ok "Service removed (the bot itself is untouched – start it with ./start.sh)"
}

# ── finishing: choose how to start (systemd is never touched unless it already exists) ──
finish() {
  local ans=""
  echo
  if service_exists; then
    $SUDO systemctl daemon-reload || true
    $SUDO systemctl restart minecraft-panel
    ok "Your existing systemd service was restarted (logs: journalctl -u minecraft-panel -f)"
    exit 0
  fi
  echo "How do you want to start the bot?"
  echo "  1) Here in this terminal   (stops when you close it)"
  echo "  2) In the background       (keeps running after you close the terminal; stop with ./stop.sh)"
  echo "  3) Not now                 (later: ./start.sh  or  ./start.sh --bg)"
  read -r -p "Choose [1-3] (default 1): " ans || ans=3
  case "$ans" in
    2) ./start.sh --bg ;;
    3) ok "Done. Start it any time with ./start.sh or ./start.sh --bg" ;;
    *) exec ./start.sh ;;
  esac
  echo "(Optional, only if you want it: ./setup.sh --service  → auto-start on boot via systemd.)"
}

# ── update ──
link_repo() {  # turn a ZIP download into a git checkout so it can be updated
  local url="$1" branch
  git init -q
  git remote add origin "$url" 2>/dev/null || git remote set-url origin "$url"
  git fetch -q --depth=1 origin || { warn "Could not fetch $url"; return 1; }
  branch="$(git remote show origin 2>/dev/null | sed -n 's/.*HEAD branch: //p')"
  branch="${branch:-main}"
  git checkout -q -f -B "$branch" "origin/$branch" && ok "Linked to $url ($branch)"
}

update_code() {
  if [ -d .git ]; then
    if git pull --ff-only; then ok "Panel code is up to date"
    else warn "git pull failed (local changes?). Fix with: git stash && git pull   – continuing with the current code."; fi
  else
    warn "This folder is not a git checkout, so there is nothing to pull."
    local url=""
    read -r -p "GitHub repo URL to update the code from (empty = skip): " url || url=""
    if [ -n "$url" ]; then link_repo "$url" || true; fi
  fi
}

do_update() {  # do_update [with-server|ask|no]
  local with_server="${1:-no}" ans=""
  info "Updating the bot code only – config, world, plugins and backups stay exactly as they are"
  [ -f config.json ] && cp -p config.json config.json.bak && ok "Safety copy of your config: config.json.bak"
  stop_everything          # a still-running old bot is what causes duplicate panels / port clashes
  ensure_system            # (installs git if missing)
  update_code              # only files tracked in git change; config.json, server/, data/, backups/, runtime/ are git-ignored
  ensure_venv              # after the pull: requirements.txt may have changed
  .venv/bin/python -m panel --update || warn "The update check reported a problem (see above)"
  if [ "$with_server" = "ask" ]; then
    read -r -p "Also update Paper + plugins to their newest builds? This changes files inside server/. [y/N] " ans || ans=""
    case "$ans" in [Yy]*) with_server="with-server" ;; *) with_server="no" ;; esac
  fi
  if [ "$with_server" = "with-server" ]; then .venv/bin/python -m panel --update-server || warn "Server update reported a problem (see above)"; fi
  finish
}

# ── clean reinstall ──
do_clean() {
  local c="" b=""
  echo
  warn "This PERMANENTLY deletes: config.json, the server folder (including the WORLD and all plugins), the Java runtime and the virtualenv."
  read -r -p "Type DELETE to continue: " c || c=""
  [ "$c" = "DELETE" ] || { info "Cancelled – nothing was changed."; exit 0; }
  read -r -p "Also delete the backups folder? [y/N] " b || b=""
  stop_everything
  if [ -x .venv/bin/python ]; then .venv/bin/python -m panel --cleanup || true; fi   # removes the old panel message
  rm -rf config.json server data runtime .venv
  case "$b" in [Yy]*) rm -rf backups ;; esac
  ok "Old installation removed"
  fresh_install
}

fresh_install() {
  ensure_system
  ensure_venv
  info "First-time configuration"
  .venv/bin/python -m panel --setup
  finish
}

configured() {  # true only when config.json exists AND the wizard finished
  [ -f config.json ] && [ -x .venv/bin/python ] &&
  .venv/bin/python -c 'import sys; from panel.config import Config; sys.exit(0 if Config().is_configured() else 1)' 2>/dev/null
}

main() {
  case "${1:-}" in
    --update)
      [ -f config.json ] || die "Nothing to update – no config.json here."
      if [ "${2:-}" = "--with-server" ]; then do_update with-server; else do_update no; fi
      return ;;
    --clean)          do_clean; return ;;
    --service)        install_service; return ;;
    --remove-service) remove_service; return ;;
  esac
  if [ -f config.json ] && ! configured; then
    info "Found an unfinished setup – continuing where you left off (your earlier answers are kept)"
    fresh_install
  elif [ -f config.json ]; then
    echo -e "${C_Y}An existing installation was found.${C_0} What do you want to do?"
    echo "  1) Update          – update ONLY the bot code/scripts; your config, world, plugins and backups stay untouched"
    echo "  2) Clean reinstall – delete the old install and set everything up from scratch"
    echo "  3) Cancel"
    local choice=""
    read -r -p "Choose [1-3]: " choice || choice=3
    case "$choice" in
      1) do_update ask ;;
      2) do_clean ;;
      *) info "Cancelled." ;;
    esac
  else
    fresh_install
  fi
}

main "$@"; exit $?
