# EVE 猪场管理器

本地自用的 Web 应用：通过 CCP 官方 EVE SSO 登录并绑定多个游戏角色，展示每个角色的**总技能点**与**技能队列（含当前训练进度）**。

- 后端：Python 3.12 + FastAPI + httpx + SQLite（标准库）
- 前端：FastAPI 直接托管的单页 HTML/CSS/原生 JS（无需 Node）
- 仅监听 `127.0.0.1:8000`，数据存本机 `data/eve_esi.db`

## 一、注册 CCP 开发者应用（一次性，需你自己完成）

1. 用 EVE 账号登录 https://developers.eveonline.com ，进入 Applications → Create New Application。
2. Connection Type 选择 **Authentication & API Access**；Permissions 勾选：
   - `esi-skills.read_skills.v1`（读取技能点）
   - `esi-skills.read_skillqueue.v1`（读取技能队列）
3. Callback URL 填：`http://localhost:8000/callback`
4. 创建后记下 **Client ID** 和 **Secret Key**。
   - 注意：若之后修改权限范围，所有已绑定角色都需要重新授权。




## 二、安装与配置

```
   点击 启动.bat 一键启动  
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动  
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动  
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动  
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动  
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动  
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动
   点击 启动.bat 一键启动  
```

```powershell
cd D:\game\eve_esi
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 复制配置模板并填入你的 Client ID / Secret
Copy-Item .env.example .env
# 用编辑器打开 .env 填写 EVE_CLIENT_ID / EVE_CLIENT_SECRET
```

## 三、启动

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 **http://localhost:8000**（请始终使用 localhost，与回调地址保持一致），点击右上角「＋ 添加角色」→ 跳转 EVE 官方登录页 → 选择角色并授权 → 自动回到首页显示数据。同一账号下的多个角色需分别登录一次。

- **刷新**：卡片上的“刷新”按钮立即更新该角色；打开首页时数据超过 5 分钟会自动刷新。
- **移除**：删除该角色及其本地令牌与缓存。
- 若角色“授权失效”（例如修改了游戏密码或应用权限变更），卡片会提示，点击“重新授权”重新登录一次即可。

## 四、运行测试

```powershell
python -m pytest -q
```

## 目录结构

```
app/
  main.py      # FastAPI 入口与路由（SSO 回调、JSON API、刷新逻辑）
  esi.py       # EVE SSO / ESI 客户端与技能队列数据加工
  db.py        # SQLite 存取
  config.py    # 配置（读取 .env）
  static/      # 前端页面
data/          # SQLite 数据库（自动创建，已被 .gitignore 忽略）
tests/         # 自动化测试（Mock CCP 接口，无需真实账号）
```

## 安全说明

应用只面向本机个人使用：不提供公网访问、无 Web 端账号系统，令牌明文保存在本机数据库中。请不要把 `data/`、`.env` 或 `EVE_CLIENT_SECRET` 分享给他人；若日后要部署到公网，需另行加固（HTTPS、加密存储、访问控制等）。

## 物品名本地索引（模糊搜索）

市场页的"物品价格查询"支持中/英文**部分名称**模糊搜索，基于本地物品名索引
（`data/item_index.db`，约 27k 个已发布物品的中英文名，数据来自 CCP 静态数据）。

索引已在本机构建好。若索引被删除或换了机器，重新构建一次即可（一次性下载约 170MB）：

```powershell
python -m app.name_index --build
```

其他可用命令：`python -m app.name_index --count`、`python -m app.name_index --search 灾难`、`python -m app.name_index --attach`（补齐物品分类信息，供市场页“按分类浏览”使用；本机已执行）。
索引文件位于 `data/`（已被 .gitignore 忽略，不会提交）。

