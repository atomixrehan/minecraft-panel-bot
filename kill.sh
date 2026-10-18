#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  kill.sh – stop EVERYTHING and remove everything this project installed.
#    ./kill.sh              interactive (asks before each destructive part)
#    ./kill.sh --yes        no questions (keeps backups, source folder and system packages)
#    ./kill.sh --purge      with --yes: ALSO delete backups and this whole folder
#    ./kill.sh --packages   also remove the system packages setup.sh installed
# ─────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
. ./lib.sh

YES=false; PURGE=false; WANT_PKGS=false
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=true ;;
    --purge) PURGE=true ;;
    --packages) WANT_PKGS=true ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,2\}//'; exit 0 ;;
    *) die "Unknown option: $a" ;;
  esac
done

remove_packages() {
  [ -f "$STATE_FILE" ] || { info "No record of system packages installed by setup.sh – leaving system packages alone."; return 0; }
  local PM="" PKGS=""
  . "$STATE_FILE"
  [ -n "$PKGS" ] || { info "setup.sh did not install any system packages – nothing to remove."; return 0; }
  info "Removing system packages that setup.sh installed: $PKGS"
  case "$PM" in
    apt-get|apt) $SUDO apt-get remove -y $PKGS ;;
    dnf)    $SUDO dnf remove -y $PKGS ;;
    yum)    $SUDO yum remove -y $PKGS ;;
    pacman) $SUDO pacman -Rns --noconfirm $PKGS ;;
    apk)    $SUDO apk del $PKGS ;;
    zypper) $SUDO zypper --non-interactive remove $PKGS ;;
    *) warn "Unknown package manager '$PM' – skipping" ;;
  esac
}

main() {
  local c="" ans="" DEL_BACKUPS=false DEL_REPO=false DO_PKGS=false
  echo -e "${C_R}This will STOP and DELETE everything this project put on this machine:${C_0}"
  echo "   • the systemd service (if installed) and any running bot / Minecraft server of this folder"
  echo "   • server/ (WORLD, plugins, configs)   runtime/ (downloaded Java)   .venv/   data/   logs/   config.json (bot token)"
  echo "   • the panel message in Discord"
  echo "   optional: backups/, this whole folder, and the system packages setup.sh installed"
  echo

  if $YES; then
    DEL_BACKUPS=$PURGE; DEL_REPO=$PURGE; DO_PKGS=$WANT_PKGS
  else
    read -r -p "Type KILL to continue: " c || c=""
    [ "$c" = "KILL" ] || { info "Cancelled – nothing was changed."; exit 0; }
    read -r -p "Also delete the backups folder? [y/N] " ans || ans="";           case "$ans" in [Yy]*) DEL_BACKUPS=true ;; esac
    read -r -p "Also delete this whole folder (the source code)? [y/N] " ans || ans=""; case "$ans" in [Yy]*) DEL_REPO=true ;; esac
    if [ -f "$STATE_FILE" ]; then
      read -r -p "Also remove the system packages setup.sh installed? [y/N] " ans || ans=""; case "$ans" in [Yy]*) DO_PKGS=true ;; esac
    fi
  fi

  stop_everything

  if [ -x .venv/bin/python ] && [ -f config.json ]; then
    .venv/bin/python -m panel --cleanup || true        # deletes the panel message from Discord
  fi

  if service_exists; then
    info "Removing the systemd service…"
    $SUDO systemctl disable minecraft-panel >/dev/null 2>&1 || true
    $SUDO rm -f "$UNIT"
    $SUDO systemctl daemon-reload 2>/dev/null || true
    $SUDO systemctl reset-failed minecraft-panel 2>/dev/null || true
    ok "Service removed"
  fi

  info "Deleting installed files…"
  rm -rf .venv server runtime data logs config.json config.json.bak config.tmp ./*.part
  find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  $DEL_BACKUPS && rm -rf backups
  ok "Files deleted"

  $DO_PKGS && remove_packages
  rm -f "$STATE_FILE"

  if $DEL_REPO; then
    info "Deleting this folder…"
    local here="$DIR"
    cd / && rm -rf "$here"
    ok "Everything removed – the machine is clean."
  else
    ok "Done. The machine is clean (source code kept; run ./setup.sh to install again)."
  fi
}

main; exit $?
