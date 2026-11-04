# Sourced by Start.command and ./jemu: pick a Python 3.10+ already on this Mac.
# Finder-launched scripts may miss Homebrew/python.org paths, so they are checked too.
jemu_find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 \
      /opt/homebrew/bin/python3 /usr/local/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    found=$(command -v "$candidate" 2>/dev/null) || continue
    if "$found" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      JEMU_PYTHON=$found
      return 0
    fi
  done
  return 1
}
