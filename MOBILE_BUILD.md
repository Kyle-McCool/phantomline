# Building the Ghostline mobile app

This doc covers two paths from the existing PWA to a real shippable app:

1. **PWA-only** (zero build): users install from your domain on Android Chrome or iOS Safari home-screen.
2. **APK / AAB on Google Play** via Capacitor: wraps the same PWA in a native shell so it lists on Play Store with notifications, share targets, etc.

The PWA path is what Ghostline ships first. APK comes later, after PWA usage validates demand.

---

## Path 1: PWA distribution (no build step)

The site is already a fully functional installable PWA. Customers on Android Chrome or iOS Safari just visit `https://yourdomain.com`, tap "Add to Home Screen," and Ghostline behaves like an app.

What's already wired:
- `static/manifest.json` with shortcuts, icons, theme color
- `/sw.js` service worker with offline shell + cache-first static assets
- `apple-touch-icon`, `apple-mobile-web-app-capable`, `theme-color` meta
- An **Install as app** button in Settings that fires `beforeinstallprompt`
- WebGPU-based AI inference via `static/engines.js` so the PWA actually works without your Python backend running on the user's machine

To distribute the PWA: deploy the existing Flask app to a host (Fly.io, Render, Vercel for the static parts + a Python container for the API) under HTTPS. PWAs require HTTPS — the install prompt won't fire on plain http.

---

## Path 2: Capacitor wrapper for Google Play

Capacitor (https://capacitorjs.com/) is the modern descendant of Cordova. It bundles your existing PWA into a native Android (or iOS) app with a `WebView` that runs your code, plus a plugin layer for native APIs (push notifications, share targets, file system).

### Why Capacitor over native Kotlin

- Reuses the same JS/CSS/HTML you already ship for the PWA
- One codebase, one CI pipeline
- Apple App Store + Google Play both ship from the same source
- Lets you publish to Play Store without rewriting in Kotlin
- Tradeoff: ~10MB extra app overhead, slightly slower first paint

### One-time setup

```bash
# From the project root
npm init -y
npm install --save-dev @capacitor/cli @capacitor/core @capacitor/android
npx cap init Ghostline fun.ghostline.studio --web-dir=static
```

`capacitor.config.json` is already in this repo with the right settings — you just need to:

1. Replace `YOUR-PWA-DOMAIN-HERE.com` in `server.url` with your live PWA domain.
2. Generate a release keystore (see below).
3. Update the `appId` if you don't own `fun.ghostline.studio`.

### Generate a release keystore

```bash
mkdir -p keys
keytool -genkey -v -keystore keys/release.keystore \
  -alias ghostline-release \
  -keyalg RSA -keysize 4096 -validity 10000 \
  -storepass <your-store-password> -keypass <your-key-password>
```

Back this file up offline. **You cannot recover it.** Losing it means you can never push updates to your existing Play Store listing — you'd have to re-publish under a new package name.

### Add the Android platform

```bash
npx cap add android
npx cap sync
```

This generates an `android/` folder that's a real Android Studio project. Check it into your repo (or .gitignore it — your call).

### Build the APK / AAB

```bash
# Open in Android Studio for the first build
npx cap open android

# Or build from CLI once you've signed the gradle config
cd android
./gradlew bundleRelease   # produces app-release.aab for Play Store
./gradlew assembleRelease # produces app-release.apk for sideload testing
```

### What runs inside the wrapper

The Capacitor app launches a WebView that loads `https://yourdomain.com` (the same PWA). Everything that works on the PWA works inside the app. The on-device AI engine (`engines.js`) runs Llama 3.2 1B via WebGPU — most modern Android Chrome versions have WebGPU enabled, but some older devices fall back to WebGL. Test on Pixel 6+, Galaxy S22+, OnePlus 10+ for the supported floor.

### Publishing to Play Store

1. Create a Google Play Console account ($25 one-time)
2. Set up an "Internal testing" track first — push the AAB there
3. Create the listing: title, short description, full description, screenshots (mandatory: 2 phone screenshots), feature graphic, app icon, privacy policy URL
4. Privacy policy: Ghostline collects telemetry (opt-out in Settings). Mention this in the policy. List the data collected: anonymous error events, render outcomes, optional feedback messages.
5. Content rating: complete the IARC questionnaire (Ghostline = "All ages" likely, since it's a creator tool with no user-facing media)
6. Release type: Closed testing → Open testing → Production. Don't skip closed testing.

### Costs

- Google Play registration: $25 one-time
- iOS App Store: $99/yr (only if you want iOS app — PWA on iOS Safari works great without this)
- Code signing certs for iOS: free with Apple Developer account
- Apple/Google reviews: free, takes 1-7 days

### What needs to be in `.env` on your live server

```
GHOSTLINE_LICENSE_SECRET=<the secret you generated>
GHOSTLINE_TELEMETRY_URL=<optional — your Supabase function URL>
YOUTUBE_CLIENT_ID=<for OAuth publish>
YOUTUBE_CLIENT_SECRET=<for OAuth publish>
YOUTUBE_API_KEY=<for SEO research>
```

Capacitor wraps your PWA but the PWA still calls `/api/*` on your domain. Backend deployment is unchanged whether you ship PWA-only or APK.

---

## Summary checklist

- [ ] Live PWA at HTTPS domain
- [ ] License secret in production env
- [ ] Stripe checkout buttons live (replace placeholder URLs in `routes/billing.py`)
- [ ] Supabase Edge Function deployed (see `supabase/functions/issue-license/`)
- [ ] PWA tested on real Android + iPhone via mobile data, not just dev WiFi
- [ ] **Then** install Capacitor + build APK
- [ ] Play Store listing in closed testing
- [ ] Promote to production after a week of testing
