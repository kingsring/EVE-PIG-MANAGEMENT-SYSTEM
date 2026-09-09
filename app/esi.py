"""EVE SSO（OAuth2 授权码）与 ESI 接口客户端。"""
import threading
from urllib.parse import urlencode

import httpx

from . import config


class EsiError(Exception):
    """ESI / SSO 请求失败的基类。"""

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.message = message
        # transient=True 表示临时性错误（网络 / 5xx），可稍后重试
        self.transient = transient


class EsiReauthError(EsiError):
    """令牌失效或权限不足，需要用户重新登录授权该角色。"""


class EsiConfigError(EsiError):
    """应用配置错误（缺少 / 错误的 Client ID、Secret 等）。"""


_name_cache: dict = {}
_name_cache_lock = threading.Lock()


class EsiClient:
    def __init__(self, transport=None):
        self._http = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
            transport=transport,
        )

    def close(self):
        self._http.close()

    # ---------- SSO ----------

    @staticmethod
    def authorize_url(state: str) -> str:
        params = {
            "response_type": "code",
            "redirect_uri": config.CALLBACK_URL,
            "client_id": config.CLIENT_ID,
            "scope": config.SCOPES,
            "state": state,
        }
        return f"{config.LOGIN_BASE}/v2/oauth/authorize?{urlencode(params)}"

    def _check_credentials(self):
        if not config.CLIENT_ID or not config.CLIENT_SECRET:
            raise EsiConfigError("尚未配置 EVE_CLIENT_ID / EVE_CLIENT_SECRET，请按 README 填写 .env。")

    def exchange_code(self, code: str) -> dict:
        """用授权码换取 access_token + refresh_token。"""
        self._check_credentials()
        resp = self._http.post(
            f"{config.LOGIN_BASE}/v2/oauth/token",
            auth=(config.CLIENT_ID, config.CLIENT_SECRET),
            data={"grant_type": "authorization_code", "code": code},
        )
        if resp.status_code != 200:
            if resp.status_code == 401:
                raise EsiConfigError("EVE_CLIENT_ID / EVE_CLIENT_SECRET 无效，请检查 .env 配置。")
            raise EsiError(f"换取访问令牌失败（HTTP {resp.status_code}）。")
        return resp.json()

    def refresh_access_token(self, refresh_token: str) -> dict:
        """用 refresh_token 续期 access_token。"""
        self._check_credentials()
        resp = self._http.post(
            f"{config.LOGIN_BASE}/v2/oauth/token",
            auth=(config.CLIENT_ID, config.CLIENT_SECRET),
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        if resp.status_code != 200:
            if resp.status_code == 400:
                raise EsiReauthError("登录已失效，请重新授权该角色。")
            if resp.status_code == 401:
                raise EsiConfigError("EVE_CLIENT_ID / EVE_CLIENT_SECRET 无效，请检查 .env 配置。")
            raise EsiError(f"续期令牌失败（HTTP {resp.status_code}）。", transient=True)
        return resp.json()

    def verify(self, access_token: str) -> dict:
        """校验 access_token 并取得角色 ID / 名称。"""
        resp = self._http.get(
            f"{config.LOGIN_BASE}/oauth/verify",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise EsiReauthError("验证角色身份失败，请重新授权。")
        return resp.json()

    # ---------- ESI ----------

    def _esi_request(self, method: str, path: str, access_token=None, json_body=None):
        kwargs = {"params": {"datasource": config.DATASOURCE}}
        if access_token:
            kwargs["headers"] = {"Authorization": f"Bearer {access_token}"}
        if json_body is not None:
            kwargs["json"] = json_body
        return self._http.request(method, f"{config.ESI_BASE}{path}", **kwargs)

    def get_character_skills(self, character_id: int, access_token: str) -> dict:
        """读取角色技能点（total_sp 与技能明细）。"""
        resp = self._esi_request(
            "GET", f"/latest/characters/{character_id}/skills/", access_token=access_token
        )
        if resp.status_code == 200:
            return resp.json()
        self._raise_esi_error(resp, "读取技能点")

    def get_character_wallet(self, character_id: int, access_token: str) -> float:
        """读取角色个人钱包 ISK 余额。"""
        resp = self._esi_request(
            "GET", f"/latest/characters/{character_id}/wallet/", access_token=access_token
        )
        if resp.status_code == 200:
            return float(resp.json())
        self._raise_esi_error(resp, "读取钱包")
    def get_skill_queue(self, character_id: int, access_token: str) -> list:
        """读取角色技能队列。"""
        resp = self._esi_request(
            "GET", f"/latest/characters/{character_id}/skillqueue/", access_token=access_token
        )
        if resp.status_code == 200:
            return resp.json()
        self._raise_esi_error(resp, "读取技能队列")

    @staticmethod
    def _raise_esi_error(resp, action: str):
        if resp.status_code in (401, 403):
            raise EsiReauthError(f"{action}需要授权（HTTP {resp.status_code}），请重新登录该角色。")
        if resp.status_code == 404:
            raise EsiReauthError(f"{action}失败：角色不存在或已被删除，请重新授权。")
        raise EsiError(f"{action}失败（HTTP {resp.status_code}）。", transient=True)

    def get_market_orders(self, region_id: int, order_type: str, type_id: int, max_pages: int = 15) -> list:
        """读取公开市场挂单(buy/sell),自动翻页聚合。order_type: buy | sell | all。"""
        orders = []
        page = 1
        pages = 1
        while page <= pages and page <= max_pages:
            resp = self._http.get(
                f"{config.ESI_BASE}/latest/markets/{region_id}/orders/",
                params={
                    "datasource": config.DATASOURCE,
                    "order_type": order_type,
                    "type_id": type_id,
                    "page": page,
                },
            )
            if resp.status_code != 200:
                raise EsiError(f"读取市场挂单失败（HTTP {resp.status_code}）。", transient=True)
            data = resp.json()
            orders.extend(data)
            if not data:
                break
            try:
                pages = max(pages, int(resp.headers.get("x-pages", "1")))
            except (TypeError, ValueError):
                pass
            page += 1
        return orders
    def fetch_universe_categories_zh(self, ids) -> dict:
        """逐个请求 /universe/categories/{id}/?language=zh 取官方中文类目名（尽力而为）。"""
        result = {}
        for i in sorted({int(x) for x in ids}):
            try:
                resp = self._http.get(
                    f"{config.ESI_BASE}/latest/universe/categories/{i}/",
                    params={"datasource": config.DATASOURCE, "language": "zh"},
                )
                if resp.status_code == 200:
                    name = resp.json().get("name")
                    if name:
                        result[i] = name
            except httpx.HTTPError:
                continue
        return result
    def resolve_universe_ids(self, names, language: str = "zh") -> list:
        """按精确名称解析 type id（/universe/ids/）。返回 [{"id":..,"name":..}, ...]。"""
        resp = self._http.post(
            f"{config.ESI_BASE}/latest/universe/ids/",
            params={"datasource": config.DATASOURCE, "language": language},
            json=list(names),
        )
        if resp.status_code == 200:
            return resp.json().get("inventory_types", [])
        raise EsiError(f"名称解析失败（HTTP {resp.status_code}）。", transient=True)
    def fetch_skill_names_zh(self, ids) -> dict:
        """逐个请求 /universe/types/{id}/?language=zh 取官方中文技能名（尽力而为）。

        单个请求较慢，只应针对缺失缓存的小批量 id 使用；结果应写入本地缓存。
        """
        result = {}
        for i in sorted({int(x) for x in ids}):
            try:
                resp = self._http.get(
                    f"{config.ESI_BASE}/latest/universe/types/{i}/",
                    params={"datasource": config.DATASOURCE, "language": "zh"},
                )
                if resp.status_code == 200:
                    name = resp.json().get("name")
                    if name:
                        result[i] = name
            except httpx.HTTPError:
                continue  # 单次失败不阻断，缺失项走英文兜底
        return result
    def resolve_names(self, ids) -> dict:
        """通过 /universe/names/ 批量解析 id -> 名称（进程内缓存，尽力而为）。"""
        unique = sorted({int(i) for i in ids if i is not None})
        if not unique:
            return {}
        result = {}
        with _name_cache_lock:
            missing = [i for i in unique if i not in _name_cache]
            for i in unique:
                if i in _name_cache:
                    result[i] = _name_cache[i]
        if missing:
            try:
                resp = self._esi_request("POST", "/latest/universe/names/", json_body=missing)
                if resp.status_code == 200:
                    for item in resp.json():
                        result[item["id"]] = item["name"]
                        with _name_cache_lock:
                            _name_cache[item["id"]] = item["name"]
            except EsiError:
                pass  # 名称解析失败不阻断刷新，回退为占位名
        return result


def enrich_queue(queue_entries, skills, names: dict) -> list:
    """把原始技能队列与技能快照合并，补充技能名、等级与训练进度。"""
    sp_by_id = {s.get("skill_id"): s for s in (skills or {}).get("skills", [])}
    out = []
    for idx, e in enumerate(queue_entries or []):
        sid = e.get("skill_id")
        sp = sp_by_id.get(sid, {})
        current_sp = sp.get("skillpoints_in_skill")
        start_sp = e.get("level_start_sp")
        end_sp = e.get("level_end_sp")
        progress = None
        if (
            current_sp is not None
            and start_sp is not None
            and end_sp is not None
            and end_sp > start_sp
        ):
            progress = max(0.0, min(1.0, (current_sp - start_sp) / (end_sp - start_sp)))
        out.append(
            {
                "skill_id": sid,
                "name": names.get(sid, f"技能 {sid}"),
                "current_level": sp.get("active_skill_level"),
                "target_level": e.get("finished_level"),
                "position": int(e.get("queue_position", idx)),
                "start_sp": start_sp,
                "end_sp": end_sp,
                "current_sp": current_sp,
                "progress": progress,
                # CCP 现返回 start_date(旧文档写作 training_start_date),两者都兼容
                "training_start_date": e.get("start_date") or e.get("training_start_date"),
                "finish_date": e.get("finish_date"),
            }
        )
    out.sort(key=lambda x: x["position"])
    return out






