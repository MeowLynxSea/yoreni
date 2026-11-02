package dev.yoreni.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Keeps the gateway alive when the screen is off or the app is in the
 * background.
 *
 * The runtime is a child of this process, so a foreground service is what keeps
 * it from being frozen or reclaimed: Android stops background processes long
 * before it touches a foreground one, and the wake lock covers the CPU while
 * the screen is off.
 */
public class YoreniService extends Service {

    private static final String CHANNEL_ID = "yoreni-gateway";
    private static final int NOTIFICATION_ID = 1;

    private PowerManager.WakeLock wakeLock;
    private Thread worker;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        startForeground(NOTIFICATION_ID, notification());

        PowerManager power = getSystemService(PowerManager.class);
        if (power != null) {
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "yoreni:gateway");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        }

        new VmControlServer(this).start();

        worker = new Thread(() -> YoreniRuntime.run(this), "yoreni-runtime");
        worker.start();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // A sticky service is restarted with a null intent; the runtime is
        // already running in that case and nothing else is needed.
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        if (worker != null) {
            worker.interrupt();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "Yoreni", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Keeps the Yoreni gateway running");
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private Notification notification() {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(
                this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, CHANNEL_ID)
                .setContentTitle("Yoreni")
                .setContentText("The gateway is running")
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentIntent(pending)
                .setOngoing(true)
                .build();
    }
}
