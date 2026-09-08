/* ==========================================================================
   The native shell's whole configuration.

   Every value that differs between "the shell I am developing against" and
   "the shell I publish" lives in app.config.json, which this file and
   android/app/build.gradle both read. One file, two halves: the shell cannot
   point at one server while the package is versioned for another.

   The shell renders nothing of its own. `server.url` is the deployed web app
   and the WebView loads it exactly as a browser would -- same session cookie,
   same service worker, same SSR pages -- so no page, template or router in
   `app/` knows this project exists. `webDir` below is *not* the app: it is a
   single local page the WebView falls back to when that URL cannot be
   reached, which is the one thing a remote-loaded shell owes the member that
   a browser tab does not (a browser has an address bar; this has nothing).
   ========================================================================== */

const cfg = require("./app.config.json");

/** The host of `serverUrl`, which is the only host the WebView may navigate
 *  inside itself. Everything else -- an outbound link, a payment page --
 *  Capacitor hands to the system browser, which is the behaviour a member
 *  expects and the one that keeps a foreign page from wearing this app's
 *  chrome. */
const serverHost = new URL(cfg.serverUrl).hostname;

/** @type {import('@capacitor/cli').CapacitorConfig} */
module.exports = {
  appId: cfg.appId,
  appName: cfg.appName,
  webDir: "www",
  // The colour behind the WebView before its first paint. Any other value is
  // a flash of the wrong colour on every cold start; this is the light
  // theme's `--bg-0`, the same value the manifest's `background_color` takes.
  backgroundColor: "#f8f4ee",
  server: {
    url: cfg.serverUrl,
    // Relative to webDir. Shown when the WebView cannot reach `url` at all --
    // a lost connection, DNS, a server that is down.
    //
    // It must never be shown for a *status code*: a 404 here is a designed
    // answer with its own page behind it, and the 303/401 split has to arrive
    // at the page exactly as the server wrote it. Capacitor's own web view
    // client does not make that distinction, so MainActivity.java overrides
    // the HTTP half of it. Removing this line means removing that too.
    errorPath: "index.html",
    allowNavigation: [serverHost],
    // The deploy is HTTPS and this app carries a session cookie. Neither of
    // these may be relaxed to make a local server easier to reach.
    cleartext: false,
    androidScheme: "https",
  },
  android: {
    allowMixedContent: false,
    // The one thing the shell tells the web app about itself, and the reason
    // it has to be the user agent rather than `window.Capacitor`: with a
    // remote `server.url` the native bridge is injected by evaluateJavascript
    // from onPageStarted, which is not guaranteed to have run by the time the
    // page's own <head> script does -- and that script is where «پردهٔ آغاز»
    // decides whether this load is a launch. A WebView also reports
    // `display-mode: browser`, so the check every installed PWA passes fails
    // here. The UA is on the very first byte of every request and survives
    // every navigation, so it is the only answer available early enough.
    appendUserAgent: "ActpactShell",
    // Android 15 forces every app targeting SDK 35 to draw edge to edge, and
    // this layout is not built for it: the top bar reads `env(safe-area-
    // inset-top)` nowhere, so the WebView would paint its own header under
    // the status bar's clock. "auto" insets the WebView on exactly the
    // versions that need it, which is what makes the app look the same on
    // Android 15 as it does on Android 10. ("force" would double the inset
    // on the versions that already leave room.)
    adjustMarginsForEdgeToEdge: "auto",
    // Off in the shipped build: it opens the WebView to chrome://inspect from
    // any machine the phone is plugged into. Phase 2 turns it on locally to
    // debug a real device and turns it back off here.
    webContentsDebuggingEnabled: false,
  },
  plugins: {
    SplashScreen: {
      // The native splash carries no mark (see the launch theme in
      // android/.../values/styles.xml): it is the app's ground colour and
      // nothing else, so the *only* logo a launch shows is «پردهٔ آغاز»,
      // drawing itself in from its first stroke. Two mechanisms on purpose,
      // and they do not overlap: the web app calls hide() the moment it has
      // painted, and this duration is the ceiling under it, so a page that
      // never runs its script cannot leave the member staring at a blank
      // window with no way out.
      launchAutoHide: true,
      launchShowDuration: 2500,
      // Zero: there is nothing to dissolve. Both sides are the same flat
      // colour, and a fade only delays the drawing the member is waiting on.
      launchFadeOutDuration: 0,
      backgroundColor: "#f8f4ee",
      androidScaleType: "CENTER_CROP",
      showSpinner: false,
    },
  },
};
