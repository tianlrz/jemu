# Sourced by Start.command, ./jemu and Jemu.app: pick a Python 3.10+ already on this Mac.
# Apps opened from Finder get a minimal PATH, so python.org and Homebrew locations are checked first;
# /usr/bin/python3 (often 3.9 or the Command Line Tools stub) is tried last.
jemu_find_python() {
  for candidate in \
      /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
      /opt/homebrew/bin/python3 /usr/local/bin/python3 \
      python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    found=$(command -v "$candidate" 2>/dev/null) || continue
    if "$found" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      JEMU_PYTHON=$found
      return 0
    fi
  done
  return 1
}
