#!/usr/bin/env bash
# setup_envroment.sh
#
# Prepare shell environment for Android builds in this repo.
# This script is intended for GitHub Codespaces-style environments.
#
# IMPORTANT:
# - Must be run from an active Python virtual environment.
# - Must be sourced (not executed) so exported vars affect your current shell.
#
# Usage:
#   source ./setup_envroment.sh
#   source ./setup_envroment.sh --install-sdk
#   source ./setup_envroment.sh --install-java
#   source ./setup_envroment.sh --install-all
#
# Optional:
#   --install-sdk   Install Android SDK cmdline-tools + required packages if missing.
#   --install-java  Install OpenJDK 21 if missing (apt-based environments).
#   --install-all   Install both Java and Android SDK dependencies.

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "❌ Please source this script, do not execute it directly."
  echo "   Use: source ./setup_envroment.sh"
  exit 1
fi

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "❌ This script is allowed only inside an active Python virtual environment."
  echo "   Activate venv first, then run: source ./setup_envroment.sh"
  return 1
fi

INSTALL_SDK=false
INSTALL_JAVA=false
for arg in "$@"; do
  case "$arg" in
    --) break ;;
    --install-sdk) INSTALL_SDK=true ;;
    --install-java) INSTALL_JAVA=true ;;
    --install-all)
      INSTALL_JAVA=true
      INSTALL_SDK=true
      ;;
    --help|-h)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# //' | sed 's/^#//'
      return 0
      ;;
    *)
      echo "❌ Unknown argument: $arg"
      return 1
      ;;
  esac
done

export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}"
export ANDROID_HOME="$ANDROID_SDK_ROOT"

if [[ -d "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin" ]]; then
  export PATH="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$PATH"
fi
if [[ -d "$ANDROID_SDK_ROOT/platform-tools" ]]; then
  export PATH="$ANDROID_SDK_ROOT/platform-tools:$PATH"
fi

if [[ -x "/usr/lib/jvm/java-21-openjdk-amd64/bin/java" ]]; then
  export JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"
elif [[ -x "/usr/lib/jvm/java-17-openjdk-amd64/bin/java" ]]; then
  export JAVA_HOME="/usr/lib/jvm/java-17-openjdk-amd64"
fi
if [[ -n "${JAVA_HOME:-}" ]]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi

install_sdk() {
  local sdk_root="$ANDROID_SDK_ROOT"
  mkdir -p "$sdk_root/cmdline-tools"

  if [[ ! -x "$sdk_root/cmdline-tools/latest/bin/sdkmanager" ]]; then
    echo "⬇️  Installing Android cmdline-tools..."
    local zip_file="$sdk_root/commandlinetools-linux-latest.zip"
    curl -fL -o "$zip_file" \
      "https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip"

    rm -rf "$sdk_root/cmdline-tools/latest" "$sdk_root/cmdline-tools/cmdline-tools"
    unzip -q -o "$zip_file" -d "$sdk_root/cmdline-tools"
    mkdir -p "$sdk_root/cmdline-tools/latest"
    cp -a "$sdk_root/cmdline-tools/cmdline-tools/." "$sdk_root/cmdline-tools/latest/"
  fi

  export PATH="$sdk_root/cmdline-tools/latest/bin:$sdk_root/platform-tools:$PATH"

  if command -v sdkmanager >/dev/null 2>&1; then
    echo "📦 Installing Android SDK packages..."
    yes | sdkmanager --licenses >/dev/null
    sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0"
  else
    echo "❌ sdkmanager is still unavailable after cmdline-tools setup."
    return 1
  fi
}

install_java() {
  if [[ -x "/usr/lib/jvm/java-21-openjdk-amd64/bin/java" ]]; then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "❌ Cannot auto-install Java 21: apt-get not found."
    return 1
  fi

  echo "⬇️  Installing OpenJDK 21..."
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y openjdk-21-jdk
  else
    apt-get update
    apt-get install -y openjdk-21-jdk
  fi

  if [[ ! -x "/usr/lib/jvm/java-21-openjdk-amd64/bin/java" ]]; then
    echo "❌ Java 21 installation did not complete as expected."
    return 1
  fi
}

if [[ "$INSTALL_JAVA" == "true" ]]; then
  install_java
fi

if [[ "$INSTALL_SDK" == "true" ]]; then
  install_sdk
fi

echo "=== Environment Ready ==="
echo "VIRTUAL_ENV     : $VIRTUAL_ENV"
echo "ANDROID_SDK_ROOT: $ANDROID_SDK_ROOT"
echo "ANDROID_HOME    : $ANDROID_HOME"
echo "JAVA_HOME       : ${JAVA_HOME:-<not-set>}"
echo "java            : $(command -v java || echo '<not-found>')"
echo "node            : $(command -v node || echo '<not-found>')"
echo "npm             : $(command -v npm || echo '<not-found>')"
echo "sdkmanager      : $(command -v sdkmanager || echo '<not-found>')"
echo
if [[ -x "./build-apk.sh" ]]; then
  echo "Next: ./build-apk.sh --release"
else
  echo "Next: run build script from android-app directory"
fi
