"""舰船配装分析与 EFT 配装文本解析。

校验:槽位/CPU/PG/改装校准/炮台发射器/无人机舱容与带宽(基础属性,不含技能加成)。
技能:汇总船体与全部装备/弹药/无人机的技能需求,取最高等级;可对比角色技能。
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone

from . import name_index

SLOT_HI, SLOT_MED, SLOT_LOW, SLOT_RIG, SLOT_DRONE, SLOT_CHARGE = (
    "hi", "med", "low", "rig", "drone", "charge",
)

REQ_SKILL_ATTRS = [(182, 277), (183, 278), (184, 279), (1285, 1286), (1289, 1287), (1290, 1288)]
DEFAULT_TRAINING_SP_HOUR = 2700.0


def _num(attrs: dict, key, default=0.0) -> float:
    try:
        return float(attrs.get(key, default) or 0.0)
    except (TypeError, ValueError):
        return default


def item_info(type_id: int) -> dict:
    """单件物品的配装信息(槽位推断/CPU/PG/体积等)。"""
    item = name_index.get_item(type_id)
    attrs = name_index.get_type_attrs([type_id]).get(type_id, {})
    effects = name_index.get_type_effects([type_id]).get(type_id, set())
    slot = ""
    if _num(attrs, 1374) > 0:
        slot = SLOT_HI
    elif _num(attrs, 1375) > 0:
        slot = SLOT_MED
    elif _num(attrs, 1376) > 0:
        slot = SLOT_LOW
    else:
        if 11 in effects:
            slot = SLOT_LOW      # loPower
        elif 12 in effects:
            slot = SLOT_HI       # hiPower
        elif 13 in effects:
            slot = SLOT_MED      # medPower
        elif 2663 in effects:
            slot = SLOT_RIG      # rigSlot
        elif (item or {}).get("category_id") == 7 and (_num(attrs, 1153) > 0 or _num(attrs, 1547) > 0):
            slot = SLOT_RIG
    return {
        "type_id": type_id,
        "name": (item or {}).get("name_zh") or (item or {}).get("name_en") or str(type_id),
        "name_en": (item or {}).get("name_en"),
        "category_id": (item or {}).get("category_id"),
        "group_id": (item or {}).get("group_id"),
        "volume": (item or {}).get("volume"),
        "slot_guess": slot,
        "cpu": _num(attrs, 50),
        "power": _num(attrs, 30),
        "slots_used": _num(attrs, 47),
        "rig_cost": _num(attrs, 1153),
        "drone_bandwidth": _num(attrs, 1272),
        "cap_need": _num(attrs, 6),
        "duration": _num(attrs, 73),
        "active_capable": ((_num(attrs, 6) > 0 and _num(attrs, 73) > 0) or bool(effects & {34, 40, 42})),
        "known": item is not None,
    }


def skill_pairs(attrs: dict) -> list:
    out = []
    for sid_attr, lvl_attr in REQ_SKILL_ATTRS:
        sid = attrs.get(sid_attr)
        lvl = attrs.get(lvl_attr)
        if sid:
            try:
                out.append((int(sid), int(lvl or 1)))
            except (TypeError, ValueError):
                continue
    return out


def cumulative_sp(rank: float, target_level: int) -> int:
    """到目标等级所需的技能点(等级门槛本身即累计值)。

    官方公式:SP(等级N) = 250 * skillTimeConstant * sqrt(32)^(N-1)
    rank1 时依次为 250 / 1414 / 8000 / 45255 / 256000。
    """
    rank = max(1.0, float(rank or 1.0))
    level = max(1, int(target_level or 1))
    return int(round(250.0 * rank * (math.sqrt(32) ** (level - 1))))


CHARACTER_ATTRIBUTE_KEYS = {
    165: "intelligence",
    166: "memory",
    167: "perception",
    168: "willpower",
    169: "charisma",
}
IMPLANT_BONUS_ATTRS = {
    175: "charisma",
    176: "intelligence",
    177: "memory",
    178: "perception",
    179: "willpower",
}


def implant_bonus_map(implant_ids) -> dict:
    """根据脑插 type_id 汇总角色属性加成。"""
    ids = [int(i) for i in (implant_ids or []) if i is not None]
    bonuses = {name: 0.0 for name in CHARACTER_ATTRIBUTE_KEYS.values()}
    if not ids:
        return bonuses
    attrs_map = name_index.get_type_attrs(ids)
    for attrs in attrs_map.values():
        for attr_id, name in IMPLANT_BONUS_ATTRS.items():
            try:
                bonuses[name] += float(attrs.get(attr_id) or 0.0)
            except (TypeError, ValueError):
                continue
    return bonuses


def _implant_bonus_map(char: dict) -> dict:
    data = (char or {}).get("implants")
    if data is None:
        raw = (char or {}).get("implants_json")
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                data = {}
        else:
            data = raw if isinstance(raw, dict) else {}
    if isinstance(data, dict):
        bonuses = data.get("bonuses")
        if isinstance(bonuses, dict):
            return {name: float(bonuses.get(name) or 0.0) for name in CHARACTER_ATTRIBUTE_KEYS.values()}
        data = data.get("implant_ids") or []
    return implant_bonus_map(data or [])


def _character_attribute_map(char: dict) -> dict:
    attrs = (char or {}).get("attributes")
    if attrs is None:
        attrs = (char or {}).get("attributes_json")
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except (TypeError, ValueError):
            attrs = {}
    return attrs if isinstance(attrs, dict) else {}


def skill_training_rate(skill_attrs: dict, char: dict):
    """按技能主/副属性计算当前 SP/小时；属性缺失时回退队列/默认速度。"""
    attrs = _character_attribute_map(char)
    primary_key = CHARACTER_ATTRIBUTE_KEYS.get(int(skill_attrs.get(180) or 0))
    secondary_key = CHARACTER_ATTRIBUTE_KEYS.get(int(skill_attrs.get(181) or 0))
    try:
        primary = float(attrs.get(primary_key) or 0) if primary_key else 0.0
        secondary = float(attrs.get(secondary_key) or 0) if secondary_key else 0.0
    except (TypeError, ValueError):
        primary = secondary = 0.0
    if primary > 0 and secondary > 0:
        return 60.0 * (primary + secondary / 2.0), "attributes"
    return training_rate(char)


def remap_info(attrs: dict, now: datetime | None = None) -> dict:
    """根据 ESI 属性数据计算奖励重置与普通重置的可用次数。"""
    attrs = attrs if isinstance(attrs, dict) else {}
    now = now or datetime.now(timezone.utc)
    try:
        bonus = max(0, int(attrs.get("bonus_remaps") or 0))
    except (TypeError, ValueError):
        bonus = 0
    cooldown = attrs.get("accrued_remap_cooldown_date")
    normal = 1
    if cooldown:
        try:
            available_at = datetime.fromisoformat(str(cooldown).replace("Z", "+00:00"))
            if available_at.tzinfo is None:
                available_at = available_at.replace(tzinfo=timezone.utc)
            normal = 1 if available_at <= now else 0
        except (TypeError, ValueError):
            normal = 0
    return {
        "bonus_remaps": bonus,
        "normal_available": normal,
        "total_remaps": bonus + normal,
        "next_normal_remap": cooldown,
    }


def training_rate(char: dict):
    """从角色队列估算训练速度(SP/小时);不可用时返回默认值与来源标记。"""
    try:
        queue = json.loads(char.get("queue_json") or "[]")
    except (TypeError, ValueError):
        queue = []
    if queue:
        q = queue[0]
        start, finish = q.get("training_start_date"), q.get("finish_date")
        s_sp, e_sp = q.get("start_sp"), q.get("end_sp")
        if start and finish and s_sp is not None and e_sp is not None and e_sp > s_sp:
            from datetime import datetime
            try:
                st = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                fn = datetime.fromisoformat(str(finish).replace("Z", "+00:00"))
                hours = (fn - st).total_seconds() / 3600.0
                if hours > 0:
                    return (e_sp - s_sp) / hours, "queue"
            except ValueError:
                pass
    return DEFAULT_TRAINING_SP_HOUR, "default"


def _char_skill_map(char: dict) -> dict:
    try:
        skills = json.loads(char.get("skills_json") or "{}")
    except (TypeError, ValueError):
        skills = {}
    return {s.get("skill_id"): s for s in skills.get("skills", [])}


def need_modifiers(character: dict) -> list:
    """从角色技能的效果(modifierInfo)中提取“模块 CPU/PG 需求削减”规则。

    规则形如:某技能的效果按 skillTypeID / groupID 定位模块,
    用技能自身属性(310/323)按等级修改模块的 cpu(50)/power(30) 需求。
    """
    if not character:
        return []
    cmap = _char_skill_map(character)
    ids = list(cmap.keys())
    attrs = name_index.get_type_attrs(ids)
    effects = name_index.get_type_effects(ids)
    all_effects = set()
    for s in effects.values():
        all_effects |= s
    infos = name_index.get_effect_modifier_info(all_effects)
    mods = []
    for sid, cur in cmap.items():
        lvl = int(cur.get("active_skill_level") or 0)
        if lvl <= 0:
            continue
        for eid in effects.get(sid, set()):
            raw = infos.get(eid) or ""
            if not raw:
                continue
            try:
                entries = json.loads(raw)
            except (TypeError, ValueError):
                continue
            for en in entries:
                if en.get("domain") != "shipID":
                    continue
                if en.get("modifiedAttributeID") not in (50, 30):
                    continue
                bonus = attrs.get(sid, {}).get(en.get("modifyingAttributeID"))
                if bonus is None:
                    continue
                mods.append({
                    "skill_type": en.get("skillTypeID"),
                    "group": en.get("groupID"),
                    "attr": en.get("modifiedAttributeID"),
                    "factor": 1 + (float(bonus) / 100.0) * lvl,
                })
    return mods

def expand_skill_requirements(need: dict) -> dict:
    """把技能需求展开为完整前置链(取每个技能的最高需求等级)。"""
    out = dict(need)
    frontier = list(out.keys())
    while frontier:
        attrs = name_index.get_type_attrs(frontier)
        nxt = []
        for sid in frontier:
            for psid, plvl in skill_pairs(attrs.get(sid, {})):
                if out.get(psid, 0) < plvl:
                    out[psid] = plvl
                    nxt.append(psid)
        frontier = nxt
    return out

def _modifier_entries(source_attrs: dict, effect_ids, penalize: bool = True, skip_resistance: bool = False) -> list:
    """从某来源(技能/装备)的效果中取出作用于舰船的属性修正。"""
    out = []
    infos = name_index.get_effect_modifier_info(effect_ids)
    for raw in infos.values():
        if not raw:
            continue
        try:
            entries = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for en in entries:
            if en.get("domain") != "shipID" or en.get("func") != "ItemModifier":
                continue
            if en.get("skillTypeID") or en.get("groupID"):
                continue  # 带限制的修正用于模块自身,不作用于舰船基础属性
            val = source_attrs.get(en.get("modifyingAttributeID"))
            if val is None:
                continue
            mod_attr = en.get("modifiedAttributeID")
            if skip_resistance and mod_attr in (109, 110, 111, 113, 267, 268, 269, 270, 271, 272, 273, 274, 974, 975, 976, 977):
                continue
            out.append({
                "attr": mod_attr,
                "op": en.get("operation"),
                "value": float(val),
                "penalize": penalize,
            })
    return out


def _apply_attr_mods(base: dict, mods: list) -> dict:
    """按属性分组应用修正,顺序:赋值 → 加法 → 乘法 → 百分比。

    仅“装备来源的百分比修正”计算堆叠惩罚(技能加成不惩罚,
    加法/乘法类修正也不惩罚,和游戏内电容电池/继电器一致)。
    """
    grouped = {}
    for m in mods:
        grouped.setdefault(m["attr"], []).append(m)
    out = dict(base)
    for attr, ms in grouped.items():
        cur = float(out.get(attr, 0.0) or 0.0)
        assigns = [m for m in ms if m["op"] == 0]
        adds = [m for m in ms if m["op"] in (2, 7)]
        others = [m for m in ms if m["op"] not in (0, 2, 4, 6, 7)]
        mults = [m for m in ms if m["op"] == 4]
        pcts = [m for m in ms if m["op"] == 6]
        if assigns:
            cur = assigns[-1]["value"]
        for m in adds + others:
            cur += m["value"]
        for m in mults:
            cur *= m["value"]
        penalized = sorted([m for m in pcts if m.get("penalize")], key=lambda x: abs(x["value"]), reverse=True)
        penalty_index = {id(m): i for i, m in enumerate(penalized)}
        for m in pcts:
            eff = 1.0
            if m.get("penalize"):
                i = penalty_index[id(m)]
                if i > 0:
                    eff = 0.5 ** ((i / 2.22292081) ** 2)
            cur *= (1 + (m["value"] / 100.0) * eff)
        out[attr] = cur
    return out


def compute_stats(ship_type_id: int, items: list, character: dict = None) -> dict:
    """第一阶段属性:电容 / 锁定 / 机动 / 防御(近似计算,含堆叠惩罚)。

    说明:主动装备按其类型(护盾/装甲)应用抗性加成;DPS、锁定时间等留待后续。
    """
    ship = name_index.get_type_attrs([ship_type_id]).get(ship_type_id, {})
    base = dict(ship)
    skills_mods, item_mods = [], []
    if character:
        cmap = _char_skill_map(character)
        ids = list(cmap.keys())
        attrs = name_index.get_type_attrs(ids)
        effs = name_index.get_type_effects(ids)
        for sid, cur in cmap.items():
            if int(cur.get("active_skill_level") or 0) <= 0:
                continue
            lvl = int(cur.get("active_skill_level") or 0)
            # 技能属性按等级放大(技能属性值多为每级数值)
            src = {}
            for k, v in attrs.get(sid, {}).items():
                if k in (280,):
                    continue
                src[k] = v * lvl
            skills_mods += _modifier_entries(src, effs.get(sid, set()), penalize=False)

    # ---- 抗性:按游戏面板顺序 电(EM)/热(Thermal)/动(Kinetic)/爆(Explosive) ----
    SHIELD_ORDER = [271, 274, 273, 272]
    ARMOR_ORDER = [267, 270, 269, 268]
    HULL_ORDER = [113, 110, 111, 109]
    DC_HULL_ORDER = [974, 977, 976, 975]
    BONUS_TO_INDEX = {984: 0, 987: 1, 986: 2, 985: 3}
    RES_ATTRS = set(SHIELD_ORDER + ARMOR_ORDER + HULL_ORDER)

    def _multiplier_values(attrs, order):
        return [float(attrs.get(k, 1.0) or 1.0) for k in order]

    def _stack_penalty(index):
        return 1.0 if index <= 0 else 0.5 ** ((index / 2.22292081) ** 2)

    def _apply_bonuses(vuln, bonuses):
        out = list(vuln)
        for idx, values in enumerate(bonuses):
            ordered = sorted((float(v) for v in values), key=abs, reverse=True)
            for n, value in enumerate(ordered):
                out[idx] *= 1.0 + (value / 100.0) * _stack_penalty(n)
        return out

    shield_vuln = _multiplier_values(ship, SHIELD_ORDER)
    armor_vuln = _multiplier_values(ship, ARMOR_ORDER)
    hull_vuln = _multiplier_values(ship, HULL_ORDER)
    shield_bonuses = [[] for _ in range(4)]
    armor_bonuses = [[] for _ in range(4)]

    # 船体自身抗性加成(例如 Moa 的 shipBonusCC2=-4%/级)。
    cmap = _char_skill_map(character) if character else {}
    ship_effs = name_index.get_type_effects([ship_type_id]).get(ship_type_id, set())
    ship_infos = name_index.get_effect_modifier_info(ship_effs)
    required = [(182, 277), (183, 278), (184, 279), (1285, 1286), (1289, 1287), (1290, 1288)]

    def _skill_level(skill_id):
        try:
            cur = cmap.get(int(skill_id))
        except (TypeError, ValueError):
            cur = None
        return int((cur or {}).get("active_skill_level") or 0)

    for raw in ship_infos.values():
        if not raw:
            continue
        try:
            entries = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for en in entries:
            if en.get("domain") != "shipID" or en.get("func") != "ItemModifier":
                continue
            target = en.get("modifiedAttributeID")
            if target not in RES_ATTRS:
                continue
            val = ship.get(en.get("modifyingAttributeID"))
            if val is None:
                continue
            scale = 1.0
            skill_id = en.get("skillTypeID")
            if skill_id:
                scale = _skill_level(skill_id) or 1.0
            else:
                for sid_attr, lvl_attr in required:
                    sid = ship.get(sid_attr)
                    if sid:
                        scale = _skill_level(sid) or float(ship.get(lvl_attr) or 1.0)
                        break
            value = float(val) * scale
            if target in SHIELD_ORDER:
                shield_vuln[SHIELD_ORDER.index(target)] *= 1.0 + value / 100.0
            elif target in ARMOR_ORDER:
                armor_vuln[ARMOR_ORDER.index(target)] *= 1.0 + value / 100.0
            elif target in HULL_ORDER:
                hull_vuln[HULL_ORDER.index(target)] *= 1.0 + value / 100.0

    # 装备抗性:损伤控制使用 267-270/974-977;其他抗性装备使用 984-987。
    for entry in items:
        tid = int(entry.get("type_id") or 0)
        info = item_info(tid)
        if not info["known"]:
            continue
        attrs = name_index.get_type_attrs([tid]).get(tid, {})
        cap_cost = float(attrs.get(6) or 0.0)
        if cap_cost > 0 and not entry.get("active"):
            continue
        qty = max(1, int(entry.get("qty") or 1))
        effs = name_index.get_type_effects([tid]).get(tid, set())
        for _ in range(qty):
            item_mods += _modifier_entries(attrs, effs, skip_resistance=True)

        # Damage Control 使用完整装甲/护盾/结构修正。游戏面板中 DC II 的护盾与装甲同为 15% 修正。
        if all(attrs.get(k) is not None for k in (267, 268, 269, 270)) and any(
            attrs.get(k) is not None for k in (974, 975, 976, 977)
        ):
            dc_armor = _multiplier_values(attrs, ARMOR_ORDER)
            dc_hull = _multiplier_values(attrs, DC_HULL_ORDER)
            for _ in range(qty):
                for i in range(4):
                    shield_vuln[i] *= dc_armor[i]
                    armor_vuln[i] *= dc_armor[i]
                    hull_vuln[i] *= dc_hull[i]
            continue

        found_effect = False
        for raw in name_index.get_effect_modifier_info(effs).values():
            if not raw:
                continue
            try:
                entries = json.loads(raw)
            except (TypeError, ValueError):
                continue
            for en in entries:
                if en.get("domain") != "shipID" or en.get("func") != "ItemModifier":
                    continue
                target = en.get("modifiedAttributeID")
                bonus_attr = en.get("modifyingAttributeID")
                if bonus_attr not in BONUS_TO_INDEX or attrs.get(bonus_attr) is None:
                    continue
                value = float(attrs[bonus_attr])
                if target in SHIELD_ORDER:
                    shield_bonuses[SHIELD_ORDER.index(target)].extend([value] * qty)
                    found_effect = True
                elif target in ARMOR_ORDER:
                    armor_bonuses[ARMOR_ORDER.index(target)].extend([value] * qty)
                    found_effect = True
        if found_effect:
            continue

        # 兼容测试/旧索引:没有效果表时按装备名称归类。
        values = [(BONUS_TO_INDEX[k], float(attrs[k])) for k in BONUS_TO_INDEX if attrs.get(k) is not None]
        if not values:
            continue
        nm_zh = info.get("name") or ""
        nm_en = info.get("name_en") or ""
        if "护盾" in nm_zh or "Shield" in nm_en:
            for idx, value in values:
                shield_bonuses[idx].extend([value] * qty)
        elif "装甲" in nm_zh or "Armor" in nm_en:
            for idx, value in values:
                armor_bonuses[idx].extend([value] * qty)

    shield_vuln = _apply_bonuses(shield_vuln, shield_bonuses)
    armor_vuln = _apply_bonuses(armor_vuln, armor_bonuses)
    stats_attrs = _apply_attr_mods(base, skills_mods + item_mods)
    def g(key, default=0.0):
        try:
            return float(stats_attrs.get(key, default) or 0.0)
        except (TypeError, ValueError):
            return default

    cap_max = g(482)
    cap_recharge_ms = g(55)
    cap_avg = (cap_max / (cap_recharge_ms / 1000.0)) if cap_recharge_ms else 0.0
    cap_peak = (cap_max / (cap_recharge_ms / 1000.0) * 1.6) if cap_recharge_ms else 0.0

    # 电容持续消耗(激活模块估算:activation cost / duration)
    cap_drain = 0.0
    for entry in items:
        if not entry.get("active"):
            continue  # 未启用的装备不消耗电容
        tid = int(entry.get("type_id") or 0)
        attrs = name_index.get_type_attrs([tid]).get(tid, {})
        cost = attrs.get(6)
        dur = attrs.get(73)
        qty = int(entry.get("qty") or 1)
        if cost and dur:
            cap_drain += (float(cost) / (float(dur) / 1000.0)) * qty
    stable_pct = None
    if cap_drain <= 0:
        stable_pct = 100.0
    elif cap_avg > 0:
        tau = (cap_recharge_ms / 1000.0) / 5.0
        steady = cap_max - cap_drain * tau
        stable_pct = round(max(0.0, steady / cap_max) * 100.0, 1)

    def resist_value(v):
        return round((1.0 - max(0.0, min(1.0, v))) * 100.0, 1)

    shield_hp = g(263)
    armor_hp = g(265)
    hull_hp = g(9)
    # 顺序固定为:电 / 热 / 动 / 爆。
    shield_res = [resist_value(v) for v in shield_vuln]
    armor_res = [resist_value(v) for v in armor_vuln]
    hull_res = [resist_value(v) for v in hull_vuln]
    avg = lambda arr: sum(arr) / 4.0 / 100.0
    ehp = 0.0
    for hp, res in ((shield_hp, shield_res), (armor_hp, armor_res), (hull_hp, hull_res)):
        r = min(avg(res), 0.95)
        ehp += hp / (1 - r)

    agility = g(70)
    mass = g(4) or float((name_index.get_item(ship_type_id) or {}).get("mass") or 0.0)
    # 加上已装配装备的质量(无人机在货舱,不计入)
    for entry in items:
        tid = int(entry.get("type_id") or 0)
        info = item_info(tid)
        if info.get("category_id") == 18:
            continue
        mass += float((name_index.get_item(tid) or {}).get("mass") or 0.0) * int(entry.get("qty") or 1)
    # 游戏内“朝向时间”= 质量 × 灵敏度 / 1e6 × ln(4)
    align = (math.log(4) * mass * agility / 1e6) if (mass and agility) else 0.0
    shield_regen_ms = g(479) * (g(134, 1.0) or 1.0)
    shield_regen = (shield_hp / (shield_regen_ms / 1000.0)) if shield_regen_ms else 0.0

    return {
        "capacitor": {"capacity": round(cap_max, 1), "recharge_s": round(cap_recharge_ms / 1000.0, 2),
                      "recharge_avg": round(cap_avg, 2), "recharge_peak": round(cap_peak, 2),
                      "drain": round(cap_drain, 2), "stable_pct": stable_pct},
        "targeting": {"range": round(g(76) / 1000.0, 1), "max_targets": int(g(192)),
                      "scan_resolution": round(g(564), 1),
                      "sensor": {"radar": round(g(208), 2), "ladar": round(g(209), 2),
                                 "magnetometric": round(g(210), 2), "gravimetric": round(g(211), 2)}},
        "mobility": {"max_velocity": round(g(37), 1), "align_s": round(align, 2),
                     "warp_speed": round(g(600), 2), "mass": int(mass)},
        "defense": {"shield_hp": round(shield_hp, 1), "armor_hp": round(armor_hp, 1),
                    "hull_hp": round(hull_hp, 1), "shield_res": shield_res, "armor_res": armor_res,
                    "hull_res": hull_res, "ehp": int(round(ehp)),
                    "shield_regen": round(shield_regen, 2)},
        "notes": ["属性为基础+技能效应近似计算(主动抗性按装备类型归类,含堆叠惩罚)", "朝向时间=质量×灵敏度/1e6×ln4(含技能与装备质量)", "电容:回充时间/峰值由船体与被动装备决定;启停主动装备只影响持续消耗与稳定度", "锁定时间/DPS 待后续版本"],
    }

def analyze(ship_type_id: int, items: list, character: dict = None, skill_mode: str = "online", extra_skills: list = None) -> dict:
    """分析配装:槽位/CPU/PG/校准/无人机校验 + 技能需求(可选角色对比)。"""
    ship_info = item_info(ship_type_id)
    ship_attrs = name_index.get_type_attrs([ship_type_id]).get(ship_type_id, {})
    need_mods = need_modifiers(character) if character else []

    known_ids = {ship_type_id} | {int(i["type_id"]) for i in items if i.get("type_id")}
    for i in items:
        if i.get("charge_type_id"):
            known_ids.add(int(i["charge_type_id"]))
    attrs_map = name_index.get_type_attrs(list(known_ids))
    items_map = name_index.get_items(list(known_ids))

    def nm(tid):
        it = items_map.get(tid) or {}
        return it.get("name_zh") or it.get("name_en") or str(tid)

    slot_totals = {
        SLOT_HI: _num(ship_attrs, 14), SLOT_MED: _num(ship_attrs, 13),
        SLOT_LOW: _num(ship_attrs, 12), SLOT_RIG: _num(ship_attrs, 1137),
    }
    slot_used = {SLOT_HI: 0.0, SLOT_MED: 0.0, SLOT_LOW: 0.0, SLOT_RIG: 0.0}
    cpu_used = pg_used = rig_cost = weapon_slots = 0.0
    drone_bay_used = drone_bw_used = 0.0
    cpu_out_mult = pg_out_mult = 1.0
    cpu_out_add = pg_out_add = 0.0
    out_bonus_count = 0
    drone_count = 0
    unknown = []
    detail_items = []

    for entry in items:
        tid = int(entry.get("type_id") or 0)
        qty = int(entry.get("qty") or 1)
        info = item_info(tid)
        if not info["known"]:
            unknown.append({"type_id": tid, "name": str(tid), "reason": "本地索引中没有该物品"})
            continue
        slot = entry.get("slot") or info["slot_guess"]
        if slot == SLOT_DRONE or info.get("category_id") == 18:
            slot = SLOT_DRONE
            drone_count += qty
            drone_bay_used += _num(info, "volume") * qty
            bw = name_index.get_type_attrs([tid]).get(tid, {}).get(1272)
            drone_bw_used += _num({1272: bw}, 1272) * qty
        elif slot in slot_used:
            slot_used[slot] += qty
            need_cpu = need_pg = 1.0
            if need_mods:
                req_skills = {sid for sid, _ in skill_pairs(attrs_map.get(tid, {}))}
                grp = (items_map.get(tid) or {}).get("group_id")
                for m in need_mods:
                    hit = (m["skill_type"] and m["skill_type"] in req_skills) or (m["group"] and m["group"] == grp)
                    if not hit:
                        continue
                    if m["attr"] == 50:
                        need_cpu *= m["factor"]
                    else:
                        need_pg *= m["factor"]
            cpu_used += info["cpu"] * qty * need_cpu
            pg_used += info["power"] * qty * need_pg
            rig_cost += info["rig_cost"] * qty
            weapon_slots += info["slots_used"] * qty
            a = attrs_map.get(tid, {})
            for _ in range(max(1, qty)):
                for attr in (424, 425, 288):  # CPU 输出百分比加成
                    v = a.get(attr)
                    if v:
                        cpu_out_mult *= (1 + float(v) / 100.0)
                        out_bonus_count += 1
                if a.get(1377):
                    cpu_out_add += float(a[1377])
                for attr in (334, 121, 313):  # 电力输出百分比加成
                    v = a.get(attr)
                    if v:
                        pg_out_mult *= (1 + float(v) / 100.0)
                        out_bonus_count += 1
                if a.get(1378):
                    pg_out_add += float(a[1378])
                if a.get(202):  # cpuMultiplier(如协处理器,+10% 表示为 1.1)
                    cpu_out_mult *= float(a[202])
                    out_bonus_count += 1
                if a.get(145):  # powerOutputMultiplier
                    pg_out_mult *= float(a[145])
                    out_bonus_count += 1
        detail_items.append({
            "type_id": tid, "name": nm(tid), "slot": slot or "unknown", "qty": qty,
            "cpu": info["cpu"], "power": info["power"],
            "charge_type_id": entry.get("charge_type_id"),
            "passive": not info["active_capable"],
        })

    # 角色技能对 CPU/PG 输出的加成(同一账号下按可用等级计算)
    cpu_skill_mult = pg_skill_mult = 1.0

    skill_bonus_notes = []
    if character:
        cmap_pre = _char_skill_map(character)
        sh_ids = list(cmap_pre.keys())
        sh_attrs = name_index.get_type_attrs(sh_ids)
        sh_items = name_index.get_items(sh_ids)
        for sid, cur in cmap_pre.items():
            lvl = int(cur.get("active_skill_level") or 0)
            if lvl <= 0:
                continue
            a = sh_attrs.get(sid, {})
            it = sh_items.get(sid) or {}
            nmz = it.get("name_zh") or it.get("name_en") or str(sid)
            for attr in (424, 425):  # CPU管理学等全局 CPU 输出技能加成
                v = a.get(attr)
                if v:
                    cpu_skill_mult *= (1 + (float(v) / 100.0) * lvl)
                    skill_bonus_notes.append(f"{nmz} Lv{lvl}(CPU +{float(v) * lvl:.0f}%)")
                    break
            for attr in (313, 121):  # 能量栅格管理学等全局 PG 输出技能加成
                v = a.get(attr)
                if v:
                    pg_skill_mult *= (1 + (float(v) / 100.0) * lvl)
                    skill_bonus_notes.append(f"{nmz} Lv{lvl}(PG +{float(v) * lvl:.0f}%)")
                    break



    cpu_total = (_num(ship_attrs, 48) + cpu_out_add) * cpu_out_mult * cpu_skill_mult
    pg_total = (_num(ship_attrs, 11) + pg_out_add) * pg_out_mult * pg_skill_mult
    notes = ["CPU/能量栅格基础值已计入装备/改装件输出加成", "炮台/发射器槽位为合计校验", "技能需求已包含前置技能链"]
    if out_bonus_count:
        notes.append(f"已计入 {out_bonus_count} 项装备/改装件输出加成(百分比按近似叠加)")
    if skill_bonus_notes:
        notes.append("技能加成:" + "、".join(skill_bonus_notes))
    limits = {
        "cpu": {"used": round(cpu_used, 2), "total": round(cpu_total, 2), "ok": cpu_used <= cpu_total},
        "power": {"used": round(pg_used, 2), "total": round(pg_total, 2), "ok": pg_used <= pg_total},
        "slots": {
            k: {"used": slot_used[k], "total": slot_totals[k], "ok": slot_used[k] <= slot_totals[k]}
            for k in slot_used
        },
        "rig_calibration": {
            "used": round(rig_cost, 2), "total": _num(ship_attrs, 1132),
            "ok": rig_cost <= _num(ship_attrs, 1132),
        },
        "weapon_slots": {
            "used": weapon_slots,
            "total": _num(ship_attrs, 101) + _num(ship_attrs, 102),
            "ok": weapon_slots <= (_num(ship_attrs, 101) + _num(ship_attrs, 102)),
        },
        "drone_bay": {
            "used": round(drone_bay_used, 2), "total": _num(ship_attrs, 283),
            "ok": drone_bay_used <= _num(ship_attrs, 283),
        },
        "drone_bandwidth": {
            "used": round(drone_bw_used, 2), "total": _num(ship_attrs, 1271),
            "ok": drone_bw_used <= _num(ship_attrs, 1271),
        },
        "drone_count": {
            "used": drone_count, "total": _num(ship_attrs, 352),
            "ok": (drone_count <= _num(ship_attrs, 352)) if _num(ship_attrs, 352) else True,
        },
        "notes": notes,
    }

    # 技能需求:船体 + 装备 + 弹药
    need = {}
    ids_for_req = {ship_type_id} | {int(i["type_id"]) for i in items if i.get("type_id")}
    for i in items:
        if i.get("charge_type_id"):
            ids_for_req.add(int(i["charge_type_id"]))
    for tid in ids_for_req:
        for sid, lvl in skill_pairs(attrs_map.get(tid, {})):
            need[sid] = max(need.get(sid, 0), lvl)
    for extra in extra_skills or []:
        try:
            sid = int(extra.get("skill_id") or 0)
            lvl = max(1, min(5, int(extra.get("need_level") or 1)))
        except (TypeError, ValueError, AttributeError):
            continue
        if sid:
            need[sid] = max(need.get(sid, 0), lvl)
    need = expand_skill_requirements(need)  # 展开技能前置链
    if skill_mode == "all5":
        need = {sid: 5 for sid in need}
    elif skill_mode == "all4":
        # 全部到 4;但前置链中必须达到 5 的技能保持 5
        need = {sid: (5 if lvl >= 5 else 4) for sid, lvl in need.items()}
    skill_ids = list(need.keys())
    skill_items = name_index.get_items(skill_ids)
    skill_attrs = name_index.get_type_attrs(skill_ids)

    def skill_name(sid):
        it = skill_items.get(sid) or {}
        return it.get("name_zh") or it.get("name_en") or f"技能 {sid}"

    def skill_name_en(sid):
        it = skill_items.get(sid) or {}
        return it.get("name_en") or skill_name(sid)

    skills = [
        {"skill_id": sid, "name": skill_name(sid), "name_en": skill_name_en(sid), "need_level": lvl}
        for sid, lvl in sorted(need.items(), key=lambda kv: skill_name(kv[0]))
    ]

    result = {
        "ship": {**ship_info, "slots": slot_totals},
        "items": detail_items,
        "unknown": unknown,
        "limits": limits,
        "skills": skills,
        "stats": compute_stats(ship_type_id, items, character),
        "character": None,
    }

    if character:
        cmap = _char_skill_map(character)
        fallback_rate, fallback_src = training_rate(character)
        rows = []
        missing = 0
        for sk in skills:
            sid = sk["skill_id"]
            cur = cmap.get(sid) or {}
            active = int(cur.get("active_skill_level") or 0)
            trained = int(cur.get("trained_skill_level") or 0)
            sp_now = int(cur.get("skillpoints_in_skill") or 0)
            rank = skill_attrs.get(sid, {}).get(275, 1.0)
            required_sp = cumulative_sp(rank, sk["need_level"])
            deficit = max(0, required_sp - sp_now)
            ok = active >= sk["need_level"]
            if not ok:
                missing += 1
            rate, rate_source = skill_training_rate(skill_attrs.get(sid, {}), character)
            hours = round(deficit / rate, 2) if (deficit and rate > 0) else 0
            rows.append({
                **sk, "active_level": active, "trained_level": trained, "ok": ok,
                "sp_deficit": deficit, "rate_sp_hour": round(rate, 2),
                "rate_source": rate_source, "hours": hours,
            })
        rows.sort(key=lambda r: (r["ok"], -r["sp_deficit"], r["name"]))
        plan_started_at = datetime.now(timezone.utc)
        plan_cursor = plan_started_at
        for row in rows:
            if not row["ok"] and row["hours"] > 0:
                plan_cursor += timedelta(hours=row["hours"])
                row["finish_at"] = plan_cursor.isoformat().replace("+00:00", "Z")
            else:
                row["finish_at"] = None
        plan_finished_at = plan_cursor.isoformat().replace("+00:00", "Z")
        total_deficit = int(sum(r["sp_deficit"] for r in rows))
        character_attributes = _character_attribute_map(character)
        result["character"] = {
            "character_id": character.get("character_id"),
            "character_name": character.get("character_name"),
            "remap": remap_info(character_attributes),
            "skill_levels": {
                int(sid): max(
                    int((cur or {}).get("active_skill_level") or 0),
                    int((cur or {}).get("trained_skill_level") or 0),
                )
                for sid, cur in cmap.items() if sid is not None
            },
            "rate_sp_hour": round(fallback_rate, 2),
            "rate_source": fallback_src,
            "rate_mode": "per_skill" if _character_attribute_map(character) else "fallback",
            "plan_started_at": plan_started_at.isoformat().replace("+00:00", "Z"),
            "plan_finished_at": plan_finished_at,
            "missing_count": missing,
            "total_sp_deficit": total_deficit,
            "total_hours": round(sum(r["hours"] for r in rows), 2),
            "items": rows,
        }
    return result


UPGRADE_ATTR_IDS = {
    9, 11, 37, 48, 55, 70, 76, 192, 208, 209, 210, 211, 263, 265, 479, 482, 564,
    267, 268, 269, 270, 271, 272, 273, 274, 974, 975, 976, 977,
}


def find_upgrade_skills(ship_type_id: int, items: list, character: dict = None) -> dict:
    """规则筛选可能提升当前配装的技能，区分确定建议与需人工检查的专业研究。"""
    fit_ids = {int(ship_type_id)}
    for entry in items or []:
        tid = int(entry.get("type_id") or 0)
        if tid:
            fit_ids.add(tid)
        charge_id = int(entry.get("charge_type_id") or 0)
        if charge_id:
            fit_ids.add(charge_id)
    items_map = name_index.get_items(fit_ids)
    attrs_map = name_index.get_type_attrs(fit_ids)
    fit_groups = {
        int((items_map.get(tid) or {}).get("group_id"))
        for tid in fit_ids if (items_map.get(tid) or {}).get("group_id")
    }
    fit_attrs = set()
    for tid in fit_ids:
        attrs = attrs_map.get(tid, {})
        fit_attrs |= set(attrs)
        # 发射器/武器属性 604-610 指向其允许使用的弹药组。
        for attr_id in (604, 605, 606, 609, 610):
            group_id = attrs.get(attr_id)
            if group_id:
                try:
                    fit_groups.add(int(group_id))
                except (TypeError, ValueError):
                    pass
    direct = {}
    for tid in fit_ids:
        for sid, lvl in skill_pairs(attrs_map.get(tid, {})):
            direct[sid] = max(direct.get(sid, 0), lvl)
    required = set(expand_skill_requirements(direct))
    equipment_direct = {}
    for tid in fit_ids - {int(ship_type_id)}:
        for sid, lvl in skill_pairs(attrs_map.get(tid, {})):
            equipment_direct[sid] = max(equipment_direct.get(sid, 0), lvl)
    equipment_required = set(expand_skill_requirements(equipment_direct))
    cmap = _char_skill_map(character) if character else {}
    skill_ids = name_index.skill_ids()
    skill_attrs = name_index.get_type_attrs(skill_ids)
    skill_effects = name_index.get_type_effects(skill_ids)
    all_effects = set()
    for effects in skill_effects.values():
        all_effects |= effects
    effect_infos = name_index.get_effect_modifier_info(all_effects)
    result = set()
    uncertain = set()
    for sid in skill_ids:
        if sid in required:
            continue
        if int((cmap.get(sid) or {}).get("active_skill_level") or 0) >= 5:
            continue
        for eid in skill_effects.get(sid, set()):
            raw = effect_infos.get(eid)
            if not raw:
                continue
            try:
                entries = json.loads(raw)
            except (TypeError, ValueError):
                continue
            hit = False
            for en in entries:
                if not isinstance(en, dict) or en.get("domain") == "itemID":
                    continue
                group_id = en.get("groupID")
                required_skill = en.get("skillTypeID")
                attr_id = en.get("modifiedAttributeID")
                if group_id is not None or required_skill is not None:
                    # 有明确装备组/技能限制时，必须命中限制条件；不能退回属性名匹配。
                    if group_id is not None and int(group_id) in fit_groups:
                        hit = True
                    elif required_skill is not None and int(required_skill) in required:
                        hit = True
                elif en.get("func") == "ItemModifier" and attr_id in fit_attrs:
                    hit = True
                if hit:
                    break
            if hit:
                result.add(sid)
                break
        if sid in result:
            continue
        # 专业研究/职业等级型技能常用 itemID 修改自身等级加成属性，
        # 再通过前置技能与当前装备的必需技能链产生关联。
        prereqs = {req for req, _ in skill_pairs(skill_attrs.get(sid, {}))}
        # 专业研究必须让它要求的整套前置技能都出现在装备直接要求中；
        # 不能只命中通用前置，否则轻型导弹专业研究会借 Missile Launcher Operation 误命中重型导弹。
        if prereqs and prereqs <= set(equipment_direct):
            for eid in skill_effects.get(sid, set()):
                raw = effect_infos.get(eid)
                if not raw:
                    continue
                try:
                    entries = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                for en in entries:
                    if not isinstance(en, dict):
                        continue
                    if (en.get("domain") == "itemID" and en.get("func") == "ItemModifier"
                            and en.get("modifyingAttributeID") == 280
                            and en.get("modifiedAttributeID") not in (None, 280)):
                        uncertain.add(sid)
                        break
                if sid in uncertain:
                    break
    return {
        "skill_ids": sorted(result),
        "uncertain_skill_ids": sorted(uncertain),
    }


def find_upgrade_skill_ids(ship_type_id: int, items: list, character: dict = None) -> list:
    """兼容旧调用：只返回确定可提升的技能 ID。"""
    return find_upgrade_skills(ship_type_id, items, character).get("skill_ids", [])


_EFT_HEADER = re.compile(r"^\[(.+?),\s*(.*)\]$")
_EFT_QTY = re.compile(r"\s+x(\d+)$", re.IGNORECASE)


def parse_eft(text: str) -> dict:
    """解析 EFT 配装文本,返回 {name, ship_type_id, items, unknown}。"""
    lines = [l.strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return {"error": "内容为空"}
    m = _EFT_HEADER.match(lines[0])
    if not m:
        return {"error": "首行应为 [舰船名, 配装名]"}
    ship = name_index.find_by_name(m.group(1).strip())
    if not ship:
        return {"error": f"无法识别舰船: {m.group(1).strip()}"}

    items = []
    unknown = []
    for line in lines[1:]:
        if line.startswith("["):
            continue
        qty = 1
        qm = _EFT_QTY.search(line)
        if qm:
            qty = int(qm.group(1))
            line = line[: qm.start()].strip()
        parts = [p.strip() for p in line.split(",")]
        item = name_index.find_by_name(parts[0])
        if not item:
            unknown.append({"line": line, "reason": "无法识别物品"})
            continue
        info = item_info(item["type_id"])
        charge_id = None
        if len(parts) > 1 and parts[1]:
            charge = name_index.find_by_name(parts[1])
            if charge:
                charge_id = charge["type_id"]
            else:
                unknown.append({"line": line, "reason": f"无法识别弹药: {parts[1]}"})
        slot = info["slot_guess"]
        if info.get("category_id") == 18:
            slot = SLOT_DRONE
        items.append({
            "type_id": item["type_id"], "qty": qty, "slot": slot,
            "charge_type_id": charge_id,
        })
    return {"name": (m.group(2) or "").strip(), "ship_type_id": ship["type_id"], "items": items, "unknown": unknown}











SLOT_ORDER = [("hi", "[Empty High slot]"), ("med", "[Empty Med slot]"),
              ("low", "[Empty Low slot]"), ("rig", "[Empty Rig slot]")]
SHIP_SLOT_ATTR = {"hi": 14, "med": 13, "low": 12, "rig": 1137}


def _en_name(type_id: int) -> str:
    it = name_index.get_item(type_id) or {}
    return it.get("name_en") or it.get("name_zh") or str(type_id)


def export_eft(ship_type_id: int, items: list, name: str = "") -> str:
    """按 CCP 官方配装剪贴板(EFT)格式导出,名称一律使用英文。"""
    ship_en = _en_name(ship_type_id)
    ship_attrs = name_index.get_type_attrs([ship_type_id]).get(ship_type_id, {})
    title = (name or "").strip() or "Fit"
    lines = [f"[{ship_en}, {title}]"]

    used = {k: 0 for k, _ in SLOT_ORDER}
    by_slot = {"hi": [], "med": [], "low": [], "rig": []}
    drones = {}
    extra = {}
    for it in items:
        tid = int(it.get("type_id") or 0)
        if not tid:
            continue
        info = item_info(tid)
        slot = it.get("slot") or info.get("slot_guess") or ""
        qty = int(it.get("qty") or 1)
        if slot == "drone" or info.get("category_id") == 18:
            drones[tid] = drones.get(tid, 0) + qty
            continue
        if slot in by_slot:
            charge = it.get("charge_type_id")
            line = _en_name(tid)
            if charge:
                line += ", " + _en_name(int(charge))
            by_slot[slot].append(line)
            used[slot] += 1
        else:
            extra[tid] = extra.get(tid, 0) + qty

    for slot, _empty_label in SLOT_ORDER:
        entries = by_slot[slot]
        if not entries:
            continue
        lines.append("")
        lines.extend(entries)

    if drones:
        lines.append("")
        for tid, qty in drones.items():
            lines.append(f"{_en_name(tid)} x{qty}")
    if extra:
        lines.append("")
        for tid, qty in extra.items():
            lines.append(f"{_en_name(tid)} x{qty}")

    return "\n".join(lines) + "\n"

















