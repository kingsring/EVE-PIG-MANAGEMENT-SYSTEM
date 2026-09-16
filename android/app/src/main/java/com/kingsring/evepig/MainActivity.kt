package com.kingsring.evepig

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.net.HttpURLConnection
import java.io.File
import java.net.URL

class MainActivity : Activity() {
    private lateinit var prefs: SharedPreferences
    private lateinit var root: LinearLayout
    private lateinit var configPanel: LinearLayout
    private lateinit var webView: WebView
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private lateinit var clientId: EditText
    private lateinit var clientSecret: EditText
    private lateinit var settingsButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = createEncryptedPrefs()
        copyItemIndexIfNeeded()
        buildUi()
        requestNotificationPermissionIfNeeded()

        val savedId = prefs.getString(KEY_CLIENT_ID, "") ?: ""
        val savedSecret = prefs.getString(KEY_CLIENT_SECRET, "") ?: ""
        if (savedId.isNotBlank() && savedSecret.isNotBlank()) {
            showWebView()
            startServerAndWait(savedId, savedSecret)
        }
    }

    private fun createEncryptedPrefs(): SharedPreferences {
        val masterKey = MasterKey.Builder(this)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        return EncryptedSharedPreferences.create(
            this,
            "secure_settings",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    private fun copyItemIndexIfNeeded() {
        val versionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            packageManager.getPackageInfo(packageName, 0).longVersionCode
        } else {
            @Suppress("DEPRECATION")
            packageManager.getPackageInfo(packageName, 0).versionCode.toLong()
        }
        val copiedVersion = prefs.getLong(KEY_INDEX_VERSION, -1L)
        val target = java.io.File(filesDir, "data/item_index.db")
        if (copiedVersion == versionCode && target.exists()) return
        target.parentFile?.mkdirs()
        assets.open("data/item_index.db").use { input ->
            target.outputStream().use { output -> input.copyTo(output) }
        }
        prefs.edit().putLong(KEY_INDEX_VERSION, versionCode).apply()
    }

    private fun buildUi() {
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(13, 17, 23))
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        setContentView(root)

        status = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 15f
            setPadding(28, 28, 28, 12)
            text = "首次使用请填写 CCP 开发者应用凭据"
        }
        root.addView(status)

        settingsButton = Button(this).apply {
            text = "设置"
            visibility = View.GONE
            setOnClickListener { openSettings() }
        }
        root.addView(settingsButton)

        configPanel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 8, 28, 24)
        }
        clientId = EditText(this).apply {
            hint = "EVE_CLIENT_ID"
            setSingleLine(true)
        }
        clientSecret = EditText(this).apply {
            hint = "EVE_CLIENT_SECRET"
            setSingleLine(true)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val callback = TextView(this).apply {
            setTextColor(Color.LTGRAY)
            text = "Callback URL: http://localhost:8000/callback"
        }
        val startButton = Button(this).apply {
            text = "启动本地服务"
        }
        startButton.setOnClickListener { startFromForm() }
        configPanel.addView(clientId)
        configPanel.addView(clientSecret)
        configPanel.addView(callback)
        configPanel.addView(startButton)
        root.addView(configPanel)

        progress = ProgressBar(this).apply { visibility = View.GONE }
        root.addView(progress)

        webView = WebView(this).apply {
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            webViewClient = WebViewClient()
            webChromeClient = WebChromeClient()
        }
        root.addView(webView)
    }

    private fun startFromForm() {
        val id = clientId.text.toString().trim()
        val secret = clientSecret.text.toString().trim()
        if (id.isBlank() || secret.isBlank()) {
            Toast.makeText(this, "请填写 Client ID 和 Secret", Toast.LENGTH_SHORT).show()
            return
        }
        prefs.edit().putString(KEY_CLIENT_ID, id).putString(KEY_CLIENT_SECRET, secret).apply()
        showWebView()
        startServerAndWait(id, secret)
    }

    private fun showWebView() {
        configPanel.visibility = View.GONE
        settingsButton.visibility = View.VISIBLE
        progress.visibility = View.VISIBLE
        webView.visibility = View.VISIBLE
        status.text = "正在启动本地服务…"
    }

    private fun openSettings() {
        val stopIntent = Intent(this, ServerService::class.java).setAction(ServerService.ACTION_STOP)
        startService(stopIntent)
        clientId.setText(prefs.getString(KEY_CLIENT_ID, "") ?: "")
        clientSecret.setText(prefs.getString(KEY_CLIENT_SECRET, "") ?: "")
        configPanel.visibility = View.VISIBLE
        settingsButton.visibility = View.GONE
        progress.visibility = View.GONE
        webView.visibility = View.GONE
        status.text = "修改凭据后重新启动本地服务"
    }

    private fun startServerAndWait(clientId: String, clientSecret: String) {
        val intent = Intent(this, ServerService::class.java).apply {
            action = ServerService.ACTION_START
            putExtra(ServerService.EXTRA_CLIENT_ID, clientId)
            putExtra(ServerService.EXTRA_CLIENT_SECRET, clientSecret)
            putExtra(ServerService.EXTRA_PORT, 8000)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(intent) else startService(intent)

        Thread({
            val errorFile = File(filesDir, "server_error.txt")
            var ready = false
            var startupError = ""
            for (attempt in 0 until 120) {
                if (errorFile.exists()) {
                    startupError = runCatching { errorFile.readText().trim() }.getOrDefault("")
                    break
                }
                try {
                    val conn = URL("http://127.0.0.1:8000/").openConnection() as HttpURLConnection
                    conn.connectTimeout = 800
                    conn.readTimeout = 800
                    if (conn.responseCode in 200..499) ready = true
                    conn.disconnect()
                } catch (_: Exception) {
                    Thread.sleep(500)
                }
                if (ready) break
            }
            runOnUiThread {
                progress.visibility = View.GONE
                if (ready) {
                    status.text = "本地服务已启动"
                    webView.loadUrl("http://127.0.0.1:8000/")
                } else {
                    val detail = startupError.lineSequence().toList().takeLast(5).joinToString("\n")
                    status.text = if (detail.isBlank()) "服务启动失败，请检查凭据或 Logcat" else "服务启动失败：\n$detail"
                    Toast.makeText(this, "本地服务启动失败", Toast.LENGTH_LONG).show()
                }
            }
        }, "eve-server-health").start()
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
        }
    }

    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    companion object {
        private const val KEY_CLIENT_ID = "client_id"
        private const val KEY_CLIENT_SECRET = "client_secret"
        private const val KEY_INDEX_VERSION = "index_version"
    }
}
