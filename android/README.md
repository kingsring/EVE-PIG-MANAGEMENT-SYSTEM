# Android APK 构建说明

Android 版本完全在手机本地运行：

- Kotlin + Android WebView
- Chaquopy 内置 Python 3.12
- 本地 FastAPI/Uvicorn 服务
- SQLite 数据保存在应用私有目录
- EVE 令牌只保存在手机内部
- `data/item_index.db` 随 APK 打包，首次启动复制到应用私有目录

## GitHub Actions 构建

仓库上传后：

1. 打开 GitHub 的 **Actions**。
2. 选择 **Build Android APK**。
3. 点击 **Run workflow**。
4. 构建完成后下载 `EVE-Pig-Management-APK` 工件。

没有配置签名密钥时会生成可安装的 debug APK。正式 Release 需要配置签名密钥。

## 正式签名

在本地安装 Java 17 后生成一次并长期妥善保存 keystore：

```bash
keytool -genkeypair -v -keystore eve-pig-release.jks -alias evepig -keyalg RSA -keysize 4096 -validity 10000
```

将 keystore 转为 Base64，并在 GitHub 仓库设置以下 Secrets：

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

设置后再次运行 GitHub Actions，会构建 release APK。签名密钥丢失后，无法为已安装应用继续提供升级。

## 本地 Android Studio 构建

先同步 Python 源码：

```bash
python android/sync_python.py
```

然后用 Android Studio 打开 `android/` 目录并运行构建。

## 数据导入与导出

APK 设置页提供：

- `导出数据`：通过 Android 文件选择器导出 `eve_esi.db`
- `导入数据`：选择电脑版 `eve_esi.db`，替换手机本地数据库

导入流程：

1. 关闭 Windows 版服务。
2. 把电脑上的 `data/eve_esi.db` 发送到手机。
3. 首次打开 APK，点击“导入数据”。
4. 选择 `eve_esi.db`。
5. 填写与电脑版相同的 CCP Client ID 和 Secret。
6. 启动本地服务。

应用成功启动后，顶部“设置”按钮会自动隐藏。之后需要重新进入设置或迁移数据时，长按顶部的“本地服务已启动”状态文字。

导出的数据库包含 EVE refresh token，只能用于自己的设备迁移，不要分享给他人。

## Android SSO

应用继续使用：

```text
http://localhost:8000/callback
```

登录页面会在应用内 WebView 中打开，回调会返回手机上的本地 FastAPI 服务。
