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

from . import config, db, fitting, name_index, skill_optimizer
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
GLOBAL_PLEX_REGION = 19000001  # CCP 全球 PLEX 市场(2025-07 上线),PLEX/伊甸币在此交易
PLEX_TYPE_ID = 44992
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
    name_index.init_index()
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


def extractor_stats(total_sp, unallocated_sp=None):
    """按“已训练技能点”计算可提取量（未分配技能点不计入、不提取）。

    公式：(已训练技能点 − 550万) ÷ 50万 向下取整。
    total_sp 为 None 时按无数据处理。
    """
    if total_sp is None:
        return 0, 0
    extractable = max(0, total_sp - EXTRACT_THRESHOLD_SP)
    return extractable, extractable // EXTRACTOR_SP

def drop_sale_count(old_combined, new_combined) -> int:
    """技能点下降折算可卖注入器个数(每 50 万一个,向下取整)。"""
    if old_combined is None or new_combined is None:
        return 0
    drop = (old_combined or 0) - (new_combined or 0)
    if drop < 500_000:
        return 0
    return int(drop // 500_000)


def _injector_mid(esi: EsiClient):
    """取大型技能注入器当前中间价:优先顶部行情缓存,否则现查吉他。"""
    cache = getattr(app.state, "market_cache", None)
    if cache and cache.get("items"):
        for it in cache["items"]:
            if it.get("type_id") == 40520 and it.get("mid"):
                return it["mid"]
    try:
        buy = esi.get_market_orders(10000002, "buy", 40520)
        sell = esi.get_market_orders(10000002, "sell", 40520)
        b = max((o.get("price") for o in buy if o.get("price") is not None), default=None)
        s = min((o.get("price") for o in sell if o.get("price") is not None), default=None)
        if b and s:
            return round((b + s) / 2, 2)
    except EsiError:
        pass
    return None


def _extractor_mid(esi: EsiClient):
    """取技能提取器当前中间价:优先顶部行情缓存,否则现查吉他。"""
    cache = getattr(app.state, "market_cache", None)
    if cache and cache.get("items"):
        for it in cache["items"]:
            if it.get("type_id") == 40519 and it.get("mid"):
                return it["mid"]
    try:
        buy = esi.get_market_orders(10000002, "buy", 40519)
        sell = esi.get_market_orders(10000002, "sell", 40519)
        b = max((o.get("price") for o in buy if o.get("price") is not None), default=None)
        s = min((o.get("price") for o in sell if o.get("price") is not None), default=None)
        if b and s:
            return round((b + s) / 2, 2)
    except EsiError:
        pass
    return None

def _auto_finance_drop(esi, account_name, character_id, character_name, old_total, old_unalloc, new_total, new_unalloc):
    """角色总技能点下降时自动记两条账:提取器支出 + 注入器收入(均按中间价)。

    归属到该角色所属账号组；两条都标记为自动,可在财务页手动修改金额。
    """
    try:
        old = (old_total or 0) + (old_unalloc or 0)
        new = (new_total or 0) + (new_unalloc or 0)
        qty = drop_sale_count(old, new)
        if not qty:
            return
        inj_mid = _injector_mid(esi)
        ext_mid = _extractor_mid(esi)
        if not inj_mid or not ext_mid:
            return  # 缺少行情价时不自动记账,避免金额失真
        today = datetime.now().strftime("%Y-%m-%d")
        acct = (account_name or "").strip() or None
        inj_amount = round(qty * float(inj_mid), 2)
        ext_amount = round(qty * float(ext_mid), 2)
        db.add_finance_entry(
            kind="income", date=today, account_name=acct, amount=inj_amount,
            category="sale", qty=qty, note=f"自动:卖出注入器 x{qty}",
            unit_price=float(inj_mid), auto=1,
            character_id=character_id, character_name=character_name,
        )
        db.add_finance_entry(
            kind="expense", date=today, account_name=acct, amount=ext_amount,
            category="extractor", qty=qty, note=f"自动:提取器成本 x{qty}",
            unit_price=float(ext_mid), auto=1,
            character_id=character_id, character_name=character_name,
        )
    except Exception:
        pass

def _record_today_history():
    """汇总所有角色并把“当天可提取总数”写入历史(失败不影响主流程)。"""
    try:
        rows = db.list_characters()
        total_ext = sum(extractor_stats(r["total_sp"], r["unallocated_sp"])[1] for r in rows)
        trained = sum(r["total_sp"] or 0 for r in rows)
        combined = sum((r["total_sp"] or 0) + (r["unallocated_sp"] or 0) for r in rows)
        db.upsert_history(
            datetime.now().strftime("%Y-%m-%d"),
            total_ext,
            trained,
            combined,
            len(rows),
        )
    except Exception:
        pass

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
        try:
            attributes_getter = getattr(esi, "get_character_attributes", None)
            attributes = attributes_getter(character_id, access_token) if attributes_getter else None
        except EsiError:
            attributes = None  # 属性接口失败时保留旧缓存，不影响技能刷新
        implants = None
        implants_error = None
        try:
            implants_getter = getattr(esi, "get_character_implants", None)
            if implants_getter:
                implant_ids = implants_getter(character_id, access_token)
                implants = {
                    "implant_ids": [int(i) for i in implant_ids],
                    "bonuses": fitting.implant_bonus_map(implant_ids),
                }
            else:
                implants_error = "当前角色尚未读取脑插，请重新登录以授予 esi-clones.read_implants.v1 权限。"
        except EsiReauthError as exc:
            implants_error = exc.message
        except EsiError as exc:
            implants_error = exc.message
        if implants is None and implants_error:
            implants = {"error": implants_error}
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
        attributes_json=json.dumps(attributes, ensure_ascii=False) if attributes is not None else None,
        implants_json=json.dumps(implants, ensure_ascii=False) if implants is not None else None,
        queue_json=json.dumps(queue_view, ensure_ascii=False),
    )
    _auto_finance_drop(
        esi, row.get("account_name"), character_id, row.get("character_name"),
        row["total_sp"], row["unallocated_sp"],
        skills.get("total_sp"), skills.get("unallocated_sp"),
    )
    _record_today_history()
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
        "extractable_sp": extractor_stats(row["total_sp"], row["unallocated_sp"])[0],
        "extractor_count": extractor_stats(row["total_sp"], row["unallocated_sp"])[1],
        "wallet_isk": row["wallet_isk"],
        "owner_hash": row["owner_hash"],
        "account_name": row["account_name"],
        "clone_status": infer_clone_status(
            json.loads(row["skills_json"]) if row.get("skills_json") else None
        ),
        "attributes": json.loads(row["attributes_json"]) if row.get("attributes_json") else None,
        "implants": json.loads(row["implants_json"]) if row.get("implants_json") else None,
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
    """仅返回本地缓存数据(立即响应);刷新由前端逐个轮流调用刷新接口完成,避免首屏阻塞。"""
    return [character_payload(r) for r in db.list_characters()]


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


@app.get("/api/history")
def api_history(limit: int = 30):
    """按天返回历史“可提取总数”等汇总(自开始记录起)。"""
    return db.get_history(limit)

class FittingItemIn(BaseModel):
    type_id: int = 0
    qty: int = 1
    slot: str = ""
    charge_type_id: int | None = None
    active: bool = False


class ExtraSkillIn(BaseModel):
    skill_id: int
    need_level: int = 5


class AnalyzeIn(BaseModel):
    ship_type_id: int
    items: list[FittingItemIn] = []
    character_id: int | None = None
    skill_mode: str = "online"
    extra_skills: list[ExtraSkillIn] = []


class FittingIn(BaseModel):
    name: str
    ship_type_id: int
    data: dict = {}


class UpgradeSkillsIn(BaseModel):
    ship_type_id: int
    items: list[FittingItemIn] = []
    character_id: int | None = None

class OptimizeSkillsIn(BaseModel):
    ship_type_id: int
    items: list[FittingItemIn] = []
    extra_skills: list[ExtraSkillIn] = []
    character_id: int | None = None
    skill_mode: str = "online"
    max_time_regret_hours: float = 24

class RebasePlanIn(BaseModel):
    character_id: int
    plan: dict = {}


class ExportIn(BaseModel):
    ship_type_id: int
    items: list[FittingItemIn] = []
    name: str = ""


class ImportIn(BaseModel):
    text: str = ""

@app.get("/api/fitting/item")
def api_fitting_item(type_id: int):
    """单件物品的配装信息(槽位推断/CPU/PG/体积)。"""
    info = fitting.item_info(type_id)
    if not info.get("known"):
        raise HTTPException(status_code=404, detail="本地索引中没有该物品")
    return info


@app.get("/api/fitting/search")
def api_fitting_search(slot: str = "", q: str = "", limit: int = 50, group_id: int | None = None):
    """按槽位搜索可装配物品(hi/med/low/rig/drone)。"""
    if slot == "drone":
        return name_index.search_category(18, q, limit)
    if slot == "ship":
        return name_index.search_category(6, q, limit)
    if slot == "skill":
        return name_index.search_category(16, q, limit, group_id=group_id)
    if slot in ("hi", "med", "low", "rig"):
        return name_index.search_by_slot(slot, q, limit)
    raise HTTPException(status_code=422, detail="slot 必须是 hi/med/low/rig/drone")


@app.post("/api/fitting/rebase-plan")
def api_fitting_rebase_plan(payload: RebasePlanIn):
    """按角色当前技能队列重算已保存方案的训练时间节点。"""
    character = db.get_character(payload.character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    try:
        return skill_optimizer.rebase_plan_with_queue(character, payload.plan)
    except skill_optimizer.OptimizerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/fitting/optimize-skills")
def api_fitting_optimize_skills(payload: OptimizeSkillsIn):
    """生成满足技能依赖的近似最短训练计划。"""
    if not payload.character_id:
        raise HTTPException(status_code=422, detail="请先选择角色")
    character = db.get_character(payload.character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    items = [i.model_dump() for i in payload.items]
    mode = payload.skill_mode if payload.skill_mode in ("online", "all4", "all5") else "online"
    regret_hours = payload.max_time_regret_hours if payload.max_time_regret_hours in (24, 72, 168) else 24
    analysis = fitting.analyze(
        payload.ship_type_id, items, character, skill_mode=mode,
        extra_skills=[i.model_dump() for i in payload.extra_skills],
    )
    try:
        result = skill_optimizer.optimize_skill_plan(
            character,
            analysis.get("skills", []),
            max_time_regret_hours=regret_hours,
        )
    except skill_optimizer.OptimizerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result["ship_type_id"] = payload.ship_type_id
    result["character_id"] = payload.character_id
    result["skill_mode"] = mode
    return result


@app.post("/api/fitting/upgrade-skills")
def api_fitting_upgrade_skills(payload: UpgradeSkillsIn):
    """返回可能提升当前配装的技能 ID，不计算具体收益。"""
    character = None
    if payload.character_id:
        character = db.get_character(payload.character_id)
        if character is None:
            raise HTTPException(status_code=404, detail="角色不存在")
    return fitting.find_upgrade_skills(
        payload.ship_type_id, [i.model_dump() for i in payload.items], character,
    )


@app.get("/api/fitting/skill-info")
def api_fitting_skill_info(type_id: int):
    """技能中文说明、等级系数、主/副属性与前置技能。"""
    item = name_index.get_item(type_id)
    if not item or item.get("category_id") != 16:
        raise HTTPException(status_code=422, detail="不是有效技能")
    attrs = name_index.get_type_attrs([type_id]).get(type_id, {})
    detail = db.get_skill_details([type_id]).get(type_id, {})
    if not detail.get("description"):
        fetched = app.state.esi.fetch_skill_details_zh([type_id])
        if fetched:
            db.save_skill_details(fetched)
            detail = fetched.get(type_id, detail)
    attr_names = {165: "智力", 166: "记忆", 167: "感知", 168: "意志力", 169: "魅力"}
    prerequisites = []
    for sid_attr, lvl_attr in fitting.REQ_SKILL_ATTRS:
        sid = attrs.get(sid_attr)
        if not sid:
            continue
        pre = name_index.get_item(int(sid)) or {}
        prerequisites.append({
            "skill_id": int(sid),
            "name": pre.get("name_zh") or pre.get("name_en") or str(sid),
            "level": int(attrs.get(lvl_attr) or 1),
        })
    return {
        "type_id": type_id,
        "name": detail.get("name") or item.get("name_zh") or item.get("name_en") or str(type_id),
        "description": detail.get("description") or "暂无技能说明",
        "rank": attrs.get(275) or 1,
        "primary_attribute": attr_names.get(attrs.get(180), "—"),
        "secondary_attribute": attr_names.get(attrs.get(181), "—"),
        "prerequisites": prerequisites,
    }


@app.get("/api/fitting/skill-groups")
def api_fitting_skill_groups():
    """返回技能大类；缺失中文组名时通过 ESI 拉取并缓存。"""
    groups = name_index.skill_groups()
    cached = name_index.group_names([g["group_id"] for g in groups])
    missing = [g["group_id"] for g in groups if g["group_id"] not in cached]
    if missing:
        fetched = app.state.esi.fetch_universe_groups_zh(missing)
        if fetched:
            name_index.save_group_names(fetched)
            cached.update(fetched)
    for g in groups:
        g["name"] = cached.get(g["group_id"], g["name"])
    return groups
@app.get("/api/fitting/charges")
def api_fitting_charges(type_id: int, q: str = "", limit: int = 50):
    """某模块可装填的弹药列表(按 chargeGroup1..5)。"""
    attrs = name_index.get_type_attrs([type_id]).get(type_id, {})
    groups = [attrs.get(a) for a in (604, 605, 606, 609, 610) if attrs.get(a)]
    return name_index.search_group_charges(groups, q, limit)

@app.post("/api/fitting/analyze")
def api_fitting_analyze(payload: AnalyzeIn):
    """分析配装并(可选)对比角色技能。"""
    character = None
    if payload.character_id:
        character = db.get_character(payload.character_id)
        if character is None:
            raise HTTPException(status_code=404, detail="角色不存在")
    items = [i.model_dump() for i in payload.items]
    mode = payload.skill_mode if payload.skill_mode in ("online", "all4", "all5") else "online"
    return fitting.analyze(payload.ship_type_id, items, character, skill_mode=mode, extra_skills=[i.model_dump() for i in payload.extra_skills])


@app.post("/api/fitting/export")
def api_fitting_export(payload: ExportIn):
    """按 CCP 官方 EFT 格式导出配装(英文名)。"""
    items = [i.model_dump() for i in payload.items]
    text = fitting.export_eft(payload.ship_type_id, items, payload.name)
    return {"text": text}

@app.post("/api/fitting/import")
def api_fitting_import(payload: ImportIn):
    return fitting.parse_eft(payload.text)


@app.get("/api/fittings")
def api_fittings():
    characters = {int(c["character_id"]): c for c in db.list_characters()}
    out = []
    for row in db.list_fittings():
        try:
            data = json.loads(row.get("data_json") or "{}")
        except ValueError:
            data = {}
        plan = data.get("optimizedPlan") if isinstance(data, dict) else None
        character_id = None
        if isinstance(plan, dict):
            try:
                character_id = int(plan.get("character_id") or 0) or None
            except (TypeError, ValueError):
                character_id = None
        queue_match = skill_optimizer.analyze_queue_match(
            characters.get(character_id) if character_id else None,
            plan if isinstance(plan, dict) else None,
        )
        out.append({
            "id": row["id"], "name": row["name"], "ship_type_id": row["ship_type_id"],
            "data": data, "updated_at": row["updated_at"], "queue_match": queue_match,
        })
    return out


@app.post("/api/fittings")
def api_fitting_create(payload: FittingIn):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="配装名称不能为空")
    fid = db.add_fitting(name, payload.ship_type_id, json.dumps(payload.data, ensure_ascii=False))
    return {"id": fid}


@app.put("/api/fittings/{fitting_id}")
def api_fitting_update(fitting_id: int, payload: FittingIn):
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="配装名称不能为空")
    if not db.update_fitting(fitting_id, name, payload.ship_type_id,
                             json.dumps(payload.data, ensure_ascii=False)):
        raise HTTPException(status_code=404, detail="配装不存在")
    return {"ok": True}


@app.delete("/api/fittings/{fitting_id}")
def api_fitting_delete(fitting_id: int):
    if not db.delete_fitting(fitting_id):
        raise HTTPException(status_code=404, detail="配装不存在")
    return {"ok": True}

@app.get("/api/finance/extractions")
def api_finance_extractions(limit: int = 90):
    """提取事件列表(按天/账号/角色/数量),用于走势图标记。

    新格式优先取“提取器成本”支出记录;旧版只有自动收入的历史记录也兼容。
    """
    limit = max(1, min(limit, 365))
    entries = db.get_finance_entries()
    events = {}
    legacy = []
    for e in entries:
        is_auto = bool(e.get("auto")) or (e.get("note") or "").startswith("自动:")
        if not (is_auto and e.get("qty")):
            continue
        key = (e["date"], e["account_name"], e["qty"])
        if e["kind"] == "expense" and e["category"] == "extractor":
            events[key] = {
                "date": e["date"],
                "account": e["account_name"] or "未归属",
                "character_id": e.get("character_id"),
                "character_name": e.get("character_name") or "",
                "qty": e["qty"],
            }
        elif e["kind"] == "income" and e["category"] == "sale":
            legacy.append((key, e))
    for key, e in legacy:
        if key not in events:  # 旧版只有收入记录,没有对应的支出记录
            events[key] = {
                "date": e["date"],
                "account": e["account_name"] or "未归属",
                "character_id": e.get("character_id"),
                "character_name": e.get("character_name") or "",
                "qty": e["qty"],
            }
    out = sorted(events.values(), key=lambda x: x["date"])
    return out[-limit:]

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
            regions = list(ITEM_REGIONS)
            if type_id == PLEX_TYPE_ID:
                regions = [(GLOBAL_PLEX_REGION, "全球市场(PLEX)")] + regions
            hubs = []
            for rid, rname in regions:
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

def _normalize_account(name):
    return (name or "").strip() or None


class FinanceEntryIn(BaseModel):
    kind: str = ""
    date: str = ""
    amount: float = 0.0
    account_name: str = ""
    category: str = ""
    qty: int | None = None
    note: str = ""

def _validate_finance(payload: FinanceEntryIn):
    if payload.kind not in ("income", "expense"):
        raise HTTPException(status_code=422, detail="kind 必须是 income 或 expense")
    try:
        datetime.strptime(payload.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="日期格式应为 YYYY-MM-DD")
    if not payload.amount or payload.amount <= 0:
        raise HTTPException(status_code=422, detail="金额必须为正数")
    if payload.qty is not None and (payload.qty <= 0 or int(payload.qty) != payload.qty):
        raise HTTPException(status_code=422, detail="数量必须为正整数")
    if payload.kind == "expense" and payload.category not in ("omega", "mct", "extractor"):
        raise HTTPException(status_code=422, detail="支出类型必须是 omega(欧米伽)/mct(多角色训练)/extractor(技能提取器)")
    category = "sale" if payload.kind == "income" else payload.category
    return {
        "kind": payload.kind,
        "date": payload.date,
        "account_name": _normalize_account(payload.account_name),
        "amount": round(float(payload.amount), 2),
        "category": category,
        "qty": int(payload.qty) if payload.qty is not None else None,
        "note": (payload.note or "").strip(),
    }


@app.get("/api/finance/entries")
def api_finance_entries(month: str = "", account: str = "", kind: str = ""):
    """收支明细；month=YYYY-MM，account 传 __none__ 表示未归属。"""
    return db.get_finance_entries(
        month=month,
        account=account if account else None,
        kind=kind if kind in ("income", "expense") else "",
    )


@app.post("/api/finance/entries")
def api_finance_create(payload: FinanceEntryIn):
    fields = _validate_finance(payload)
    entry_id = db.add_finance_entry(**fields)
    return db.get_finance_entry(entry_id)


@app.put("/api/finance/entries/{entry_id}")
def api_finance_update(entry_id: int, payload: FinanceEntryIn):
    fields = _validate_finance(payload)
    if not db.update_finance_entry(entry_id, **fields):
        raise HTTPException(status_code=404, detail="记录不存在")
    return db.get_finance_entry(entry_id)


@app.delete("/api/finance/entries/{entry_id}")
def api_finance_delete(entry_id: int):
    if not db.delete_finance_entry(entry_id):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"ok": True, "id": entry_id}


@app.get("/api/finance/summary")
def api_finance_summary(month: str = ""):
    """累计/本月 汇总与分账号收支利润。"""
    def totals(entries):
        income = round(sum(e["amount"] for e in entries if e["kind"] == "income"), 2)
        expense = round(sum(e["amount"] for e in entries if e["kind"] == "expense"), 2)
        return {"income": income, "expense": expense, "profit": round(income - expense, 2)}

    all_entries = db.get_finance_entries()
    result = {"cumulative": totals(all_entries), "month": None, "accounts": []}

    valid_month = True
    try:
        if month:
            datetime.strptime(month, "%Y-%m")
    except ValueError:
        valid_month = False
    if month and valid_month:
        result["month"] = totals(db.get_finance_entries(month=month))

    by_acct = {}
    for e in all_entries:
        key = e["account_name"] or ""
        d = by_acct.setdefault(key, {"income": 0.0, "expense": 0.0})
        if e["kind"] == "income":
            d["income"] += e["amount"]
        else:
            d["expense"] += e["amount"]
    for row in db.list_characters():
        name = (row.get("account_name") or "").strip()
        if name:
            by_acct.setdefault(name, {"income": 0.0, "expense": 0.0})

    accounts = []
    for key, d in by_acct.items():
        label = key if key else "未归属"
        accounts.append({
            "account": label,
            "income": round(d["income"], 2),
            "expense": round(d["expense"], 2),
            "profit": round(d["income"] - d["expense"], 2),
        })
    accounts.sort(key=lambda a: (a["account"] == "未归属", a["account"]))
    result["accounts"] = accounts
    return result

@app.delete("/api/characters")
def api_delete_all_characters():
    """一键删除所有角色及其本地令牌与缓存。"""
    deleted = db.delete_all_characters()
    _record_today_history()
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
    _record_today_history()
    return {"ok": True}









































