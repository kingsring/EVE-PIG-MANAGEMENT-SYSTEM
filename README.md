# EVE 猪场管理系统

完全本地运行的多角色管理工具，支持技能点、军团钱包、总 ISK 走势、市场行情、财务、配装规划和训练方案。

## Windows 启动

1. 安装 Python 3.12+，勾选 `Add Python to PATH`。
2. 双击 `启动.bat`。
3. 首次启动填写自己的 CCP Client ID 和 Secret，再次双击启动。

CCP Callback URL：

```text
http://localhost:8000/callback
```

## Android APK

仓库包含 Chaquopy + Kotlin Android 工程和 GitHub Actions 构建流程。

上传 GitHub 后：

1. 打开 `Actions`。
2. 选择 `Build Android APK`。
3. 点击 `Run workflow`。
4. 构建完成后下载 `EVE-Pig-Management-APK`。

APK 完全在手机本地运行，角色令牌和 SQLite 数据保存在应用私有目录。未配置签名密钥时生成可安装的 debug APK；正式 Release 签名见 `android/README.md`。

## 隐私

分享包不包含原作者的 `.env`、角色令牌、`data/eve_esi.db`、钱包、财务或配装数据。
