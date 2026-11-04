#!/bin/sh
# macOS: double-click to open Jemu in its own Chrome/Edge app window.
cd "$(dirname "$0")" || exit 1
. ./jemu_python.sh
if ! jemu_find_python; then
  echo "Python 3.10 or newer is required. Install it from python.org or Homebrew, then try again."
  echo "Press Return to close."
  read -r _
  exit 1
fi
# Detach so this Terminal window can be closed; logs go to .runtime/ (see launch.pyw).
nohup "$JEMU_PYTHON" launch.pyw >/dev/null 2>&1 &
echo "Jemu is starting. You can close this window."
