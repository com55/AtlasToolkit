# AtlasToolkit Android App

Capacitor-based Android port of AtlasToolkit.  
The Python/pywebview desktop backend has been fully rewritten in JavaScript using the Canvas API.

## Architecture

```
AtlasToolkit/
├── www/                      ← Web assets (source of truth)
│   ├── index.html            ← App shell (same UI, ES-module script)
│   ├── style.css             ← Unchanged from desktop version
│   ├── script.js             ← UI logic (pywebview.api → AtlasAPI)
│   └── js/
│       ├── atlas-converter.js  ← Port of atlas_converter.py
│       ├── atlas-extracter.js  ← Port of atlas_extracter.py (Canvas API)
│       ├── atlas-modifier.js   ← Port of atlas_modifier.py (Canvas API)
│       └── atlas-api.js        ← Drop-in replacement for pywebview.api
└── android-app/
    ├── www -> ../www         ← symlink used by Capacitor
    ├── package.json
    └── capacitor.config.json
```

### What changed vs the desktop app

| Desktop (Python)              | Android (JavaScript)                     |
|-------------------------------|------------------------------------------|
| `pywebview` window            | Capacitor WebView (Android Activity)     |
| `Pillow` image processing     | HTML5 Canvas API                         |
| `pywebview.api.*` calls       | `AtlasAPI.*` calls in `atlas-api.js`     |
| File dialogs (pywebview)      | `<input type="file">` / download links  |
| Auto-update / self-update     | Not applicable (Play Store or side-load) |

---

## Prerequisites

- **Node.js** ≥ 18 and **npm** ≥ 9
- **Android Studio** (latest stable) with Android SDK ≥ 24
- **Python virtual environment (venv)** for local scripts
- **Java 21** (recommended; Java 17 may work in some setups)
- Android device or emulator (API 24+)

---

## Build Instructions

### Option A — Local build script

```bash
cd android-app

# 1) Activate venv first (required)
python -m venv .venv
source .venv/bin/activate

# 2) Prepare environment (venv-only script)
source ./setup_envroment.sh
# or bootstrap dependencies on a clean machine:
# source ./setup_envroment.sh --install-all

# Debug APK (fastest, no signing needed)
./build-apk.sh

# Unsigned release APK
./build-apk.sh --release

# One-shot bootstrap + build (still requires active venv)
# ./build-apk.sh --install-all --release

# Signed release APK (set env vars first)
export ANDROID_KEYSTORE_PATH=/path/to/release.keystore
export ANDROID_KEYSTORE_PASSWORD=yourKeystorePassword
export ANDROID_KEY_ALIAS=yourKeyAlias
export ANDROID_KEY_PASSWORD=yourKeyPassword
./build-apk.sh --release --sign
```

Notes:
- `setup_envroment.sh` must be sourced, not executed.
- `build-apk.sh` auto-sources `setup_envroment.sh` and will fail if no active venv is detected.
- If your Java version is outside 17-21, the build script will stop with a clear message.

Output APKs are written to:
- Debug: `android/app/build/outputs/apk/debug/app-debug.apk`
- Release: `android/app/build/outputs/apk/release/app-release-{unsigned,signed}.apk`

### Option B — GitHub Actions CI

The workflow `.github/workflows/build_android.yml` runs automatically on:

| Trigger | What happens |
|---|---|
| Push to a `v*` tag | Builds debug + release APK, uploads release APK to GitHub Release |
| `auto_tag.yml` creates a tag | Same as above (called as reusable workflow) |
| `workflow_dispatch` | Builds debug APK and uploads as an artifact |

#### Keystore secrets (for signed release APKs in CI)

Add these secrets to your GitHub repository (**Settings → Secrets and variables → Actions**):

| Secret | Description |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | Base64-encoded `.jks` / `.keystore` file (`base64 -w0 release.keystore`) |
| `ANDROID_KEYSTORE_PASSWORD` | Keystore password |
| `ANDROID_KEY_ALIAS` | Key alias inside the keystore |
| `ANDROID_KEY_PASSWORD` | Key password |

If these secrets are absent, the CI still builds and uploads an **unsigned** release APK.

### Option C — Android Studio (manual)

```bash
cd android-app
npm install
npx cap add android   # only once
npx cap sync android  # run after every www/ change
npx cap open android  # opens in Android Studio
```

In Android Studio:
- Select **Build → Build Bundle(s) / APK(s) → Build APK(s)**
- The debug APK will be at `android/app/build/outputs/apk/debug/app-debug.apk`

### Option D — Run on device / emulator

```bash
npx cap run android
```

---

## Required Android Permissions

The app needs these permissions (already included in the generated `AndroidManifest.xml` after `cap add android`, but verify):

```xml
<uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
<uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE"
    android:maxSdkVersion="28" />
<uses-permission android:name="android.permission.READ_MEDIA_IMAGES" />
```

For Android 13+ (API 33), `READ_MEDIA_IMAGES` is needed instead of `READ_EXTERNAL_STORAGE`.

---

## Usage on Android

1. **Open atlas**: Tap **Open** → select the `.atlas` file *and* its associated PNG image(s) together in the multi-file picker.
2. **Extract regions**: Select regions in the list and tap **Extract Selected** or **Extract All**. Files are saved to the **Downloads** folder.
3. **Modify mode**: Tap **Modify** → select regions → tap **Modify Selected** → pick your mod PNG → optionally enable **Repack** → tap **Save As...** (saves to Downloads).

---

## Development (browser testing)

The app works in a regular browser too (no native features required for core functionality):

```bash
# Serve root www/ with any static file server, e.g.:
cd ..
npx serve www
# then open http://localhost:3000
```

> Note: `<input type="file">` and `<a download>` are used for all file I/O, so no Capacitor plugins are strictly required for basic use.
