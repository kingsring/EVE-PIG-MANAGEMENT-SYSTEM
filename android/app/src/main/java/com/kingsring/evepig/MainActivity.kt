package com.kingsring.evepig

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.database.sqlite.SQLiteDatabase
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
import java.io.FileInputStream
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

        if (intent.getBooleanExtra(EXTRA_OPEN_SETTINGS, false)) {
            openSettings()
            return
        }

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
        status.setOnLongClickListener {
            openSettings()
            true
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
        val importButton = Button(this).apply {
            text = "导入数据"
            setOnClickListener { openImportPicker() }
        }
        val exportButton = Button(this).apply {
            text = "导出数据"
            setOnClickListener { openExportPicker() }
        }
        val privacyHint = TextView(this).apply {
            setTextColor(Color.LTGRAY)
            text = "导入/导出的数据库包含 EVE 登录令牌，请勿分享给他人。"
        }
        configPanel.addView(clientId)
        configPanel.addView(clientSecret)
        configPanel.addView(callback)
        configPanel.addView(startButton)
        configPanel.addView(importButton)
        configPanel.addView(exportButton)
        configPanel.addView(privacyHint)
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
        status.visibility = View.VISIBLE
        status.text = "修改凭据、导入或导出数据"
    }

    private fun databaseFile(): File = File(File(filesDir, "data"), "eve_esi.db")

    private fun openImportPicker() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "application/octet-stream"
        }
        startActivityForResult(intent, IMPORT_REQUEST)
    }

    private fun openExportPicker() {
        if (!databaseFile().exists()) {
            Toast.makeText(this, "还没有可导出的本地数据", Toast.LENGTH_SHORT).show()
            return
        }
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "application/octet-stream"
            putExtra(Intent.EXTRA_TITLE, "eve_esi.db")
        }
        startActivityForResult(intent, EXPORT_REQUEST)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        when (requestCode) {
            EXPORT_REQUEST -> exportDatabase(uri)
            IMPORT_REQUEST -> importDatabase(uri)
        }
    }

    private fun exportDatabase(uri: android.net.Uri) {
        val stopIntent = Intent(this, ServerService::class.java).setAction(ServerService.ACTION_STOP)
        startService(stopIntent)
        Thread({
            try {
                Thread.sleep(600)
                val db = databaseFile()
                if (!db.exists()) throw IllegalStateException("数据库不存在")
                try { SQLiteDatabase.openDatabase(db.path, null, SQLiteDatabase.OPEN_READWRITE).close() } catch (_: Throwable) {}
                contentResolver.openOutputStream(uri)?.use { output ->
                    FileInputStream(db).use { input -> input.copyTo(output) }
                } ?: throw IllegalStateException("无法打开导出位置")
                runOnUiThread { status.text = "数据已导出：$uri" }
            } catch (error: Throwable) {
                runOnUiThread { status.text = "导出失败：${error.message}" }
            }
        }, "eve-db-export").start()
    }

    private fun importDatabase(uri: android.net.Uri) {
        val stopIntent = Intent(this, ServerService::class.java).setAction(ServerService.ACTION_STOP)
        startService(stopIntent)
        Thread({
            try {
                Thread.sleep(600)
                val dataDir = File(filesDir, "data").apply { mkdirs() }
                val temp = File(dataDir, "eve_esi.import.db")
                contentResolver.openInputStream(uri)?.use { input -> temp.outputStream().use { input.copyTo(it) } }
                    ?: throw IllegalStateException("无法读取所选文件")
                val header = ByteArray(16)
                FileInputStream(temp).use { if (it.read(header) != 16) throw IllegalStateException("数据库文件为空") }
                if (header.toString(Charsets.US_ASCII) != "SQLite format 3 ") {
                    throw IllegalStateException("所选文件不是有效的 SQLite 数据库")
                }
                val db = databaseFile()
                if (db.exists()) db.copyTo(File(dataDir, "eve_esi.backup.db"), overwrite = true)
                temp.copyTo(db, overwrite = true)
                temp.delete()
                File(dataDir, "eve_esi.db-wal").delete()
                File(dataDir, "eve_esi.db-shm").delete()
                runOnUiThread {
                    status.text = "数据导入成功，正在重新启动服务"
                    val id = prefs.getString(KEY_CLIENT_ID, "") ?: ""
                    val secret = prefs.getString(KEY_CLIENT_SECRET, "") ?: ""
                    if (id.isNotBlank() && secret.isNotBlank()) startServerAndWait(id, secret)
                }
            } catch (error: Throwable) {
                runOnUiThread { status.text = "导入失败：${error.message}" }
            }
        }, "eve-db-import").start()
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
                    status.visibility = View.GONE
                    settingsButton.visibility = View.GONE
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

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        if (intent?.getBooleanExtra(EXTRA_OPEN_SETTINGS, false) == true) {
            setIntent(intent)
            openSettings()
        }
    }

    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    companion object {
        const val EXTRA_OPEN_SETTINGS = "open_settings"
        private const val EXPORT_REQUEST = 2001
        private const val IMPORT_REQUEST = 2002
        private const val KEY_CLIENT_ID = "client_id"
        private const val KEY_CLIENT_SECRET = "client_secret"
        private const val KEY_INDEX_VERSION = "index_version"
    }
}
