package ir.actpact.app;

import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

/**
 * The whole of the native code in this project, and it exists for two
 * corrections.
 *
 * <p>Capacitor's {@code server.errorPath} is what puts this app's own «به
 * سرور وصل نشدیم» page in front of the member instead of the WebView's
 * built-in network error -- the shell has no address bar, so a raw
 * {@code net::ERR_*} screen is a dead end. But Capacitor's web view client
 * loads that page from <em>two</em> callbacks: {@code onReceivedError}, which
 * is a real transport failure, and {@code onReceivedHttpError}, which is any
 * 4xx or 5xx the server answered a navigation with.
 *
 * <p>The second one is wrong for this app, and not marginally. A 404 here is
 * a designed answer with a page of its own behind it: somebody else's
 * profile, an admin screen a member reached, a group they are not in. Letting
 * a 404 swap in the offline page would tell a member their internet is down
 * while they are looking at a perfectly healthy server -- and it would hide
 * the app's own error page, which is the one that says what actually
 * happened. The same goes for a 500, where "no connection" is a lie that
 * costs a bug report.
 *
 * <p>So the HTTP half is overridden to do nothing, which is exactly what
 * {@link android.webkit.WebViewClient} itself does: the response body the
 * server sent renders, like it does in a browser. The transport half is left
 * alone, because there the fallback page is the only thing there is.
 *
 * <p><b>The second correction is the session cookie.</b> This app's whole
 * auth is one persistent cookie -- there is no server-side session store, so
 * losing it is losing the login. A WebView writes its cookie jar to disk on
 * its own schedule, and a process that is killed before that write happens
 * takes every cookie set since the last one with it. Swiping the app out of
 * recents is exactly that kill, which is why the symptom is "it forgets me
 * every time I properly close it" rather than a steady logout. A browser tab
 * never shows this, because Chrome flushes on its own lifecycle.
 *
 * <p>{@code CookieManager.flush()} is the one call that makes the write
 * happen now. {@code onPause} is the right place for it: it is the last
 * callback Android guarantees before a process may be killed, and it is cheap
 * enough to run on every backgrounding.
 */
public class MainActivity extends BridgeActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Null when the device has no usable WebView at all; BridgeActivity
        // has already shown its own "no web view" layout in that case.
        if (bridge == null) {
            return;
        }

        bridge.setWebViewClient(
            new BridgeWebViewClient(bridge) {
                @Override
                public void onReceivedHttpError(
                    WebView view,
                    WebResourceRequest request,
                    WebResourceResponse errorResponse
                ) {
                    // Deliberately empty: the status code belongs to the page,
                    // not to the shell. See the class comment.
                }
            }
        );
    }

    // `public`, not `protected`: BridgeActivity widens onPause(), and Java
    // refuses an override that narrows visibility.
    @Override
    public void onPause() {
        super.onPause();
        // Write the cookie jar to disk while the process is still alive. See
        // the class comment: without this, the session cookie does not
        // survive the app being swiped out of recents.
        CookieManager.getInstance().flush();
    }
}
