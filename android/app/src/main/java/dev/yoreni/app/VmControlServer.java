package dev.yoreni.app;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.net.Socket;

/**
 * Loopback control channel for the VM that Podroid owns.
 *
 * The gateway runs inside the Linux rootfs and cannot send an Android
 * broadcast, so it asks this server instead. Only 127.0.0.1 is bound: the
 * caller is the gateway on the same device, and nothing else can reach it.
 */
public final class VmControlServer {

    public static final int PORT = YoreniRuntime.CONTROL_PORT;
    private static final String PACKAGE = "com.excp.podroid";
    private static final String RECEIVER = "com.excp.podroid.service.VmControlReceiver";
    private static final String START = "com.excp.podroid.action.START_VM";
    private static final String STOP = "com.excp.podroid.action.STOP_VM";

    private final Context context;

    public VmControlServer(Context context) {
        this.context = context.getApplicationContext();
    }

    /** Serve until the process dies; the thread is a daemon. */
    public void start() {
        Thread thread = new Thread(this::serve, "yoreni-vm-control");
        thread.setDaemon(true);
        thread.start();
    }

    private void serve() {
        try (ServerSocket server = new ServerSocket(PORT, 8, InetAddress.getByName("127.0.0.1"))) {
            YoreniRuntime.append("VM control listening on 127.0.0.1:" + PORT);
            while (!Thread.currentThread().isInterrupted()) {
                try (Socket client = server.accept()) {
                    client.setSoTimeout(5000);
                    BufferedReader reader = new BufferedReader(
                            new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
                    String action = reader.readLine();
                    String reply = handle(action == null ? "" : action.trim());
                    respond(client, reply);
                } catch (Exception exc) {
                    YoreniRuntime.append("VM control request failed: " + exc);
                }
            }
        } catch (Exception exc) {
            YoreniRuntime.append("VM control server stopped: " + exc);
        }
    }

    /** Answer as HTTP: the caller is urllib, which rejects a bare line. */
    private static void respond(Socket client, String reply) throws IOException {
        byte[] body = (reply + "\n").getBytes(StandardCharsets.UTF_8);
        boolean ok = reply.startsWith("ok");
        String head = "HTTP/1.1 " + (ok ? "200 OK" : "500 Internal Server Error") + "\r\n"
                + "Content-Type: text/plain; charset=utf-8\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Connection: close\r\n\r\n";
        OutputStream out = client.getOutputStream();
        out.write(head.getBytes(StandardCharsets.UTF_8));
        out.write(body);
        out.flush();
    }

    /** Accept both a bare action and an HTTP request line, so any client works. */
    private static String actionOf(String line) {
        String[] parts = line.trim().split("\\s+");
        if (parts.length >= 2 && parts[1].startsWith("/")) {
            String path = parts[1];
            return path.substring(path.lastIndexOf('/') + 1);
        }
        return parts.length > 0 ? parts[0] : "";
    }

    private String handle(String request) {
        String action = actionOf(request);
        switch (action) {
            case "start":
                return send(START, "started");
            case "stop":
                return send(STOP, "stopped");
            case "status":
                return installed() ? "ok running" : "error: Podroid is not installed";
            default:
                return "error: unknown action " + action;
        }
    }

    private String send(String broadcast, String success) {
        if (!installed()) {
            return "error: Podroid is not installed";
        }
        Intent intent = new Intent(broadcast);
        intent.setComponent(new ComponentName(PACKAGE, RECEIVER));
        context.sendBroadcast(intent);
        return "ok " + success;
    }

    private boolean installed() {
        try {
            context.getPackageManager().getPackageInfo(PACKAGE, 0);
            return true;
        } catch (PackageManager.NameNotFoundException exc) {
            return false;
        }
    }
}
