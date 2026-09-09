"""EVE 多角色技能点查看器 —— FastAPI 应用入口。"""
import json
import secrets
import threading
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, name_index
from .esi import EsiClient, EsiConfigError, EsiError, EsiReauthError, enrich_queue

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"

config.DATA_DIR.mkdir(parents=True, exist_ok=True)

_refresh_lock = threading.Lock()
_market_lock = threading.Lock()

# 市场行情：Jita(10000002) 的两个道具
MARKET_REGION_ID = 10000002
MARKET_ITEMS = [
    {"type_id": 40519, "name": "技能提取器"},
    {"type_id": 40520, "name": "大型技能注入器"},
]
MARKET_CACHE_TTL = 60  # 秒
ITEM_PRICE_CACHE_TTL = 120  # 物品多中心价格缓存(秒)
# 主要贸易中心
ITEM_REGIONS = [
    (10000002, "吉他 Jita"),
    (10000043, "艾玛 Amarr"),
    (10000032, "多迪谢 Dodixie"),
    (10000030, "赫克 Hek"),
    (10000029, "伦斯 Rens"),
]


def _now_ts() -> int:
    return int(time.time())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    _app.state.esi = EsiClient()
    yield
    esi = getattr(_app.state, "esi", None)
    if esi is not None and hasattr(esi, "close"):
        esi.close()


app = FastAPI(title="EVE 多角色技能点查看器", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- 核心逻辑 ----------

def resolve_skill_names(esi: EsiClient, skill_ids) -> dict:
    """把技能 id 解析成名称：优先官方中文（本地缓存），缺失回退英文。"""
    ids = sorted({int(i) for i in skill_ids if i is not None})
    if not ids:
        return {}
    names = db.get_skill_names(ids)
    missing = [i for i in ids if i not in names]
    if missing:
        fetched = esi.fetch_skill_names_zh(missing)
        if fetched:
            db.save_skill_names(fetched)
            names.update(fetched)
        missing = [i for i in missing if i not in names]
    if missing:
        names.update(esi.resolve_names(missing))  # 英文兜底
    return names

# 技能提取相关：总技能点高于该门槛的部分可按 50 万 SP/个 提取
EXTRACT_THRESHOLD_SP = 5_500_000
EXTRACTOR_SP = 500_000


def extractor_stats(total_sp):
    """返回 (可提取SP, 可提取器数量)。未分配(自由)技能点不能提取，不计入。"""
    if total_sp is None:
        return 0, 0
    extractable = max(0, total_sp - EXTRACT_THRESHOLD_SP)
    return extractable, extractable // EXTRACTOR_SP

def refresh_character(esi: EsiClient, character_id: int, force: bool = False):
    """刷新单个角色的技能点与技能队列；失败抛出 EsiError（reauth 会被写入数据库）。"""
    row = db.get_character(character_id)
    if row is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    if row["needs_reauth"]:
        return row

    now = _now_ts()
    access_token = row["access_token"]
    refresh_token = row["refresh_token"]
    expires_at = row["expires_at"]

    if now >= expires_at - config.TOKEN_RENEW_BUFFER_SECONDS:
        try:
            token = esi.refresh_access_token(refresh_token)
        except EsiReauthError as exc:
            db.set_reauth(character_id, exc.message)
            raise
        access_token = token["access_token"]
        expires_at = now + int(token.get("expires_in", 1200))
        new_refresh = token.get("refresh_token") or refresh_token
        db.update_tokens(character_id, access_token, new_refresh, expires_at)

    try:
        skills = esi.get_character_skills(character_id, access_token)
        queue = esi.get_skill_queue(character_id, access_token)
        wallet_isk = esi.get_character_wallet(character_id, access_token)
    except EsiReauthError as exc:
        db.set_reauth(character_id, exc.message)
        raise

    if not row.get("owner_hash"):
        try:
            info = esi.verify(access_token)
            h = info.get("CharacterOwnerHash")
            if h:
                db.set_owner_hash(character_id, h)
                row["owner_hash"] = h
        except EsiError:
            pass  # 补充账号标识失败不影响主流程,下次再补

    names = resolve_skill_names(esi, [e.get("skill_id") for e in queue])
    queue_view = enrich_queue(queue, skills, names)

    db.update_character_data(
        character_id,
        total_sp=skills.get("total_sp"),
        unallocated_sp=skills.get("unallocated_sp"),
        wallet_isk=wallet_isk,
        skills_json=json.dumps(skills, ensure_ascii=False),
        queue_json=json.dumps(queue_view, ensure_ascii=False),
    )
    return db.get_character(character_id)


def is_stale(row: dict) -> bool:
    if row.get("total_sp") is None:
        return True
    try:
        last = datetime.strptime(row["last_updated"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return (_now_ts() - last.timestamp()) > config.STALE_AFTER_SECONDS
    except (TypeError, ValueError):
        return True


def infer_clone_status(skills):
    """根据技能 trained/active 差异推断当前克隆状态。

    存在 trained > active 的技能(被 Alpha 限制压制) -> alpha(确定)
    否则 -> omega(推定)；数据缺失返回 None。
    """
    if not skills:
        return None
    for s in skills.get("skills", []):
        trained = s.get("trained_skill_level")
        active = s.get("active_skill_level")
        if trained is not None and active is not None and trained > active:
            return "alpha"
    return "omega"

def training_speed_per_hour(queue) -> int | None:
    """根据队列第一项(当前训练)计算训练速度(SP/小时)。

    CCP 不提供速度字段,但技能队列自带当前技能等级的 level_start_sp/
    level_end_sp/training_start_date/finish_date。同一技能等级内技能点按
    恒定速率增长,因此 (终点-起点)/(耗时) 即真实训练速度(已含 Alpha
    减速与脑插加成,无需额外权限)。
    """
    if not queue:
        return None
    q = queue[0]
    start_sp = q.get("start_sp")
    end_sp = q.get("end_sp")
    start_date = q.get("training_start_date")
    finish_date = q.get("finish_date")
    if start_sp is None or end_sp is None or not start_date or not finish_date:
        return None
    if end_sp <= start_sp:
        return None
    try:
        start_dt = datetime.fromisoformat(str(start_date).replace("Z", "+00:00"))
        finish_dt = datetime.fromisoformat(str(finish_date).replace("Z", "+00:00"))
    except ValueError:
        return None
    hours = (finish_dt - start_dt).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return int(round((end_sp - start_sp) / hours))


def character_payload(row: dict) -> dict:
    queue = json.loads(row["queue_json"]) if row.get("queue_json") else []
    return {
        "character_id": row["character_id"],
        "character_name": row["character_name"],
        "portrait_url": f"{config.IMAGE_BASE}/characters/{row['character_id']}/portrait?size=128",
        "total_sp": row["total_sp"],
        "unallocated_sp": row["unallocated_sp"],
        "extractable_sp": extractor_stats(row["total_sp"])[0],
        "extractor_count": extractor_stats(row["total_sp"])[1],
        "wallet_isk": row["wallet_isk"],
        "owner_hash": row["owner_hash"],
        "account_name": row["account_name"],
        "clone_status": infer_clone_status(
            json.loads(row["skills_json"]) if row.get("skills_json") else None
        ),
        "queue": queue,
        "training_speed": training_speed_per_hour(queue),
        "last_updated": row["last_updated"],
        "needs_reauth": row["needs_reauth"],
        "auth_error": row["auth_error"],
    }


def _msg_redirect(message: str) -> RedirectResponse:
    return RedirectResponse("/?msg=" + urllib.parse.quote_plus(message), status_code=302)


# ---------- 页面 ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_HTML)


# ---------- SSO ----------

@app.get("/login")
def login():
    if not config.CLIENT_ID or not config.CLIENT_SECRET:
        return _msg_redirect("尚未配置 EVE_CLIENT_ID / EVE_CLIENT_SECRET，请先复制 .env.example 为 .env 并填写。")
    state = secrets.token_urlsafe(32)
    db.add_state(state)
    return RedirectResponse(EsiClient.authorize_url(state), status_code=302)


@app.get("/callback")
def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        return _msg_redirect(f"授权未完成（{error}），未绑定新角色。")
    if not state or not db.pop_state(state):
        return _msg_redirect("登录状态校验失败，请重新点击「添加角色」。")
    if not code:
        return _msg_redirect("未收到授权码，请重试。")

    esi: EsiClient = app.state.esi
    try:
        token = esi.exchange_code(code)
        verify = esi.verify(token["access_token"])
        character_id = int(verify["CharacterID"])
        character_name = verify["CharacterName"]
        owner_hash = verify.get("CharacterOwnerHash")
    except EsiConfigError as exc:
        return _msg_redirect(exc.message)
    except EsiError as exc:
        return _msg_redirect("登录失败：" + exc.message)

    expires_at = _now_ts() + int(token.get("expires_in", 1200))
    db.upsert_character(
        character_id,
        character_name,
        token["access_token"],
        token.get("refresh_token", ""),
        expires_at,
        owner_hash=owner_hash,
    )
    try:
        refresh_character(esi, character_id, force=True)
    except (EsiError, HTTPException):
        pass  # 首次拉取失败可稍后手动刷新；需要重授权的状态已写入数据库

    return RedirectResponse("/", status_code=302)


# ---------- JSON API ----------

@app.get("/api/characters")
def api_characters():
    esi: EsiClient = app.state.esi
    with _refresh_lock:
        rows = db.list_characters()
        for row in rows:
            if row["needs_reauth"]:
                continue
            if is_stale(row):
                try:
                    refresh_character(esi, row["character_id"], force=False)
                except (EsiError, HTTPException):
                    pass  # 保留缓存数据，前端仍可展示
        rows = db.list_characters()
    return [character_payload(r) for r in rows]


@app.post("/api/characters/{character_id}/refresh")
def api_refresh(character_id: int):
    esi: EsiClient = app.state.esi
    row = db.get_character(character_id)
    if row is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    with _refresh_lock:
        try:
            refresh_character(esi, character_id, force=True)
        except EsiReauthError:
            pass  # needs_reauth 已写入数据库，下方返回最新状态
        except EsiConfigError as exc:
            raise HTTPException(status_code=500, detail=exc.message)
        except EsiError as exc:
            raise HTTPException(status_code=502, detail=exc.message)
    return character_payload(db.get_character(character_id))


def _unit_extract_profit(items: list):
    """1 个提取器的利润 = 大型技能注入器中间价 − 技能提取器中间价。"""
    by_id = {it["type_id"]: it for it in items}
    inj = by_id.get(40520)
    ext = by_id.get(40519)
    if not inj or not ext or inj.get("mid") is None or ext.get("mid") is None:
        return None
    return round(inj["mid"] - ext["mid"], 2)

def _compute_market_items(esi: EsiClient) -> list:
    """按 收购价(最高买单) / 出售价(最低卖单) / 中间价 计算两个道具行情。"""
    items = []
    for it in MARKET_ITEMS:
        buy_orders = esi.get_market_orders(MARKET_REGION_ID, "buy", it["type_id"])
        sell_orders = esi.get_market_orders(MARKET_REGION_ID, "sell", it["type_id"])
        buy = max((o.get("price") for o in buy_orders if o.get("price") is not None), default=None)
        sell = min((o.get("price") for o in sell_orders if o.get("price") is not None), default=None)
        mid = round((buy + sell) / 2, 2) if buy is not None and sell is not None else None
        items.append(
            {
                "type_id": it["type_id"],
                "name": it["name"],
                "buy": buy,
                "sell": sell,
                "mid": mid,
            }
        )
    return items


@app.get("/api/market/prices")
def api_market_prices(refresh: int = 0):
    """返回顶部市场行情；默认使用 60 秒内缓存，refresh=1 强制刷新。"""
    esi: EsiClient = app.state.esi
    now = _now_ts()
    with _market_lock:
        cache = getattr(app.state, "market_cache", None)
        if refresh or cache is None or now - cache["ts"] > MARKET_CACHE_TTL:
            try:
                items = _compute_market_items(esi)
            except EsiError:
                if cache is not None:
                    return {
                        "updated": cache["updated"],
                        "items": cache["items"],
                        "unit_profit": cache["unit_profit"],
                        "stale": True,
                    }
                raise HTTPException(status_code=502, detail="读取市场行情失败，请稍后重试。")
            cache = {
                "ts": now,
                "items": items,
                "unit_profit": _unit_extract_profit(items),
                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            app.state.market_cache = cache
    return {
        "updated": cache["updated"],
        "items": cache["items"],
        "unit_profit": cache["unit_profit"],
        "stale": False,
    }

@app.get("/api/market/search")
def api_market_search(q: str = "", limit: int = 20):
    """物品名搜索：优先本地中/英文模糊索引；无索引或未命中时回退官方精确名称解析。"""
    q = (q or "").strip()
    if not q:
        return []
    try:
        local = name_index.search(q, limit)
    except Exception:
        local = []
    if local:
        return local

    esi: EsiClient = app.state.esi
    found = {}
    for lang in ("zh", "en-us"):
        try:
            hits = esi.resolve_universe_ids([q], language=lang)
        except EsiError:
            continue
        for h in hits:
            found.setdefault(h["id"], h.get("name") or q)
    if not found:
        return []
    ids = list(found.keys())[:limit]
    names = resolve_skill_names(esi, ids)  # 优先官方中文名，缺失回退英文
    return [{"type_id": i, "name": names.get(i) or found[i]} for i in ids]


@app.get("/api/market/categories")
def api_market_categories():
    """按道具分类浏览：返回各分类(含中文名)与物品数量。"""
    counts = name_index.category_counts()
    if not counts:
        return []
    ids = [c for c, _ in counts]
    names = name_index.category_names(ids)
    missing = [i for i in ids if i not in names]
    if missing:
        try:
            fetched = app.state.esi.fetch_universe_categories_zh(missing)
            name_index.save_category_names(fetched)
            names.update(fetched)
        except EsiError:
            pass
    out = [
        {"category_id": i, "name": names.get(i, f"类目 {i}"), "count": n}
        for i, n in counts
    ]
    out.sort(key=lambda x: -x["count"])
    return out


@app.get("/api/market/browse")
def api_market_browse(category_id: int, q: str = "", offset: int = 0, limit: int = 100):
    """返回某分类下的物品列表（可选关键词过滤）。"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return name_index.items_by_category(category_id, q, limit, offset)

@app.get("/api/market/item")
def api_market_item(type_id: int, refresh: int = 0):
    """查询指定物品在主要贸易中心的收/售/中价（带 120 秒缓存）。"""
    esi: EsiClient = app.state.esi
    cache_store = getattr(app.state, "item_market_cache", {})
    now = _now_ts()
    cached = cache_store.get(type_id)
    with _market_lock:
        if refresh or not cached or now - cached["ts"] > ITEM_PRICE_CACHE_TTL:
            names = resolve_skill_names(esi, [type_id])
            name = names.get(type_id, str(type_id))
            hubs = []
            for rid, rname in ITEM_REGIONS:
                try:
                    buy = esi.get_market_orders(rid, "buy", type_id)
                    sell = esi.get_market_orders(rid, "sell", type_id)
                    b = max((o.get("price") for o in buy if o.get("price") is not None), default=None)
                    s = min((o.get("price") for o in sell if o.get("price") is not None), default=None)
                    mid = round((b + s) / 2, 2) if b is not None and s is not None else None
                    hubs.append({"region_id": rid, "name": rname, "buy": b, "sell": s, "mid": mid, "error": None})
                except EsiError as exc:
                    hubs.append({"region_id": rid, "name": rname, "buy": None, "sell": None, "mid": None, "error": exc.message})
            data = {
                "type_id": type_id,
                "name": name,
                "hubs": hubs,
                "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            cache_store[type_id] = {"ts": now, "data": data}
            app.state.item_market_cache = cache_store
            return data
    return cached["data"]

@app.post("/api/characters/refresh-all")
def api_refresh_all():
    """一键刷新所有已绑定角色（跳过需重新授权的角色），返回最新列表。"""
    esi: EsiClient = app.state.esi
    with _refresh_lock:
        rows = db.list_characters()
        for row in rows:
            if row["needs_reauth"]:
                continue
            try:
                refresh_character(esi, row["character_id"], force=True)
            except (EsiError, HTTPException):
                pass  # 单个角色失败不影响其它角色
        rows = db.list_characters()
    return [character_payload(r) for r in rows]

@app.delete("/api/characters")
def api_delete_all_characters():
    """一键删除所有角色及其本地令牌与缓存。"""
    deleted = db.delete_all_characters()
    return {"ok": True, "deleted": deleted}

class AccountNameIn(BaseModel):
    account_name: str = ""


@app.post("/api/characters/{character_id}/account")
def api_set_account_name(character_id: int, payload: AccountNameIn):
    """设置角色所属账号/分组名(同名角色会排在一起)。传空字符串可清除。"""
    row = db.get_character(character_id)
    if row is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    name = (payload.account_name or "").strip()
    db.set_account_name(character_id, name or None)
    return character_payload(db.get_character(character_id))

@app.delete("/api/characters/{character_id}")
def api_delete(character_id: int):
    db.delete_character(character_id)
    return {"ok": True}



















