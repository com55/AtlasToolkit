#!/usr/bin/env bash
# build-apk.sh — Local build script for AtlasToolkit Android APK
#
# Usage:
#   ./build-apk.sh              # debug build (default)
#   ./build-apk.sh --release    # unsigned release build
#   ./build-apk.sh --release --sign  # signed release build (requires keystore env vars)
#   ./build-apk.sh --install-all      # install Java/SDK deps via setup script before build
#
# Environment variables for signing (--sign flag):
#   ANDROID_KEYSTORE_PATH     Path to your .keystore / .jks file
#   ANDROID_KEYSTORE_PASSWORD Keystore password
#   ANDROID_KEY_ALIAS         Key alias
#   ANDROID_KEY_PASSWORD      Key password
#
# Prerequisites:
#   - Node.js >= 18 and npm >= 9
#   - Active Python virtual environment (VIRTUAL_ENV set)
#   - Java 21 (preferred) or Java 17
#   - Android SDK
#   - setup_envroment.sh (auto-sourced by this script)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse arguments ──────────────────────────────────────────────────────────
BUILD_TYPE="debug"
SIGN=false
INSTALL_ALL=false

for arg in "$@"; do
  case "$arg" in
    --release) BUILD_TYPE="release" ;;
    --sign)    SIGN=true ;;
    --install-all) INSTALL_ALL=true ;;
    --help|-h)
      sed -n '2,30p' "$0" | sed 's/^# //' | sed 's/^#//'
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

echo "=== AtlasToolkit Android Build ==="
echo "Build type : $BUILD_TYPE"
echo "Signing    : $SIGN"
echo

# ── Bootstrap shell environment (venv-only) ────────────────────────────────
SETUP_SCRIPT="$SCRIPT_DIR/setup_envroment.sh"
if [ ! -f "$SETUP_SCRIPT" ]; then
  echo "❌ setup_envroment.sh not found at $SETUP_SCRIPT"
  exit 1
fi

if [ "$INSTALL_ALL" = "true" ]; then
  # shellcheck source=/dev/null
  source "$SETUP_SCRIPT" --install-all || exit 1
else
  # shellcheck source=/dev/null
  source "$SETUP_SCRIPT" -- || exit 1
fi
echo

# ── Verify prerequisites ─────────────────────────────────────────────────────
command -v node >/dev/null 2>&1 || { echo "❌ Node.js not found. Install Node.js >= 18."; exit 1; }
command -v npm  >/dev/null 2>&1 || { echo "❌ npm not found."; exit 1; }
command -v java >/dev/null 2>&1 || { echo "❌ Java not found. Install Java 21 or 17."; exit 1; }

NODE_MAJOR=$(node --version | sed 's/v\([0-9]*\).*/\1/')
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "❌ Node.js >= 18 required (found $(node --version))"; exit 1
fi

JAVA_MAJOR=$(java -version 2>&1 | sed -n '1s/.*version "\([0-9][0-9]*\).*/\1/p')
if [ -z "$JAVA_MAJOR" ] || [ "$JAVA_MAJOR" -lt 17 ] || [ "$JAVA_MAJOR" -gt 21 ]; then
  echo "❌ Java 17-21 required (found $(java -version 2>&1 | head -1))"
  echo "   Tip: source ./setup_envroment.sh --install-java"
  exit 1
fi

# Detect Android SDK
if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
  GUESSES=(
    "$HOME/Android/Sdk"
    "$HOME/Library/Android/sdk"
    "/usr/local/lib/android/sdk"
  )
  for g in "${GUESSES[@]}"; do
    if [ -d "$g" ]; then
      export ANDROID_HOME="$g"
      echo "ℹ️  Auto-detected ANDROID_HOME=$ANDROID_HOME"
      break
    fi
  done
fi

if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
  echo "❌ Android SDK not found."
  echo "   Set ANDROID_HOME or ANDROID_SDK_ROOT, or install Android Studio."
  exit 1
fi

echo "✔  Node.js $(node --version)"
echo "✔  Java $(java -version 2>&1 | head -1)"
echo "✔  Android SDK: ${ANDROID_HOME:-${ANDROID_SDK_ROOT}}"
echo

# ── Install npm dependencies ─────────────────────────────────────────────────
echo "📦 Installing npm dependencies..."
cd "$SCRIPT_DIR"
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
echo

# ── Add Android platform if not already present ───────────────────────────────
if [ ! -d "$SCRIPT_DIR/android" ]; then
  echo "➕ Adding Capacitor Android platform..."
  npx cap add android
  echo
fi

# ── Sync web assets ───────────────────────────────────────────────────────────
echo "🔄 Syncing Capacitor assets..."
if npx cap sync android --help 2>&1 | grep -q -- '--no-deps'; then
  npx cap sync android --no-deps
else
  npx cap sync android
fi
echo

# ── Build with Gradle ────────────────────────────────────────────────────────
GRADLE="$SCRIPT_DIR/android/gradlew"
chmod +x "$GRADLE"

if [ "$BUILD_TYPE" = "release" ]; then
  GRADLE_TASK="assembleRelease"
else
  GRADLE_TASK="assembleDebug"
fi

echo "🏗️  Running Gradle task: $GRADLE_TASK ..."
cd "$SCRIPT_DIR/android"
"$GRADLE" "$GRADLE_TASK" --no-daemon
echo

# ── Signing (release only) ───────────────────────────────────────────────────
if [ "$BUILD_TYPE" = "release" ] && [ "$SIGN" = "true" ]; then
  echo "🔏 Signing release APK..."

  : "${ANDROID_KEYSTORE_PATH:?  ❌ Set ANDROID_KEYSTORE_PATH}"
  : "${ANDROID_KEYSTORE_PASSWORD:?  ❌ Set ANDROID_KEYSTORE_PASSWORD}"
  : "${ANDROID_KEY_ALIAS:?  ❌ Set ANDROID_KEY_ALIAS}"
  : "${ANDROID_KEY_PASSWORD:?  ❌ Set ANDROID_KEY_PASSWORD}"

  UNSIGNED="$SCRIPT_DIR/android/app/build/outputs/apk/release/app-release-unsigned.apk"
  ALIGNED="$SCRIPT_DIR/android/app/build/outputs/apk/release/app-release-aligned.apk"
  SIGNED="$SCRIPT_DIR/android/app/build/outputs/apk/release/app-release-signed.apk"

  # Find zipalign and apksigner from the Android SDK
  SDK_ROOT="${ANDROID_HOME:-${ANDROID_SDK_ROOT}}"
  ZIPALIGN=$(find "$SDK_ROOT/build-tools" -name zipalign | sort -V | tail -1)
  APKSIGNER=$(find "$SDK_ROOT/build-tools" -name apksigner | sort -V | tail -1)

  if [ -z "$ZIPALIGN" ] || [ -z "$APKSIGNER" ]; then
    echo "❌ zipalign or apksigner not found in Android SDK build-tools."
    echo "   Install build-tools via Android Studio SDK Manager."
    exit 1
  fi

  "$ZIPALIGN" -v -p 4 "$UNSIGNED" "$ALIGNED"
  "$APKSIGNER" sign \
    --ks "$ANDROID_KEYSTORE_PATH" \
    --ks-pass "pass:$ANDROID_KEYSTORE_PASSWORD" \
    --ks-key-alias "$ANDROID_KEY_ALIAS" \
    --key-pass "pass:$ANDROID_KEY_PASSWORD" \
    --out "$SIGNED" \
    "$ALIGNED"
  "$APKSIGNER" verify "$SIGNED"
  rm -f "$ALIGNED"
  echo "✔  Signed APK: $SIGNED"
fi

# ── Report output ────────────────────────────────────────────────────────────
echo
echo "=== Build complete ==="
if [ "$BUILD_TYPE" = "release" ]; then
  OUT_DIR="$SCRIPT_DIR/android/app/build/outputs/apk/release"
  echo "📱 Release APK(s):"
  ls -lh "$OUT_DIR"/*.apk 2>/dev/null || true
else
  OUT_DIR="$SCRIPT_DIR/android/app/build/outputs/apk/debug"
  echo "📱 Debug APK:"
  ls -lh "$OUT_DIR"/*.apk 2>/dev/null || true
fi
