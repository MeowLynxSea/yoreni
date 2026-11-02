package dev.yoreni.app;

import android.app.Activity;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * Opens the WebUI once the gateway answers, and holds the splash until then.
 *
 * Everything heavy lives in YoreniService: this activity can be destroyed and
 * recreated without restarting the gateway.
 */
public class MainActivity extends Activity {

    private static final String WEBUI_URL = "http://127.0.0.1:" + YoreniRuntime.WEBUI_PORT + "/";
    /** Long enough for a cold first start, which unpacks the rootfs and pip. */
    private static final int WAIT_ATTEMPTS = 2400;

    private View splash;
    private TextView status;
    private TextView details;
    private TextView output;
    private View progress;
    private ScrollView logPanel;
    private WebView webview;
    private volatile boolean loaded;
    private volatile boolean showingLog;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        splash = findViewById(R.id.splash);
        status = findViewById(R.id.status);
        details = findViewById(R.id.details);
        output = findViewById(R.id.output);
        progress = findViewById(R.id.progress);
        logPanel = findViewById(R.id.log_panel);
        webview = findViewById(R.id.webview);

        details.setOnClickListener((view) -> setLogVisible(!showingLog));

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
        for (int attempt = 0; attempt < WAIT_ATTEMPTS; attempt++) {
            if (portOpen(YoreniRuntime.WEBUI_PORT)) {
                runOnUiThread(() -> {
                    loaded = true;
                    splash.setVisibility(View.GONE);
                    webview.setVisibility(View.VISIBLE);
                    webview.loadUrl(WEBUI_URL);
                });
                return;
            }
            render(YoreniRuntime.stage(), YoreniRuntime.failed());
            refreshLog();
            try {
                Thread.sleep(500);
            } catch (InterruptedException interrupted) {
                return;
            }
        }
        render(YoreniRuntime.stage(), true);
    }

    /** The waiting screen says what is happening, not what the process printed. */
    private void render(YoreniRuntime.Stage stage, boolean failed) {
        runOnUiThread(() -> {
            if (loaded) {
                return;
            }
            status.setText(failed ? R.string.splash_failed : label(stage));
            progress.setVisibility(failed ? View.INVISIBLE : View.VISIBLE);
            if (failed && !showingLog) {
                setLogVisible(true);
            }
        });
    }

    /** Keep an open detail panel live: the bootstrap keeps writing while it waits. */
    private void refreshLog() {
        if (!showingLog) {
            return;
        }
        String text = YoreniRuntime.log();
        runOnUiThread(() -> {
            if (!showingLog || text.equals(output.getText().toString())) {
                return;
            }
            boolean follow = atLogBottom();
            output.setText(text);
            if (follow) {
                logPanel.post(() -> logPanel.fullScroll(View.FOCUS_DOWN));
            }
        });
    }

    /** Only follow new lines when the reader has not scrolled back. */
    private boolean atLogBottom() {
        View child = logPanel.getChildAt(0);
        if (child == null) {
            return true;
        }
        int slack = Math.round(24 * getResources().getDisplayMetrics().density);
        return child.getBottom() - logPanel.getHeight() - logPanel.getScrollY() <= slack;
    }

    private static int label(YoreniRuntime.Stage stage) {
        switch (stage) {
            case RUNTIME:
                return R.string.splash_runtime;
            case ENVIRONMENT:
                return R.string.splash_environment;
            case GATEWAY:
                return R.string.splash_gateway;
            default:
                return R.string.splash_preparing;
        }
    }

    private void setLogVisible(boolean visible) {
        showingLog = visible;
        logPanel.setVisibility(visible ? View.VISIBLE : View.GONE);
        details.setText(visible ? R.string.splash_details_hide : R.string.splash_details);
        if (visible) {
            output.setText(YoreniRuntime.log());
            logPanel.post(() -> logPanel.fullScroll(View.FOCUS_DOWN));
        }
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
