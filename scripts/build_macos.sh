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
DIST_ROOT="${EPA_DIST_ROOT:-$PROJECT_ROOT/dist}"
RELEASE_PARENT="$DIST_ROOT/releases"
RELEASE_ROOT="$RELEASE_PARENT/v$APP_VERSION"
mkdir -p "$PROJECT_ROOT/assets" "$BUILD_ROOT" "$RELEASE_PARENT"
if [[ -e "$RELEASE_ROOT" ]]; then
  echo "Release directory already exists: $RELEASE_ROOT" >&2
  exit 1
fi

if [[ -n "${EPA_PACKAGE_ROOT:-}" ]]; then
  PACKAGE_ROOT="$EPA_PACKAGE_ROOT"
  if [[ -e "$PACKAGE_ROOT" ]]; then
    echo "Package staging directory already exists: $PACKAGE_ROOT" >&2
    exit 1
  fi
  mkdir -p "$PACKAGE_ROOT"
else
  PACKAGE_ROOT="$(mktemp -d "${TMPDIR:-/private/tmp}/econ-paper-analyzer-package-$APP_VERSION-$TARGET_ARCH.XXXXXX")"
fi
RELEASE_STAGE="$(mktemp -d "$RELEASE_PARENT/.v$APP_VERSION-$TARGET_ARCH.XXXXXX")"
cleanup_release_stage() {
  if [[ -n "${RELEASE_STAGE:-}" && -d "$RELEASE_STAGE" ]]; then
    rm -rf "$RELEASE_STAGE"
  fi
}
trap cleanup_release_stage EXIT

APP_PATH="$PACKAGE_ROOT/macos-$TARGET_ARCH/Econ Paper Analyzer.app"
ZIP_NAME="econ-paper-analyzer-macos-$TARGET_ARCH-v$APP_VERSION.zip"
DMG_NAME="econ-paper-analyzer-macos-$TARGET_ARCH-v$APP_VERSION.dmg"
ZIP_PATH="$RELEASE_STAGE/$ZIP_NAME"
DMG_PATH="$RELEASE_STAGE/$DMG_NAME"
STAGE_ROOT="$PACKAGE_ROOT/dmg"

mkdir -p "$PACKAGE_ROOT/macos-$TARGET_ARCH" "$BUILD_ROOT/matplotlib" "$BUILD_ROOT/cache"
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
    --distpath "$PACKAGE_ROOT/macos-$TARGET_ARCH" \
    EconPaperAnalyzer.spec

xattr -cr "$APP_PATH"
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"
unzip -tq "$ZIP_PATH"

if [[ "${EPA_SKIP_DMG:-0}" != "1" ]]; then
  mkdir -p "$STAGE_ROOT"
  ditto "$APP_PATH" "$STAGE_ROOT/Econ Paper Analyzer.app"
  ln -s /Applications "$STAGE_ROOT/Applications"
  hdiutil create \
    -volname "Econ Paper Analyzer" \
    -srcfolder "$STAGE_ROOT" \
    -format UDZO \
    "$DMG_PATH"
  hdiutil verify "$DMG_PATH"
fi

(
  cd "$RELEASE_STAGE"
  if [[ -f "$DMG_NAME" ]]; then
    shasum -a 256 "$ZIP_NAME" "$DMG_NAME" > SHA256SUMS.txt
  else
    shasum -a 256 "$ZIP_NAME" > SHA256SUMS.txt
  fi
  shasum -a 256 -c SHA256SUMS.txt
)

release_files=("$RELEASE_STAGE"/*(N))
expected_files=2
if [[ -f "$DMG_PATH" ]]; then
  expected_files=3
fi
if (( ${#release_files[@]} != expected_files )); then
  echo "Unexpected files in release staging directory: $RELEASE_STAGE" >&2
  exit 1
fi

mv "$RELEASE_STAGE" "$RELEASE_ROOT"
RELEASE_STAGE=""
trap - EXIT

echo "Staged app: $APP_PATH"
echo "Release: $RELEASE_ROOT/$ZIP_NAME"
if [[ -f "$RELEASE_ROOT/$DMG_NAME" ]]; then
  echo "Release: $RELEASE_ROOT/$DMG_NAME"
fi
echo "Checksums: $RELEASE_ROOT/SHA256SUMS.txt"
