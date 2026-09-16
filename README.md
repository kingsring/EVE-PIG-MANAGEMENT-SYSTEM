# EVE 猪场管理系统

完全本地运行的 EVE 多角色管理工具，支持 Windows 和 Android。

## 功能

- 多角色技能点、技能队列和实时训练进度
- Alpha/Omega 账号级状态推断
- 个人钱包与有权限的军团钱包总 ISK
- 每日总 ISK、可提取技能器走势
- 市场行情、财务管理和配装规划
- 技能训练方案最少重置优化
- 历史完成技能节点
- 数据导入、导出和本地迁移

## Windows 启动

1. 安装 Python 3.12+，安装时勾选 `Add Python to PATH`。
2. 双击 `启动.bat`。
3. 首次启动会生成 `.env`，填写自己的 CCP 开发者应用凭据。
4. 再次双击 `启动.bat`。
5. 浏览器访问 `http://localhost:8000`。

CCP Callback URL：

```text
http://localhost:8000/callback
```

按功能需要申请以下权限：

- `esi-skills.read_skills.v1`
- `esi-skills.read_skillqueue.v1`
- `esi-wallet.read_character_wallet.v1`
- `esi-wallet.read_corporation_wallets.v1`
- `esi-clones.read_implants.v1`

## Android APK

项目包含完全本地运行的 Android 工程：

- Kotlin + Android WebView
- Chaquopy 内置 Python 3.12
- 本地 FastAPI/Uvicorn 服务
- SQLite、CCP 凭据和 EVE 令牌保存在应用私有目录
- `data/item_index.db` 随 APK 打包，首次启动复制到私有目录
- 不依赖外部业务服务器

在 GitHub 仓库中运行：

```text
Actions → Build Android APK → Run workflow
```

构建完成后，在运行记录的 `Artifacts` 中下载 APK。

没有配置签名密钥时会生成 debug APK。正式 Release APK 的签名方法见 [android/README.md](android/README.md)。

## 数据迁移

### Windows → Android

1. 关闭 Windows 版服务。
2. 复制电脑上的 `data/eve_esi.db` 到手机。
3. 首次打开 APK，在设置页点击“导入数据”。
4. 选择 `eve_esi.db`。
5. 填写与电脑版相同的 CCP Client ID 和 Secret。
6. 启动本地服务。

数据导入后会保留角色、令牌、军团钱包、财务、配装和训练方案。

### Android → Windows

1. 打开 APK 顶部设置。
2. 点击“导出数据”。
3. 将导出的 `eve_esi.db` 放到 Windows 项目的 `data/` 目录。
4. 使用相同的 CCP 应用凭据启动 Windows 版。

应用成功启动后，顶部设置按钮会自动隐藏。需要再次进入设置时，长按顶部的“本地服务已启动”状态文字。

## 数据安全

- Windows：`.env` 和 `data/eve_esi.db` 不要上传或分享。
- Android：数据库和 CCP 凭据保存在应用私有目录。
- 导出的 `eve_esi.db` 包含 EVE refresh token，只能用于自己的设备迁移。
- 每个使用者必须创建自己的 CCP 应用，不能共用他人的 Client Secret。

## 测试

```powershell
python -m pytest -q
```

## 物品索引

`data/item_index.db` 已随项目提供。如果缺失，可重新构建：

```powershell
python -m app.name_index --build
```
