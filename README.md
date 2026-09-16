# EVE 猪场管理系统

这是一个完全本地运行的多角色管理工具，支持：

- 技能点、技能队列和实时训练进度
- Alpha/Omega 账号级状态推断
- 个人钱包和可访问军团钱包总 ISK
- 按天走势图与总 ISK 追踪
- 市场行情、财务管理和配装规划
- 训练方案优化与历史完成节点
- Windows 本地运行版和 Android APK 构建工程

## Windows 快速启动

1. 安装 Python 3.12 或更高版本，安装时勾选 `Add Python to PATH`。
2. 双击 `启动.bat`。
3. 首次启动会自动创建 `.env`，填写自己的 CCP 开发者应用凭据。
4. 再次双击 `启动.bat`，浏览器访问 `http://localhost:8000`。

CCP Callback URL 必须为：

```text
http://localhost:8000/callback
```

按需申请以下权限：

- `esi-skills.read_skills.v1`
- `esi-skills.read_skillqueue.v1`
- `esi-wallet.read_character_wallet.v1`
- `esi-wallet.read_corporation_wallets.v1`
- `esi-clones.read_implants.v1`

每个使用者必须创建自己的 CCP 应用，不能共用他人的 Client Secret。

## Android APK

项目包含完整的 Android 工程和 GitHub Actions 构建流程。上传到 GitHub 后：

1. 打开仓库的 **Actions**。
2. 选择 **Build Android APK**。
3. 点击 **Run workflow**。
4. 下载 `EVE-Pig-Management-APK` 工件。

APK 完全在手机本地运行：

- Chaquopy 内置 Python 3.12
- 本地 FastAPI/Uvicorn
- Android WebView 显示现有界面
- SQLite 和 EVE 令牌保存在应用私有目录
- 物品索引随 APK 首次启动复制
- CCP 凭据使用 Android 加密存储

正式 APK 签名说明见 `android/README.md`。

## 隐私说明

分享包不包含原作者的：

- `.env`
- EVE 角色令牌
- `data/eve_esi.db`
- 钱包、军团钱包、配装或财务数据
- 日志和截图

运行后生成的 `.env` 和 `data/eve_esi.db` 不要上传到公开仓库。
