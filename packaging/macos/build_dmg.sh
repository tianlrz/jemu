#!/bin/sh
# Build a self-contained Jemu.app and Jemu-<version>-<arch>.dmg from the committed tree.
#   packaging/macos/build_dmg.sh [arm64|x86_64] [out-dir]
#
# The app bundles a trimmed standalone Python 3.12 (with pypdfium2 + Pillow for PPT export) and a
# small Swift shell that shows the workspace and renders PDFs with the system WebKit, so users need
# neither Python nor Chrome/Edge. On non-macOS systems only the app-code skeleton is produced.
set -eu
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ARCH="${1:-$(uname -m)}"; [ "$ARCH" = aarch64 ] && ARCH=arm64
OUT="${2:-$REPO/dist}"
VERSION="$(tr -d '[:space:]' < "$REPO/VERSION")"
SHORT_VERSION="$(printf '%s' "$VERSION" | sed 's/[^0-9.].*//')"

# Pinned runtime: python-build-standalone 20241016, CPython 3.12.7 (stripped install).
PBS_URL=https://github.com/astral-sh/python-build-standalone/releases/download/20241016
case "$ARCH" in
  arm64)  PBS_FILE=cpython-3.12.7+20241016-aarch64-apple-darwin-install_only_stripped.tar.gz
          PBS_SHA=95dd397e3aef4cc1846867cf20be704bdd74edd16ea8032caf01e48f0c53d65d ;;
  x86_64) PBS_FILE=cpython-3.12.7+20241016-x86_64-apple-darwin-install_only_stripped.tar.gz
          PBS_SHA=848405b92bda20fad1f9bba99234c7d3f11e0b31e46f89835d1cb3d735e932aa ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 2 ;;
esac
WHEELS="pypdfium2==4.30.0 Pillow==11.0.0"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/stage/Jemu.app"
RES="$APP/Contents/Resources"
mkdir -p "$APP/Contents/MacOS" "$RES/app" "$OUT"

# Committed files only: no library, .runtime, credentials or caches.
git -C "$REPO" archive --format=tar HEAD -- . ':!tests' ':!.github' ':!Start.cmd' ':!jemu.cmd' ':!packaging' \
  | tar -x -C "$RES/app"
sed -e "s/@VERSION@/$VERSION/" -e "s/@SHORT_VERSION@/${SHORT_VERSION:-0.0.0}/" \
  "$REPO/packaging/macos/Info.plist" > "$APP/Contents/Info.plist"

if [ "$(uname)" != Darwin ]; then
  rm -rf "$OUT/Jemu.app"; cp -R "$APP" "$OUT/Jemu.app"
  echo "Built app-code skeleton $OUT/Jemu.app (runtime, shell and DMG need macOS)"; exit 0
fi

# 1. Python runtime, verified against the pinned hash.
CACHE="${JEMU_BUILD_CACHE:-$REPO/build/cache}"; mkdir -p "$CACHE"
[ -f "$CACHE/$PBS_FILE" ] || curl -fsSL --retry 3 -o "$CACHE/$PBS_FILE" "$PBS_URL/$PBS_FILE"
echo "$PBS_SHA  $CACHE/$PBS_FILE" | shasum -a 256 -c -
tar -xzf "$CACHE/$PBS_FILE" -C "$RES"   # -> Resources/python
PY="$RES/python"; LIB="$PY/lib/python3.12"
# Drop what Jemu never uses: Tk, IDLE, pip, tests, headers, build config (about 18 MB).
rm -rf "$PY/include" "$PY/share" "$PY/lib/pkgconfig" "$PY/lib/tcl"* "$PY/lib/tk"* "$PY/lib/itcl"* "$PY/lib/thread"* \
  "$LIB/test" "$LIB/idlelib" "$LIB/tkinter" "$LIB/turtledemo" "$LIB/turtle.py" "$LIB/ensurepip" "$LIB/lib2to3" \
  "$LIB/pydoc_data" "$LIB"/config-3.12-* "$LIB/site-packages/pip" "$LIB"/site-packages/pip-*
find "$PY/bin" -mindepth 1 ! -name python3 ! -name python3.12 -exec rm -rf {} +
find "$LIB/lib-dynload" -name '_tkinter*' -delete

# 2. PPT export components for the target architecture (binary wheels only, no build step).
case "$ARCH" in arm64) PLAT=macosx_11_0_arm64 ;; x86_64) PLAT=macosx_11_0_x86_64 ;; esac
python3 -m pip install --quiet --disable-pip-version-check --no-compile --target "$LIB/site-packages" \
  --platform "$PLAT" --implementation cp --python-version 3.12 --only-binary=:all: $WHEELS
find "$LIB/site-packages" -type d -name tests -prune -exec rm -rf {} +

# 3. Bytecode for the app code (version-specific, architecture-independent).
if python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)'; then
  python3 -m compileall -q -j 0 --invalidation-mode unchecked-hash "$RES/app" "$LIB/site-packages" >/dev/null
fi

# 4. Native shell (system WebKit window + headless PDF renderer).
swiftc -O -target "$ARCH-apple-macos12.0" -o "$APP/Contents/MacOS/Jemu" "$REPO/packaging/macos/JemuApp.swift" \
  -framework Cocoa -framework WebKit

# 5. Icon from the project SVG via Quick Look; the app still works without it.
ICONSET="$WORK/Jemu.iconset"; mkdir -p "$ICONSET"
if qlmanage -t -s 1024 -o "$WORK" "$REPO/assets/brand/logo-light.svg" >/dev/null 2>&1 && [ -f "$WORK/logo-light.svg.png" ]; then
  for size in 16 32 128 256 512; do
    sips -z $size $size "$WORK/logo-light.svg.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double "$WORK/logo-light.svg.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$RES/Jemu.icns" || echo "warning: icon not created"
else
  echo "warning: could not render icon; building without it"
fi

# 6. Ad-hoc signature over every binary (required on Apple silicon). Not Developer ID / notarized.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

ln -s /Applications "$WORK/stage/Applications"
DMG="$OUT/Jemu-$VERSION-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -volname "Jemu $VERSION" -srcfolder "$WORK/stage" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
rm -rf "$OUT/Jemu-$ARCH.app"; cp -R "$APP" "$OUT/Jemu-$ARCH.app"
du -sh "$OUT/Jemu-$ARCH.app" "$DMG"
echo "Built $DMG"
