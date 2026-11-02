package dev.yoreni.app;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.net.InetAddress;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Owns the Linux runtime: unpacks proot, the rootfs and CPython on first run,
 * then keeps one proot process alive that serves the gateway.
 *
 * It runs on its own thread and never returns; the service owns its lifetime.
 */
public final class YoreniRuntime {

    /** Port the WebUI is served on; the WebView loads it when it answers. */
    public static final int WEBUI_PORT = 8765;
    /** Port the Android host exposes for VM control from inside the rootfs. */
    public static final int CONTROL_PORT = 8766;

    /** What the bootstrap is doing, for the screen that waits on it. */
    public enum Stage {
        PREPARING,
        RUNTIME,
        ENVIRONMENT,
        GATEWAY,
    }

    private static final StringBuilder LOG = new StringBuilder();
    private static volatile Stage stage = Stage.PREPARING;
    private static volatile boolean failed;

    private YoreniRuntime() {
    }

    public static synchronized String log() {
        return LOG.toString();
    }

    public static Stage stage() {
        return stage;
    }

    public static boolean failed() {
        return failed;
    }

    public static synchronized void append(String line) {
        Log.i("yoreni", line);
        // run.sh marks each phase on its own line; the marker is protocol, not
        // something the owner should read.
        if (note(line)) {
            return;
        }
        LOG.append(line).append('\n');
        if (LOG.length() > 20000) {
            LOG.delete(0, LOG.length() - 20000);
        }
    }

    /** Advance the reported stage from one of run.sh's markers. */
    private static boolean note(String line) {
        String marker = line.trim();
        if (!marker.startsWith("STEP-")) {
            return false;
        }
        if (marker.equals("STEP-DEPS")) {
            stage = Stage.RUNTIME;
        } else if (marker.equals("STEP-CONFIG") || marker.equals("STEP-SANDBOX")) {
            stage = Stage.ENVIRONMENT;
        } else if (marker.equals("STEP-SERVE")) {
            stage = Stage.GATEWAY;
        }
        return true;
    }

    /** Blocking entry point; returns only when the gateway exits. */
    public static void run(Context context) {
        failed = false;
        stage = Stage.PREPARING;
        try {
            bootstrap(context);
        } catch (Exception exc) {
            failed = true;
            append("FAILED: " + exc);
        }
    }

    private static void bootstrap(Context context) throws IOException, InterruptedException {
        File files = context.getFilesDir();
        File rootfs = new File(files, "ubuntu");
        File tmp = new File(files, "tmp");
        //noinspection ResultOfMethodCallIgnored
        tmp.mkdirs();

        // proot and its loader live in the native library directory: Android
        // refuses to exec from the writable data directory, but the extracted
        // library directory is read-only and executable.
        String nativeDir = context.getApplicationInfo().nativeLibraryDir;
        File proot = new File(nativeDir, "libproot.so");
        File loader = new File(nativeDir, "libproot_loader.so");

        if (!new File(rootfs, "etc/os-release").isFile()) {
            File archive = new File(files, "rootfs.bin");
            //noinspection ResultOfMethodCallIgnored
            rootfs.mkdirs();
            copyAsset(context, "rootfs.bin", archive);
            append("extracting rootfs (" + archive.length() / 1024 / 1024 + " MiB)");
            long started = System.currentTimeMillis();
            exec(0, null, "/system/bin/tar", "xzf", archive.getAbsolutePath(),
                    "-C", rootfs.getAbsolutePath());
            append("extracted in " + (System.currentTimeMillis() - started) + " ms");
        }

        if (!new File(rootfs, "opt/python/bin/python3").isFile()) {
            File runtime = new File(files, "python.bin");
            copyAsset(context, "python.bin", runtime);
            append("installing CPython into the rootfs");
            exec(0, null, "/system/bin/tar", "xzf", runtime.getAbsolutePath(),
                    "-C", new File(rootfs, "opt").getAbsolutePath());
        }

        if (!new File(rootfs, "opt/probe.py").isFile()) {
            File source = new File(files, "yoreni.bin");
            copyAsset(context, "yoreni.bin", source);
            append("unpacking the yoreni package");
            exec(0, null, "/system/bin/tar", "xzf", source.getAbsolutePath(),
                    "-C", new File(rootfs, "opt").getAbsolutePath());
        }

        // Small enough to refresh on every launch, which keeps an installed app
        // in step with a rebuilt APK.
        copyAsset(context, "probe.py", new File(rootfs, "opt/probe.py"));
        copyAsset(context, "run.sh", new File(rootfs, "opt/run.sh"));
        copyAsset(context, "requirements.txt", new File(rootfs, "tmp/requirements.txt"));

        writeResolvConf(context, new File(rootfs, "etc/resolv.conf"));
        // Android hands out IPv6 records the emulator and many carriers cannot
        // reach, and every connect then waits for the AAAA timeout.
        writeText(new File(rootfs, "etc/gai.conf"), "precedence ::ffff:0:0/96  100\n");

        Map<String, String> env = Map.of(
                "PROOT_LOADER", loader.getAbsolutePath(),
                "PROOT_TMP_DIR", tmp.getAbsolutePath(),
                "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME", "/root",
                "TERM", "dumb",
                "YORENI_ANDROID_CONTROL_PORT", Integer.toString(CONTROL_PORT));

        List<String> cmd = new ArrayList<>();
        cmd.add(proot.getAbsolutePath());
        cmd.add("--kill-on-exit");
        cmd.add("-0");
        cmd.add("-r");
        cmd.add(rootfs.getAbsolutePath());
        cmd.add("-w");
        cmd.add("/root");
        for (String tree : new String[]{"/dev", "/proc", "/sys"}) {
            cmd.add("-b");
            cmd.add(tree);
        }
        cmd.add("/bin/sh");
        cmd.add("-c");
        cmd.add("sh /opt/run.sh");

        append("$ proot -0 -r ubuntu sh /opt/run.sh");
        int code = exec(0, env, cmd.toArray(new String[0]));
        failed = true;
        append("[gateway exited " + code + "]");
    }

    /**
     * Android has no /etc/resolv.conf, so the resolver configuration the system
     * currently uses is copied into the rootfs before anything reachable by
     * name runs inside it.
     */
    private static void writeResolvConf(Context context, File target) {
        try {
            ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
            Network network = manager == null ? null : manager.getActiveNetwork();
            LinkProperties properties = network == null ? null : manager.getLinkProperties(network);
            List<InetAddress> servers = properties == null ? null : properties.getDnsServers();
            if (servers == null || servers.isEmpty()) {
                append("no DNS servers from the system");
                return;
            }
            File parent = target.getParentFile();
            if (parent != null) {
                //noinspection ResultOfMethodCallIgnored
                parent.mkdirs();
            }
            try (PrintWriter writer = new PrintWriter(target, "UTF-8")) {
                for (InetAddress server : servers) {
                    writer.println("nameserver " + server.getHostAddress());
                }
            }
            append("resolv.conf: " + servers);
        } catch (Exception exc) {
            append("could not write resolv.conf: " + exc);
        }
    }

    private static void writeText(File target, String content) throws IOException {
        File parent = target.getParentFile();
        if (parent != null) {
            //noinspection ResultOfMethodCallIgnored
            parent.mkdirs();
        }
        try (FileOutputStream out = new FileOutputStream(target)) {
            out.write(content.getBytes("UTF-8"));
        }
    }

    private static int exec(long timeoutMs, Map<String, String> env, String... command)
            throws IOException, InterruptedException {
        ProcessBuilder builder = new ProcessBuilder(command).redirectErrorStream(true);
        if (env != null) {
            builder.environment().putAll(env);
        }
        Process process = builder.start();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(process.getInputStream()))) {
            String line;
            while ((line = reader.readLine()) != null) {
                append("  " + line);
            }
        }
        if (timeoutMs > 0) {
            if (!process.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)) {
                process.destroyForcibly();
                return -1;
            }
        }
        return process.waitFor();
    }

    private static void copyAsset(Context context, String assetPath, File target)
            throws IOException {
        File parent = target.getParentFile();
        if (parent != null) {
            //noinspection ResultOfMethodCallIgnored
            parent.mkdirs();
        }
        try (InputStream in = context.getAssets().open(assetPath);
             FileOutputStream out = new FileOutputStream(target)) {
            byte[] buffer = new byte[1 << 16];
            int read;
            while ((read = in.read(buffer)) > 0) {
                out.write(buffer, 0, read);
            }
        }
    }
}
