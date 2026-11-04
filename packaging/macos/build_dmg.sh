#!/bin/sh
# Build Jemu.app and Jemu-<version>.dmg from the committed tree.
#   packaging/macos/build_dmg.sh [out-dir]
# The .app is built on any POSIX system; the icon and DMG steps need macOS (sips/iconutil/hdiutil).
set -eu
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$REPO/dist}"
VERSION="$(tr -d '[:space:]' < "$REPO/VERSION")"
SHORT_VERSION="$(printf '%s' "$VERSION" | sed 's/[^0-9.].*//')"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/stage/Jemu.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app" "$OUT"

# Committed files only: no library, .runtime, credentials or caches. Tests and Windows launchers are left out.
git -C "$REPO" archive --format=tar HEAD -- . ':!tests' ':!.github' ':!Start.cmd' ':!jemu.cmd' ':!packaging' \
  | tar -x -C "$APP/Contents/Resources/app"
cp "$REPO/packaging/macos/Jemu" "$APP/Contents/MacOS/Jemu"
chmod +x "$APP/Contents/MacOS/Jemu"
sed -e "s/@VERSION@/$VERSION/" -e "s/@SHORT_VERSION@/${SHORT_VERSION:-0.0.0}/" \
  "$REPO/packaging/macos/Info.plist" > "$APP/Contents/Info.plist"

if [ "$(uname)" != Darwin ]; then
  rm -rf "$OUT/Jemu.app"; cp -R "$APP" "$OUT/Jemu.app"
  echo "Built $OUT/Jemu.app (icon and DMG need macOS)"; exit 0
fi

# Icon from the project SVG via Quick Look; the app still works without it.
ICONSET="$WORK/Jemu.iconset"; mkdir -p "$ICONSET"
if qlmanage -t -s 1024 -o "$WORK" "$REPO/assets/brand/logo-light.svg" >/dev/null 2>&1 && [ -f "$WORK/logo-light.svg.png" ]; then
  for size in 16 32 128 256 512; do
    sips -z $size $size "$WORK/logo-light.svg.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double "$WORK/logo-light.svg.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Jemu.icns" || echo "warning: icon not created"
else
  echo "warning: could not render icon; building without it"
fi

# Ad-hoc signature keeps the bundle consistent; it is not Developer ID signing or notarization.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "warning: ad-hoc signing skipped"
ln -s /Applications "$WORK/stage/Applications"
DMG="$OUT/Jemu-$VERSION.dmg"
rm -f "$DMG"
hdiutil create -volname "Jemu $VERSION" -srcfolder "$WORK/stage" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
rm -rf "$OUT/Jemu.app"; cp -R "$APP" "$OUT/Jemu.app"
echo "Built $DMG"
