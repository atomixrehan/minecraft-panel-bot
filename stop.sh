#!/usr/bin/env bash
# Stop the bot AND the Minecraft server of this folder safely (world is saved first).
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
. ./lib.sh
stop_everything
