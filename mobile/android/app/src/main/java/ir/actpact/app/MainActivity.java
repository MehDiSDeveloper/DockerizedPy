package ir.actpact.app;

import android.os.Bundle;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

/**
 * The whole of the native code in this project, and it exists for one
 * correction.
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
}
