# AtlasToolkit Android App

Capacitor-based Android port of AtlasToolkit.  
The Python/pywebview desktop backend has been fully rewritten in JavaScript using the Canvas API.

## Architecture

```
android-app/
├── www/                      ← Web assets (served by Capacitor)
│   ├── index.html            ← App shell (same UI, ES-module script)
│   ├── style.css             ← Unchanged from desktop version
│   ├── script.js             ← UI logic (pywebview.api → AtlasAPI)
│   └── js/
│       ├── atlas-converter.js  ← Port of atlas_converter.py
│       ├── atlas-extracter.js  ← Port of atlas_extracter.py (Canvas API)
│       ├── atlas-modifier.js   ← Port of atlas_modifier.py (Canvas API)
│       └── atlas-api.js        ← Drop-in replacement for pywebview.api
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
- **Java 17** (required by Gradle)
- Android device or emulator (API 24+)

---

## Build Instructions

### 1. Install dependencies

```bash
cd android-app
npm install
```

### 2. Add the Android platform

```bash
npx cap add android
```

This generates the `android/` folder with a native Android project.

### 3. Sync web assets into the native project

```bash
npx cap sync android
```

Run this command every time you modify files inside `www/`.

### 4. Open in Android Studio

```bash
npx cap open android
```

In Android Studio:
- Select **Build → Build Bundle(s) / APK(s) → Build APK(s)**
- The signed/debug APK will be in `android/app/build/outputs/apk/debug/`

### 5. Run on device / emulator

In Android Studio press the **▶ Run** button, or from the CLI:

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
# Serve www/ with any static file server, e.g.:
npx serve www
# then open http://localhost:3000
```

> Note: `<input type="file">` and `<a download>` are used for all file I/O, so no Capacitor plugins are strictly required for basic use.
