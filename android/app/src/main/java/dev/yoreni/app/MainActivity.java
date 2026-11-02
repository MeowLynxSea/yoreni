package dev.yoreni.app;

import android.app.Activity;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.TextView;

/**
 * Shows the WebUI once the gateway answers, and the bootstrap log until then.
 *
 * Everything heavy lives in YoreniService: this activity can be destroyed and
 * recreated without restarting the gateway.
 */
public class MainActivity extends Activity {

    private static final String WEBUI_URL = "http://127.0.0.1:" + YoreniRuntime.WEBUI_PORT + "/";

    private TextView output;
    private WebView webview;
    private volatile boolean loaded;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        output = findViewById(R.id.output);
        webview = findViewById(R.id.webview);

        WebSettings settings = webview.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        // The WebUI talks to 127.0.0.1 only; nothing else may be reached from it.
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        webview.setWebViewClient(new WebViewClient());

        Intent service = new Intent(this, YoreniService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(service);
        } else {
            startService(service);
        }

        new Thread(this::awaitGateway, "yoreni-webui").start();
    }

    @Override
    protected void onDestroy() {
        if (webview != null) {
            webview.destroy();
        }
        super.onDestroy();
    }

    /** Load the WebUI as soon as the port answers, then stop polling. */
    private void awaitGateway() {
        for (int attempt = 0; attempt < 1800; attempt++) {
            if (portOpen(YoreniRuntime.WEBUI_PORT)) {
                runOnUiThread(() -> {
                    loaded = true;
                    output.setVisibility(View.GONE);
                    webview.setVisibility(View.VISIBLE);
                    webview.loadUrl(WEBUI_URL);
                });
                return;
            }
            runOnUiThread(() -> {
                if (!loaded) {
                    output.setText(YoreniRuntime.log());
                }
            });
            try {
                Thread.sleep(1000);
            } catch (InterruptedException interrupted) {
                return;
            }
        }
        runOnUiThread(() -> output.setText(YoreniRuntime.log() + "\nthe gateway did not start"));
    }

    private boolean portOpen(int port) {
        try (java.net.Socket socket = new java.net.Socket()) {
            socket.connect(new java.net.InetSocketAddress("127.0.0.1", port), 400);
            return true;
        } catch (Exception exc) {
            return false;
        }
    }
}
