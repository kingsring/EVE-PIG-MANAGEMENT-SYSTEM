"""应用配置：从环境变量 / .env 读取。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 尝试加载项目根目录下的 .env（未安装 dotenv 或文件不存在时静默忽略）
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


CLIENT_ID = _env("EVE_CLIENT_ID", "")
CLIENT_SECRET = _env("EVE_CLIENT_SECRET", "")
CALLBACK_URL = _env("EVE_CALLBACK_URL", "http://localhost:8000/callback")
HOST = _env("EVE_HOST", "127.0.0.1")
PORT = int(_env("EVE_PORT", "8000"))
DATA_DIR = Path(_env("EVE_DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "eve_esi.db"
SCOPES = "esi-skills.read_skills.v1 esi-skills.read_skillqueue.v1 esi-wallet.read_character_wallet.v1"
USER_AGENT = _env("EVE_USER_AGENT", "eve-skillpoints-viewer/1.0 (local personal tool)")
ESI_BASE = "https://esi.evetech.net"
LOGIN_BASE = "https://login.eveonline.com"
IMAGE_BASE = "https://images.evetech.net"
DATASOURCE = "tranquility"
# 数据超过该秒数视为过期，打开首页 / 列表时会自动刷新
STALE_AFTER_SECONDS = int(_env("EVE_STALE_AFTER_SECONDS", "300"))
# 访问令牌到期前多少秒视为需要提前用 refresh_token 续期
TOKEN_RENEW_BUFFER_SECONDS = 30

