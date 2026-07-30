#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${0:A:h:h}"
cd "$PROJECT_ROOT"

PYTHON_BIN="${EPA_BUILD_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Build Python not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS bundle must be built on macOS." >&2
  exit 1
fi

APP_VERSION="$($PYTHON_BIN -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
TARGET_ARCH="${EPA_TARGET_ARCH:-$(uname -m)}"
PYTHON_ARCH="$($PYTHON_BIN -c 'import platform; print(platform.machine())')"
if [[ "$TARGET_ARCH" != "$PYTHON_ARCH" ]]; then
  echo "Target architecture $TARGET_ARCH does not match build Python $PYTHON_ARCH." >&2
  exit 1
fi
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-14.0}"
BUILD_ROOT="$PROJECT_ROOT/.build/macos-$TARGET_ARCH"
ICONSET="$BUILD_ROOT/AppIcon.iconset"
ICON_PATH="$PROJECT_ROOT/assets/app-icon.png"
DIST_ROOT="$PROJECT_ROOT/dist"
RELEASE_ROOT="$DIST_ROOT/releases/v$APP_VERSION"
APP_PATH="$DIST_ROOT/macos-$TARGET_ARCH/Econ Paper Analyzer.app"
ZIP_PATH="$RELEASE_ROOT/econ-paper-analyzer-macos-$TARGET_ARCH-v$APP_VERSION.zip"
DMG_PATH="$RELEASE_ROOT/econ-paper-analyzer-macos-$TARGET_ARCH-v$APP_VERSION.dmg"
STAGE_ROOT="$BUILD_ROOT/dmg"

mkdir -p "$PROJECT_ROOT/assets" "$BUILD_ROOT" "$RELEASE_ROOT" "$DIST_ROOT/macos-$TARGET_ARCH"
mkdir -p "$BUILD_ROOT/matplotlib" "$BUILD_ROOT/cache"
$PYTHON_BIN scripts/generate_icon.py \
  --png "$ICON_PATH" \
  --iconset "$ICONSET"

EPA_APP_VERSION="$APP_VERSION" \
EPA_TARGET_ARCH="$TARGET_ARCH" \
EPA_ICON_PATH="$ICON_PATH" \
PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/config" \
MPLCONFIGDIR="$BUILD_ROOT/matplotlib" \
XDG_CACHE_HOME="$BUILD_ROOT/cache" \
  $PYTHON_BIN -m PyInstaller \
    --clean \
    --noconfirm \
    --workpath "$BUILD_ROOT/pyinstaller" \
    --distpath "$DIST_ROOT/macos-$TARGET_ARCH" \
    EconPaperAnalyzer.spec

codesign --force --deep --sign - "$APP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

if [[ "${EPA_SKIP_DMG:-0}" != "1" ]]; then
  rm -rf "$STAGE_ROOT"
  mkdir -p "$STAGE_ROOT"
  ditto "$APP_PATH" "$STAGE_ROOT/Econ Paper Analyzer.app"
  ln -s /Applications "$STAGE_ROOT/Applications"
  hdiutil create \
    -volname "Econ Paper Analyzer" \
    -srcfolder "$STAGE_ROOT" \
    -ov \
    -format UDZO \
    "$DMG_PATH"
fi

(
  cd "$RELEASE_ROOT"
  if [[ -f "${DMG_PATH:t}" ]]; then
    shasum -a 256 "${ZIP_PATH:t}" "${DMG_PATH:t}" > SHA256SUMS.txt
  else
    shasum -a 256 "${ZIP_PATH:t}" > SHA256SUMS.txt
  fi
)

echo "Built: $APP_PATH"
echo "Release: $ZIP_PATH"
if [[ -f "$DMG_PATH" ]]; then
  echo "Release: $DMG_PATH"
fi
echo "Checksums: $RELEASE_ROOT/SHA256SUMS.txt"
