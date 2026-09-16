package com.kingsring.evepig

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import java.io.File
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class ServerService : Service() {
    companion object {
        const val ACTION_START = "com.kingsring.evepig.START"
        const val ACTION_STOP = "com.kingsring.evepig.STOP"
        const val EXTRA_CLIENT_ID = "client_id"
        const val EXTRA_CLIENT_SECRET = "client_secret"
        const val EXTRA_PORT = "port"
        private const val CHANNEL_ID = "eve_server"
        private const val NOTIFICATION_ID = 1001
    }

    @Volatile private var serverThread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopServer()
            stopSelf()
            return START_NOT_STICKY
        }

        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification())

        if (serverThread?.isAlive == true) return START_NOT_STICKY

        val clientId = intent?.getStringExtra(EXTRA_CLIENT_ID) ?: ""
        val clientSecret = intent?.getStringExtra(EXTRA_CLIENT_SECRET) ?: ""
        val port = intent?.getIntExtra(EXTRA_PORT, 8000) ?: 8000

        val errorFile = File(filesDir, "server_error.txt")
        if (errorFile.exists()) errorFile.delete()

        serverThread = Thread({
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }
                val dataDir = File(filesDir, "data")
                dataDir.mkdirs()
                Python.getInstance()
                    .getModule("android_bridge")
                    .callAttr(
                        "start_server",
                        clientId,
                        clientSecret,
                        "http://localhost:$port/callback",
                        dataDir.absolutePath,
                        "127.0.0.1",
                        port,
                    )
            } catch (error: Throwable) {
                Log.e("EVE_SERVER", "Python server failed", error)
                errorFile.writeText(error.stackTraceToString())
                stopSelf()
            }
        }, "eve-fastapi-server").also { it.start() }

        return START_NOT_STICKY
    }

    override fun onDestroy() {
        stopServer()
        super.onDestroy()
    }

    private fun stopServer() {
        try {
            if (Python.isStarted()) {
                Python.getInstance().getModule("android_bridge").callAttr("stop_server")
            }
        } catch (_: Throwable) {
            // Server may not have started.
        }
        serverThread?.interrupt()
        serverThread = null
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "本地服务",
                    NotificationManager.IMPORTANCE_LOW,
                )
            )
        }
    }

    private fun buildNotification(): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, ServerService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setSmallIcon(R.drawable.ic_server)
            .setContentTitle("EVE 猪场管理系统")
            .setContentText("本地服务正在运行")
            .setContentIntent(openIntent)
            .addAction(0, "停止服务", stopIntent)
            .setOngoing(true)
            .build()
    }
}
