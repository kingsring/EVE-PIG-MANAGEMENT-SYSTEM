"""技能训练方案最短时间优化器。

在满足技能等级前置依赖的前提下，将训练节点拆分、排序，并选择有限的
属性重置时点来降低整条训练队列的完成时间。
"""
from __future__ import annotations

import itertools
import json
import math
from datetime import datetime, timedelta, timezone

from . import fitting, name_index

ATTRIBUTE_ORDER = ("charisma", "intelligence", "memory", "perception", "willpower")
ATTRIBUTE_LABELS = {
    "charisma": "魅力",
    "intelligence": "智力",
    "memory": "记忆",
    "perception": "感知",
    "willpower": "意志力",
}
BASE_TOTAL = 99
BASE_MIN = 17
BASE_MAX = 27
PROFILE_LIMIT = 64
BEAM_WIDTH = 90
DEFAULT_BEAM_WIDTH = BEAM_WIDTH
MAX_NORMAL_REMAPS = 5


class OptimizerError(Exception):
    """优化器输入或运行状态错误。"""


def _attr_map(character: dict) -> dict:
    attrs = fitting._character_attribute_map(character)
    if not attrs:
        raise OptimizerError("角色属性尚未缓存，请先刷新角色。")
    return attrs


def _base_attributes(character: dict) -> dict:
    effective = _attr_map(character)
    bonuses = fitting._implant_bonus_map(character)
    base = {}
    for name in ATTRIBUTE_ORDER:
        try:
            value = float(effective.get(name) or 0)
            implant = float(bonuses.get(name) or 0)
            base[name] = max(BASE_MIN, min(BASE_MAX, value - implant))
        except (TypeError, ValueError):
            base[name] = BASE_MIN
    return base


def _effective_attributes(base: dict, bonuses: dict) -> dict:
    return {name: float(base.get(name, BASE_MIN)) + float(bonuses.get(name, 0.0) or 0.0) for name in ATTRIBUTE_ORDER}


def _skill_key(current: dict) -> int:
    return int(current.get("active_skill_level") or 0)


def _build_nodes(targets: list, character: dict):
    cmap = fitting._char_skill_map(character)
    target_levels = fitting.expand_skill_requirements({
        int(t.get("skill_id")): max(1, min(5, int(t.get("need_level") or 1)))
        for t in targets if t.get("skill_id")
    })
    skill_ids = list(target_levels)
    attrs_map = name_index.get_type_attrs(skill_ids)
    items_map = name_index.get_items(skill_ids)
    nodes = []
    by_key = {}
    for sid, target_level in target_levels.items():
        attrs = attrs_map.get(sid, {})
        rank = float(attrs.get(275) or 1.0)
        current = cmap.get(sid) or {}
        current_level = _skill_key(current)
        if current_level >= target_level:
            continue
        for level in range(current_level + 1, target_level + 1):
            start_sp = int(current.get("skillpoints_in_skill") or 0) if level == current_level + 1 else fitting.cumulative_sp(rank, level - 1)
            end_sp = fitting.cumulative_sp(rank, level)
            deps = []
            if level > 1:
                deps.append((sid, level - 1))
            for req_sid, req_level in fitting.skill_pairs(attrs):
                req_current = _skill_key(cmap.get(req_sid) or {})
                if req_current < req_level:
                    deps.append((int(req_sid), int(req_level)))
            node = {
                "skill_id": int(sid),
                "level": int(level),
                "name": (items_map.get(sid) or {}).get("name_zh") or (items_map.get(sid) or {}).get("name_en") or f"技能 {sid}",
                "name_en": (items_map.get(sid) or {}).get("name_en") or "",
                "rank": rank,
                "sp": max(0, end_sp - start_sp),
                "primary": fitting.CHARACTER_ATTRIBUTE_KEYS.get(int(attrs.get(180) or 0)),
                "secondary": fitting.CHARACTER_ATTRIBUTE_KEYS.get(int(attrs.get(181) or 0)),
                "deps": deps,
            }
            key = (int(sid), int(level))
            by_key[key] = node
            nodes.append(node)
    # Keep only prerequisites that are themselves trainable now.
    for node in nodes:
        node["dep_keys"] = [dep for dep in node["deps"] if dep in by_key]
    return nodes, by_key


def _duration_hours(node: dict, effective: dict, fallback_rate: float) -> float:
    primary = effective.get(node.get("primary"), 0.0) if node.get("primary") else 0.0
    secondary = effective.get(node.get("secondary"), 0.0) if node.get("secondary") else 0.0
    rate = 60.0 * (primary + secondary / 2.0) if primary > 0 and secondary > 0 else fallback_rate
    if rate <= 0:
        rate = fitting.DEFAULT_TRAINING_SP_HOUR
    return node["sp"] / rate


def _legal_base_vectors():
    # Five attributes, min 17, max 27, total 99.  The search space is small
    # enough to enumerate deterministically and then prune by plan cost.
    for values in itertools.product(range(BASE_MIN, BASE_MAX + 1), repeat=5):
        if sum(values) == BASE_TOTAL:
            yield dict(zip(ATTRIBUTE_ORDER, values))


def _profile_score(nodes, durations_by_profile, profile_idx):
    return sum(durations_by_profile[i][profile_idx] for i in range(len(nodes)))


def _candidate_profiles(nodes, character: dict, fallback_rate: float):
    bonuses = fitting._implant_bonus_map(character)
    current_base = _base_attributes(character)
    current_effective = _effective_attributes(current_base, bonuses)
    candidates = []
    seen = set()

    def add(base):
        effective = _effective_attributes(base, bonuses)
        key = tuple(round(effective[name], 6) for name in ATTRIBUTE_ORDER)
        if key in seen:
            return None
        seen.add(key)
        candidates.append({"base": dict(base), "effective": effective})

    add(current_base)
    for base in _legal_base_vectors():
        add(base)
    scored = []
    for idx, profile in enumerate(candidates):
        effective = profile["effective"]
        total = sum(_duration_hours(node, effective, fallback_rate) for node in nodes)
        scored.append((total, idx, profile))
    scored.sort(key=lambda x: (x[0], x[1]))
    selected = [item[2] for item in scored[:PROFILE_LIMIT]]
    selected_keys = {tuple(round(p["effective"][name], 6) for name in ATTRIBUTE_ORDER) for p in selected}
    # Keep a profile that is optimal for each observed primary/secondary pair.
    pairs = {(node.get("primary"), node.get("secondary")) for node in nodes if node.get("primary") and node.get("secondary")}
    for pair in pairs:
        pair_nodes = [node for node in nodes if (node.get("primary"), node.get("secondary")) == pair]
        best = None
        for _, idx, profile in scored:
            cost = sum(_duration_hours(node, profile["effective"], fallback_rate) for node in pair_nodes)
            if best is None or cost < best[0]:
                best = (cost, profile)
        if best:
            key = tuple(round(best[1]["effective"][name], 6) for name in ATTRIBUTE_ORDER)
            if key not in selected_keys:
                selected.append(best[1])
                selected_keys.add(key)
    current_key = tuple(round(current_effective[name], 6) for name in ATTRIBUTE_ORDER)
    if current_key not in selected_keys:
        selected.append({"base": current_base, "effective": current_effective})
    if not selected:
        selected = [{"base": current_base, "effective": current_effective}]
    return selected


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _state_reset_counts(state: dict, initial_bonus: int) -> tuple[int, int]:
    """返回候选状态的总重置次数和奖励重置次数。"""
    bonus_used = max(0, int(initial_bonus) - int(state.get("bonus_left") or 0))
    normal_used = max(0, int(state.get("normal_used") or 0))
    return bonus_used + normal_used, bonus_used


def _state_policy_score(state: dict, initial_bonus: int) -> tuple:
    """少重置策略的字典序评分：总次数、奖励次数、完成时间。"""
    total_resets, bonus_used = _state_reset_counts(state, initial_bonus)
    return total_resets, bonus_used, float(state.get("elapsed") or 0.0)


def optimize_skill_plan(
    character: dict,
    targets: list,
    now: datetime | None = None,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    max_time_regret_hours: float = 24.0,
) -> dict:
    """求解技能方案的近似最短完成时间，并优先减少属性重置次数。"""
    if character.get("needs_reauth"):
        raise OptimizerError("角色需要重新授权，才能读取脑插和属性信息。")
    implants_data = character.get("implants")
    if implants_data is None and character.get("implants_json"):
        try:
            implants_data = json.loads(character.get("implants_json"))
        except (TypeError, ValueError):
            implants_data = None
    if not implants_data:
        raise OptimizerError("尚未读取角色脑插，请重新登录该角色以授予 esi-clones.read_implants.v1 权限。")
    if isinstance(implants_data, dict) and implants_data.get("error"):
        raise OptimizerError(str(implants_data["error"]))
    fallback_rate, _ = fitting.training_rate(character)
    nodes, by_key = _build_nodes(targets, character)
    start = now or datetime.now(timezone.utc)
    if not nodes:
        return {
            "total_hours": 0.0,
            "finish_at": start.isoformat().replace("+00:00", "Z"),
            "baseline_hours": 0.0,
            "baseline_finish_at": start.isoformat().replace("+00:00", "Z"),
            "fastest_hours": 0.0,
            "time_regret_hours": 0.0,
            "saved_hours": 0.0,
            "optimization_policy": {"max_time_regret_hours": 24.0},
            "remaps": {"bonus_used": 0, "normal_used": 0},
            "phases": [],
            "nodes": [],
            "message": "当前方案已经满足，不需要训练。",
        }

    profiles = _candidate_profiles(nodes, character, fallback_rate)
    durations = [
        [_duration_hours(node, profile["effective"], fallback_rate) for profile in profiles]
        for node in nodes
    ]
    key_to_idx = {(node["skill_id"], node["level"]): i for i, node in enumerate(nodes)}
    dep_masks = []
    for node in nodes:
        mask = 0
        for dep in node.get("dep_keys", []):
            idx = key_to_idx.get(dep)
            if idx is not None:
                mask |= 1 << idx
        dep_masks.append(mask)

    current_base = _base_attributes(character)
    bonuses = fitting._implant_bonus_map(character)
    current_effective = _effective_attributes(current_base, bonuses)
    current_key = tuple(round(current_effective[name], 6) for name in ATTRIBUTE_ORDER)
    current_idx = next((i for i, p in enumerate(profiles) if tuple(round(p["effective"][n], 6) for n in ATTRIBUTE_ORDER) == current_key), 0)
    remap = fitting.remap_info(_attr_map(character), now=start)
    bonus_left = int(remap.get("bonus_remaps") or 0)
    normal_ready = start.timestamp() if remap.get("normal_available") else (_parse_ts(remap.get("next_normal_remap")) or start.timestamp())
    min_duration = [min(row) for row in durations]
    try:
        regret_hours = float(max_time_regret_hours)
    except (TypeError, ValueError):
        regret_hours = 24.0
    if regret_hours not in (24.0, 72.0, 168.0):
        regret_hours = 24.0
    all_mask = (1 << len(nodes)) - 1
    start_state = {
        "mask": 0,
        "profile": current_idx,
        "bonus_left": bonus_left,
        "normal_ready": normal_ready,
        "normal_used": 0,
        "elapsed": 0.0,
        "schedule": [],
    }
    max_steps = len(nodes) + bonus_left + MAX_NORMAL_REMAPS + 8

    def lower_bound(mask):
        value = 0.0
        remaining = all_mask & ~mask
        while remaining:
            bit = remaining & -remaining
            value += min_duration[bit.bit_length() - 1]
            remaining ^= bit
        return value

    def policy_score(state):
        return _state_policy_score(state, bonus_left)

    def trim_states(values, prefer_resets):
        by_time = sorted(
            values,
            key=lambda s: (s["_heuristic"], -s["bonus_left"], s["elapsed"]),
        )
        if not prefer_resets:
            return by_time[:beam_width]
        by_resets = sorted(
            values,
            key=lambda s: (*policy_score(s), s["_heuristic"]),
        )
        selected = []
        seen = set()
        limit = max(len(by_time), len(by_resets))
        for idx in range(limit):
            for source in (by_time, by_resets):
                if idx >= len(source):
                    continue
                state = source[idx]
                marker = id(state)
                if marker in seen:
                    continue
                seen.add(marker)
                selected.append(state)
                if len(selected) >= beam_width:
                    return selected
        return selected

    def run_search(time_limit=None, prefer_resets=False):
        states = [dict(start_state)]
        best_state = None
        best_score = None
        fixed_limit = time_limit if prefer_resets else None
        for _ in range(max_steps):
            if not states:
                break
            expanded = []
            for state in states:
                remaining = all_mask & ~state["mask"]
                available = [
                    i for i in range(len(nodes))
                    if (remaining & (1 << i)) and not (dep_masks[i] & ~state["mask"])
                ]
                for idx in available:
                    new_state = dict(state)
                    new_state["mask"] = state["mask"] | (1 << idx)
                    new_state["elapsed"] = state["elapsed"] + durations[idx][state["profile"]]
                    new_state["schedule"] = state["schedule"] + [{
                        "type": "train",
                        "node": idx,
                        "hours": durations[idx][state["profile"]],
                    }]
                    new_state["_heuristic"] = new_state["elapsed"] + lower_bound(new_state["mask"])
                    if (
                        prefer_resets
                        and fixed_limit is not None
                        and new_state["elapsed"] > fixed_limit + 1e-7
                    ):
                        continue
                    if new_state["mask"] == all_mask:
                        score = policy_score(new_state) if prefer_resets else (new_state["elapsed"],)
                        if best_score is None or score < best_score:
                            best_score = score
                            best_state = new_state
                        continue
                    ceiling = fixed_limit
                    if ceiling is None and best_state is not None:
                        ceiling = best_state["elapsed"]
                    if ceiling is None or new_state["_heuristic"] <= ceiling + 1e-7:
                        expanded.append(new_state)

                if state["mask"] == all_mask:
                    continue
                remaining_nodes = [i for i in range(len(nodes)) if remaining & (1 << i)]
                useful_profiles = []
                for pid, profile in enumerate(profiles):
                    if pid == state["profile"]:
                        continue
                    gain = sum(
                        durations[i][state["profile"]] - durations[i][pid]
                        for i in remaining_nodes
                    )
                    if gain > 1e-9:
                        useful_profiles.append((gain, pid))
                useful_profiles.sort(reverse=True)
                for gain, pid in useful_profiles[:16]:
                    candidates = []
                    if state["bonus_left"] > 0:
                        bonus_state = dict(state)
                        bonus_state["profile"] = pid
                        bonus_state["bonus_left"] = state["bonus_left"] - 1
                        bonus_state["schedule"] = state["schedule"] + [{
                            "type": "remap", "source": "bonus", "profile": pid, "wait_hours": 0.0,
                        }]
                        bonus_state["_heuristic"] = bonus_state["elapsed"] + lower_bound(bonus_state["mask"])
                        candidates.append(bonus_state)
                    if state["normal_used"] < MAX_NORMAL_REMAPS:
                        now_ts = start.timestamp() + state["elapsed"] * 3600.0
                        current_ready = max(state["normal_ready"], now_ts)
                        wait_hours = max(0.0, (current_ready - now_ts) / 3600.0)
                        normal_state = dict(state)
                        normal_state["profile"] = pid
                        normal_state["elapsed"] = state["elapsed"] + wait_hours
                        normal_state["normal_ready"] = current_ready + 365 * 86400.0
                        normal_state["normal_used"] = state["normal_used"] + 1
                        normal_state["schedule"] = state["schedule"] + [{
                            "type": "remap", "source": "normal", "profile": pid, "wait_hours": wait_hours,
                        }]
                        normal_state["_heuristic"] = normal_state["elapsed"] + lower_bound(normal_state["mask"])
                        candidates.append(normal_state)
                    for candidate in candidates:
                        ceiling = fixed_limit
                        if ceiling is None and best_state is not None:
                            ceiling = best_state["elapsed"]
                        if ceiling is None or candidate["_heuristic"] <= ceiling + 1e-7:
                            expanded.append(candidate)

            dedup = {}
            for state in expanded:
                key = (
                    state["mask"], state["profile"], state["bonus_left"],
                    state["normal_used"], round(state["normal_ready"], 3),
                )
                old = dedup.get(key)
                if old is None or state["elapsed"] < old["elapsed"]:
                    dedup[key] = state
            states = trim_states(list(dedup.values()), prefer_resets)
        return best_state

    fastest_state = run_search(prefer_resets=False)
    if fastest_state is None:
        # Fall back to a valid topological schedule under current attributes.
        mask = 0
        elapsed = 0.0
        schedule = []
        while mask != all_mask:
            available = [i for i in range(len(nodes)) if not (mask & (1 << i)) and not (dep_masks[i] & ~mask)]
            if not available:
                raise OptimizerError("技能依赖关系中存在无法训练的前置循环。")
            idx = min(available)
            elapsed += durations[idx][current_idx]
            schedule.append({"type": "train", "node": idx, "hours": durations[idx][current_idx]})
            mask |= 1 << idx
        fastest_state = {
            "mask": all_mask,
            "profile": current_idx,
            "bonus_left": bonus_left,
            "normal_used": 0,
            "normal_ready": normal_ready,
            "elapsed": elapsed,
            "schedule": schedule,
        }

    fastest_elapsed = fastest_state["elapsed"]
    preferred_state = run_search(
        time_limit=fastest_elapsed + regret_hours,
        prefer_resets=True,
    )
    best_state = preferred_state or fastest_state

    # Reconstruct phased result.
    phases = []
    phase_no = 1
    phase_elapsed = 0.0
    current_profile = current_idx
    current_nodes = []
    phase_start = 0.0
    phase_reset = {"type": "start", "wait_hours": 0.0}
    used_bonus = 0
    used_normal = 0

    def close_phase():
        nonlocal phase_no, current_nodes, phase_start, phase_reset
        if current_nodes:
            phases.append({
                "phase": phase_no,
                "profile": _profile_payload(profiles[current_profile]),
                "reset": phase_reset,
                "start_at": _iso_at(start, phase_start),
                "end_at": _iso_at(start, phase_elapsed),
                "nodes": current_nodes,
            })
            phase_no += 1
        current_nodes = []
        phase_start = phase_elapsed

    for event in best_state["schedule"]:
        if event["type"] == "remap":
            phase_elapsed += event.get("wait_hours", 0.0)
            close_phase()
            current_profile = event["profile"]
            phase_reset = {"type": event["source"], "wait_hours": event.get("wait_hours", 0.0)}
            if event["source"] == "bonus":
                used_bonus += 1
            else:
                used_normal += 1
            continue
        node = nodes[event["node"]]
        node_hours = event["hours"]
        node_start = phase_elapsed
        phase_elapsed += node_hours
        current_nodes.append({
            "skill_id": node["skill_id"],
            "name": node["name"],
            "name_en": node["name_en"],
            "level": node["level"],
            "sp": node["sp"],
            "rate_sp_hour": round(node["sp"] / node_hours, 2) if node_hours > 0 else 0,
            "hours": round(node_hours, 4),
            "start_at": _iso_at(start, node_start),
            "finish_at": _iso_at(start, phase_elapsed),
        })
    close_phase()

    baseline_hours = sum(durations[i][current_idx] for i in range(len(nodes)))
    return {
        "total_hours": round(best_state["elapsed"], 4),
        "finish_at": _iso_at(start, best_state["elapsed"]),
        "baseline_hours": round(baseline_hours, 4),
        "baseline_finish_at": _iso_at(start, baseline_hours),
        "fastest_hours": round(fastest_elapsed, 4),
        "time_regret_hours": round(best_state["elapsed"] - fastest_elapsed, 4),
        "saved_hours": round(baseline_hours - best_state["elapsed"], 4),
        "optimization_policy": {"max_time_regret_hours": regret_hours},
        "remaps": {"bonus_used": used_bonus, "normal_used": used_normal},
        "phases": phases,
        "nodes": [n for phase in phases for n in phase["nodes"]],
        "message": "",
    }


def _parse_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _queue_entries(character: dict | None):
    """解析角色训练队列，返回带方案匹配键和原始序号的条目。"""
    if not character:
        return None
    raw = character.get("queue_json")
    if isinstance(raw, list):
        queue = raw
    else:
        try:
            queue = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return None
    if not isinstance(queue, list):
        return None
    entries = []
    for idx, entry in enumerate(queue):
        if not isinstance(entry, dict):
            return None
        try:
            skill_id = int(entry.get("skill_id") or 0)
            level = int(entry.get("target_level") or 0)
        except (TypeError, ValueError):
            return None
        if not skill_id or not level:
            continue
        try:
            position = int(entry.get("position", idx))
        except (TypeError, ValueError):
            position = idx
        entries.append({
            "entry": entry,
            "key": (skill_id, level),
            "position": position,
        })
    return entries


def analyze_queue_match(character: dict | None, plan: dict | None) -> dict:
    """按当前技能和训练队列计算方案的应用状态。

    已完成节点不计入必需队列；方案外和重复队列条目会阻止“已应用”，
    匹配到的方案节点之间若顺序反转则单独返回乱序状态。
    """
    if (
        not character
        or not isinstance(plan, dict)
        or not plan
        or ("nodes" not in plan and "phases" not in plan)
    ):
        return {
            "status": "unknown", "matched_count": 0, "required_count": 0,
            "missing_count": 0, "extra_count": 0, "completed_count": 0,
            "queue_count": 0, "order_ok": None, "reason": "角色或方案不存在",
        }
    queue_entries = _queue_entries(character)
    if queue_entries is None:
        return {
            "status": "unknown", "matched_count": 0, "required_count": 0,
            "missing_count": 0, "extra_count": 0, "completed_count": 0,
            "queue_count": 0, "order_ok": None, "reason": "训练队列格式无效",
        }

    cmap = fitting._char_skill_map(character)
    required = []
    completed_count = 0
    seen = set()
    for node in plan.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        try:
            skill_id = int(node.get("skill_id") or 0)
            level = int(node.get("level") or 0)
        except (TypeError, ValueError):
            continue
        key = (skill_id, level)
        if not skill_id or not level or key in seen:
            continue
        seen.add(key)
        current = cmap.get(skill_id) or {}
        try:
            active_level = int(current.get("active_skill_level") or 0)
        except (TypeError, ValueError):
            active_level = 0
        if active_level >= level:
            completed_count += 1
        else:
            required.append(key)

    positions_by_key = {}
    for item in queue_entries:
        positions_by_key.setdefault(item["key"], []).append(item["position"])
    for positions in positions_by_key.values():
        positions.sort()

    matched_positions = {}
    for key in required:
        positions = positions_by_key.get(key)
        if positions:
            matched_positions[key] = positions[0]
    matched_count = len(matched_positions)
    required_count = len(required)
    missing_count = required_count - matched_count
    extra_count = max(0, len(queue_entries) - matched_count)

    pair_indices = {key: idx for idx, key in enumerate(required)}
    order_ok = True
    matched = list(matched_positions)
    for i, left in enumerate(matched):
        for right in matched[i + 1:]:
            if pair_indices[left] < pair_indices[right] and matched_positions[left] > matched_positions[right]:
                order_ok = False
            if pair_indices[left] > pair_indices[right] and matched_positions[left] < matched_positions[right]:
                order_ok = False

    if required_count == 0:
        status = "applied"
    elif matched_count == 0:
        status = "not_applied"
    elif not order_ok:
        status = "out_of_order"
    elif missing_count > 0 or extra_count > 0:
        status = "partial"
    else:
        status = "applied"

    return {
        "status": status,
        "matched_count": matched_count,
        "required_count": required_count,
        "missing_count": missing_count,
        "extra_count": extra_count,
        "completed_count": completed_count,
        "queue_count": len(queue_entries),
        "order_ok": order_ok,
        "reason": "",
    }


def _rate_from_effective(skill_attrs: dict, effective: dict | None, fallback: float = 0.0) -> float:
    """按指定阶段的五项有效属性计算技能训练速度。"""
    if not isinstance(effective, dict) or not effective:
        return float(fallback or fitting.DEFAULT_TRAINING_SP_HOUR)
    primary_key = fitting.CHARACTER_ATTRIBUTE_KEYS.get(int(skill_attrs.get(180) or 0))
    secondary_key = fitting.CHARACTER_ATTRIBUTE_KEYS.get(int(skill_attrs.get(181) or 0))
    try:
        primary = float(effective.get(primary_key) or 0) if primary_key else 0.0
        secondary = float(effective.get(secondary_key) or 0) if secondary_key else 0.0
    except (TypeError, ValueError):
        primary = secondary = 0.0
    if primary > 0 and secondary > 0:
        return 60.0 * (primary + secondary / 2.0)
    return float(fallback or fitting.DEFAULT_TRAINING_SP_HOUR)


def _queue_remaining_sp(entry: dict, now: datetime, fallback_sp: int) -> int:
    """估算队列技能从当前时刻起仍需要训练的技能点。"""
    try:
        start_sp = int(entry.get("start_sp"))
        end_sp = int(entry.get("end_sp"))
    except (TypeError, ValueError):
        return max(0, int(fallback_sp or 0))
    total_sp = max(0, end_sp - start_sp)
    q_start = _parse_datetime(entry.get("training_start_date"))
    q_end = _parse_datetime(entry.get("finish_date"))
    if q_start and q_end and q_end > q_start and total_sp > 0:
        if now <= q_start:
            return total_sp
        if now >= q_end:
            return 0
        progress = (now - q_start).total_seconds() / (q_end - q_start).total_seconds()
        return max(0, int(round(total_sp * (1.0 - progress))))
    return total_sp or max(0, int(fallback_sp or 0))


def rebase_plan_with_queue(character: dict, plan: dict, now: datetime | None = None) -> dict:
    """根据角色当前技能和游戏队列，重算已保存训练方案的时间节点。"""
    if not plan or not isinstance(plan, dict):
        raise OptimizerError("优化方案不存在或格式无效。")
    start = now or datetime.now(timezone.utc)
    cmap = fitting._char_skill_map(character)
    queue_entries = _queue_entries(character) or []
    queue_by_key = {}
    queue_positions = {}
    queue_end = start
    for idx, item in enumerate(queue_entries):
        entry = item["entry"]
        key = item["key"]
        queue_by_key[key] = entry
        queue_positions[key] = item["position"]
        actual_end = _parse_datetime(entry.get("finish_date"))
        if actual_end and actual_end > queue_end:
            queue_end = actual_end
    plan_order = {}
    for idx, original in enumerate(plan.get("nodes") or []):
        key = (int(original.get("skill_id") or 0), int(original.get("level") or 1))
        if key[0] and key not in plan_order:
            plan_order[key] = idx
    queued_plan_keys = [key for key in plan_order if key in queue_by_key]
    queued_sorted = sorted(queued_plan_keys, key=lambda key: queue_positions.get(key, 0))
    expected_order = {key: idx + 1 for idx, key in enumerate(queued_plan_keys)}
    actual_order = {key: idx + 1 for idx, key in enumerate(queued_sorted)}
    order_ok = {}
    for key in queued_plan_keys:
        pos = queue_positions.get(key, 0)
        ok = True
        for other in queued_plan_keys:
            if other == key:
                continue
            other_pos = queue_positions.get(other, 0)
            if plan_order.get(other, 0) < plan_order.get(key, 0) and other_pos > pos:
                ok = False
            if plan_order.get(other, 0) > plan_order.get(key, 0) and other_pos < pos:
                ok = False
        order_ok[key] = ok
    node_ids = [int(n.get("skill_id") or 0) for n in (plan.get("nodes") or []) if n.get("skill_id")]
    skill_attrs = name_index.get_type_attrs(node_ids)
    phase_attrs_by_key = {}
    for phase in plan.get("phases") or []:
        effective = (phase.get("profile") or {}).get("effective") or {}
        if not isinstance(effective, dict) or not effective:
            continue
        for node in phase.get("nodes") or []:
            try:
                phase_key = (int(node.get("skill_id") or 0), int(node.get("level") or 1))
            except (TypeError, ValueError):
                continue
            if phase_key[0]:
                phase_attrs_by_key[phase_key] = effective
    completed_by_key = {}
    for completed in plan.get("completed_nodes") or []:
        if not isinstance(completed, dict):
            continue
        try:
            completed_key = (int(completed.get("skill_id") or 0), int(completed.get("level") or 1))
        except (TypeError, ValueError):
            continue
        if completed_key[0]:
            completed_by_key[completed_key] = dict(completed)

    updated = {}
    cursor = start
    baseline_hours = 0.0
    for original in plan.get("nodes") or []:
        sid = int(original.get("skill_id") or 0)
        level = int(original.get("level") or 1)
        if not sid:
            continue
        key = (sid, level)
        current = cmap.get(sid) or {}
        active = int(current.get("active_skill_level") or 0)
        if active >= level:
            if key not in completed_by_key:
                item = dict(original)
                item["status"] = "completed"
                item["completed_at"] = start.isoformat().replace("+00:00", "Z")
                item["planned_finish_at"] = (
                    original.get("queue_finish_at") or original.get("finish_at")
                )
                completed_by_key[key] = item
            continue
        attrs = skill_attrs.get(sid, {})
        rank = float(attrs.get(275) or original.get("rank") or 1.0)
        required_sp = fitting.cumulative_sp(rank, level)
        current_sp = int(current.get("skillpoints_in_skill") or 0)
        level_start = fitting.cumulative_sp(rank, level - 1) if level > 1 else 0
        remaining_sp = max(0, required_sp - max(current_sp, level_start))
        fallback_rate, _ = fitting.skill_training_rate(attrs, character)
        current_rate = fallback_rate or fitting.DEFAULT_TRAINING_SP_HOUR
        phase_attrs = phase_attrs_by_key.get(key)
        profile_rate = _rate_from_effective(attrs, phase_attrs, current_rate)

        item = dict(original)
        queue_item = queue_by_key.get(key)
        queue_hours = 0.0
        queue_rate = 0.0
        if queue_item:
            q_start = _parse_datetime(queue_item.get("training_start_date"))
            q_end = _parse_datetime(queue_item.get("finish_date"))
            if q_start and q_end and q_end > q_start:
                queue_hours = (q_end - q_start).total_seconds() / 3600.0
                try:
                    queue_sp = int(queue_item.get("end_sp")) - int(queue_item.get("start_sp"))
                except (TypeError, ValueError):
                    queue_sp = 0
                if queue_hours > 0 and queue_sp > 0:
                    queue_rate = queue_sp / queue_hours
            item["sp"] = _queue_remaining_sp(queue_item, start, remaining_sp)
            item["status"] = "queued"
            item["order_ok"] = order_ok.get(key)
            item["plan_order"] = expected_order.get(key)
            item["queue_order"] = actual_order.get(key)
            if q_start:
                item["queue_start_at"] = q_start.isoformat().replace("+00:00", "Z")
            if q_end:
                item["queue_finish_at"] = q_end.isoformat().replace("+00:00", "Z")
            if queue_hours > 0:
                item["queue_hours"] = round(queue_hours, 4)
            if queue_rate > 0:
                item["queue_rate_sp_hour"] = round(queue_rate, 2)
        else:
            item["sp"] = remaining_sp
            item["status"] = "planned"

        baseline_hours += item["sp"] / current_rate if current_rate > 0 else 0.0
        rate = profile_rate if phase_attrs else current_rate
        if rate <= 0:
            rate = fitting.DEFAULT_TRAINING_SP_HOUR
        item["rate_sp_hour"] = round(rate, 2)

        # 有阶段属性的方案始终按计划速度推进；只有旧方案缺属性时才沿用队列耗时。
        if queue_item and not phase_attrs and queue_hours > 0 and q_end:
            item["hours"] = round(queue_hours, 4)
            item["start_at"] = (q_start or cursor).isoformat().replace("+00:00", "Z")
            item["finish_at"] = q_end.isoformat().replace("+00:00", "Z")
            if queue_rate > 0:
                item["rate_sp_hour"] = round(queue_rate, 2)
            cursor = max(cursor, q_end)
        else:
            hours = item["sp"] / rate if rate > 0 else 0.0
            item["hours"] = round(hours, 4)
            item["start_at"] = cursor.isoformat().replace("+00:00", "Z")
            cursor += timedelta(hours=hours)
            item["finish_at"] = cursor.isoformat().replace("+00:00", "Z")
        updated[key] = item
    new_phases = []
    for phase in plan.get("phases") or []:
        nodes = []
        for node in phase.get("nodes") or []:
            key = (int(node.get("skill_id") or 0), int(node.get("level") or 1))
            if key in updated:
                nodes.append(updated[key])
        if not nodes:
            continue
        new_phase = dict(phase)
        new_phase["nodes"] = nodes
        new_phase["start_at"] = nodes[0].get("start_at")
        new_phase["end_at"] = nodes[-1].get("finish_at")
        new_phases.append(new_phase)
    if not new_phases and updated:
        new_phases = [{
            "phase": 1,
            "profile": plan.get("profile") or {},
            "reset": {"type": "start", "wait_hours": 0},
            "start_at": min(n.get("start_at") for n in updated.values()),
            "end_at": max(n.get("finish_at") for n in updated.values()),
            "nodes": list(updated.values()),
        }]
    flat = [node for phase in new_phases for node in phase.get("nodes", [])]
    not_queued_count = sum(1 for node in flat if node.get("status") == "planned")
    finish_at = cursor.isoformat().replace("+00:00", "Z") if flat else start.isoformat().replace("+00:00", "Z")
    baseline_finish = start + timedelta(hours=baseline_hours)
    total_hours = max(0.0, (cursor - start).total_seconds() / 3600.0)
    try:
        previous_regret = max(0.0, float(plan.get("time_regret_hours") or 0.0))
    except (TypeError, ValueError):
        previous_regret = 0.0
    fastest_hours = max(0.0, total_hours - previous_regret)
    completed_nodes = sorted(
        completed_by_key.values(),
        key=lambda node: (str(node.get("completed_at") or ""), str(node.get("name") or "")),
    )
    result = dict(plan)
    result.update({
        "phases": new_phases,
        "nodes": flat,
        "completed_nodes": completed_nodes,
        "finish_at": finish_at,
        "baseline_hours": round(baseline_hours, 4),
        "baseline_finish_at": baseline_finish.isoformat().replace("+00:00", "Z"),
        "fastest_hours": round(fastest_hours, 4),
        "time_regret_hours": round(max(0.0, total_hours - fastest_hours), 4),
        "total_hours": round(total_hours, 4),
        "saved_hours": round(baseline_hours - total_hours, 4),
        "queue_end_at": queue_end.isoformat().replace("+00:00", "Z"),
        "queue_count": len(queue_by_key),
        "queue_match": analyze_queue_match(character, plan),
        "not_queued_count": not_queued_count,
        "queue_order_ok": all(order_ok.values()) if order_ok else None,
        "rebase_performed": True,
    })
    return result


def _iso_at(start: datetime, hours: float) -> str:
    return (start + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _profile_payload(profile: dict) -> dict:
    base = profile["base"]
    effective = profile["effective"]
    return {
        "base": {name: int(round(base.get(name, 0))) for name in ATTRIBUTE_ORDER},
        "effective": {name: round(float(effective.get(name, 0)), 2) for name in ATTRIBUTE_ORDER},
        "labels": {name: ATTRIBUTE_LABELS[name] for name in ATTRIBUTE_ORDER},
    }
