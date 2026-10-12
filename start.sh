#!/usr/bin/env bash
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Run ./setup.sh first."; exit 1
fi
exec .venv/bin/python -m panel "$@"
