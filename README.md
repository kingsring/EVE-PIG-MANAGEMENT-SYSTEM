# EVE 工具箱分享版

这是一个本地运行的 EVE 多角色工具，包含技能点查看、市场行情、农场财务和配装规划。

## 快速启动

1. 安装 Python 3.12 或更高版本，安装时勾选 `Add Python to PATH`。
2. 双击 `启动.bat`。
3. 首次启动会创建 `.env`，并提示填写你自己的 EVE 开发者应用凭据。
4. 在 https://developers.eveonline.com 创建应用，Callback URL 填：
   `http://localhost:8000/callback`
5. 权限至少勾选：
   - `esi-skills.read_skills.v1`
   - `esi-skills.read_skillqueue.v1`
   - `esi-clones.read_implants.v1`
6. 将应用自己的 Client ID 和 Secret 填入 `.env`，然后再次双击 `启动.bat`。
7. 浏览器访问 `http://localhost:8000`。

每个使用者必须创建自己的 CCP 应用，不能共用别人的 Client Secret。

## 已包含

- 最新应用源码
- `data/item_index.db` 本地物品索引
- Python 依赖清单
- Windows 启动脚本

首次运行会在本机自动创建虚拟环境并安装依赖。物品索引已经附带，无需重新下载约 170MB 数据。

## 数据与隐私

- 角色令牌和本地数据只会保存在使用者自己的 `data/eve_esi.db`。
- 分享包不包含原作者的 `.env`、角色令牌、钱包数据或财务数据。
- 不要将运行后生成的 `.env` 或 `data/eve_esi.db` 上传到公开仓库。

## 常见问题

- 端口被占用：修改 `.env` 中的 `EVE_PORT`，并同步修改 CCP Callback URL。
- 页面打不开：确认 `启动.bat` 窗口仍在运行。
- 登录失败：检查 `.env` 的 Client ID、Secret 和 Callback URL 是否完全一致。
