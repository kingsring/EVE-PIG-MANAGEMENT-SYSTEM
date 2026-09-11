"""本地物品名称索引（中文/英文），用于模糊搜索。

数据来源：CCP 静态数据（经 Fuzzwork 镜像提供的 CSV）：
- invTypes.csv：typeID / 英文名 / published
- trnTranslations.csv：各语言翻译（含中文 zh），列 tcID=8 对应 invTypes.typeName

首次需执行：python -m app.name_index --build  （下载约 170MB 数据并建库，一次性）
之后搜索完全在本地 SQLite 完成。
"""
import argparse
import csv
import io
import sqlite3
from functools import lru_cache
import time
from pathlib import Path

import httpx

from . import config

INDEX_DB = config.DATA_DIR / "item_index.db"
SOURCE_BASE = "https://www.fuzzwork.co.uk/dump/latest/csv/"
TYPENAME_TCID = "8"
EFF_MOD_COL = 27  # dgmEffects.csv 中 modifierInfo 的列序号

# 用于配装/技能需求计算的 dogma 属性
ATTRIBUTE_IDS = {
    11, 12, 13, 14,        # powerOutput / lowSlots / medSlots / hiSlots
    30, 48, 49, 50,        # power / cpuOutput / cpuLoad / cpu
    47, 101, 102,          # slots / launcherSlotsLeft / turretSlotsLeft
    283, 1271, 1272, 352,  # 无人机舱容/带宽/占用/最大数量
    275,                   # skillTimeConstant(技能等级SP系数)
    182, 183, 184, 1285, 1289, 1290,          # requiredSkill1..6
    277, 278, 279, 1286, 1287, 1288,          # requiredSkillNLevel
    1132, 1137, 1153, 1547,                    # 校准/改装槽/改装成本/改装尺寸
    1374, 1375, 1376,                          # hi/med/low slot modifier
    128, 137, 604, 605, 606, 609, 610,         # 弹药尺寸/兼容组
    424, 425, 288, 1377,                       # CPU 输出加成(百分比/固定值)
    121, 334, 313, 1378,                       # 电力(PG)输出加成
    310, 323,                                  # 模块 CPU/PG 需求增减
    202,                                       # cpuMultiplier(如协处理器,乘算)
    145, 608,                                  # powerOutputMultiplier / powerNeedMultiplier
}
ZH_LANG = "zh"


def _connect():
    conn = sqlite3.connect(str(INDEX_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _memoize_type_ids(fn):
    """Cache static index lookups keyed by DB path and type-id tuple."""
    @lru_cache(maxsize=8192)
    def cached(db_path, ids):
        return fn(ids)

    def wrapper(type_ids):
        ids = tuple(sorted({int(i) for i in type_ids if i is not None}))
        if not ids:
            return {}
        return cached(str(INDEX_DB), ids)

    wrapper.cache_clear = cached.cache_clear
    return wrapper


def init_index():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                type_id       INTEGER PRIMARY KEY,
                name_en       TEXT NOT NULL,
                name_zh       TEXT,
                name_en_lower TEXT NOT NULL,
                name_zh_lower TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_en ON items(name_en_lower)")
        cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "volume" not in cols2:
            conn.execute("ALTER TABLE items ADD COLUMN volume REAL")
        if "mass" not in cols2:
            conn.execute("ALTER TABLE items ADD COLUMN mass REAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS type_attrs (
                type_id      INTEGER NOT NULL,
                attribute_id INTEGER NOT NULL,
                value        REAL,
                PRIMARY KEY (type_id, attribute_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type_attrs_attr ON type_attrs(attribute_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS effects (
                effect_id     INTEGER PRIMARY KEY,
                modifier_info TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS type_slots (
                type_id INTEGER NOT NULL,
                slot    TEXT NOT NULL,
                PRIMARY KEY (type_id, slot)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS type_effects (
                type_id   INTEGER NOT NULL,
                effect_id INTEGER NOT NULL,
                PRIMARY KEY (type_id, effect_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_items_zh ON items(name_zh_lower)")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "group_id" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN group_id INTEGER")
        if "category_id" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN category_id INTEGER")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cat_names (
                category_id INTEGER PRIMARY KEY,
                name_zh     TEXT NOT NULL,
                updated_at  TEXT
            )
            """
        )


        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS group_names (
                group_id   INTEGER PRIMARY KEY,
                name_zh    TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )

def index_count() -> int:
    if not INDEX_DB.exists():
        return 0
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()
    return row["c"]


def build_effects() -> None:
    """导入槽位相关效果(loPower/hiPower/medPower/rigSlot),用于槽位判断。"""
    init_index()
    tmp = config.DATA_DIR / "_build"
    tmp.mkdir(parents=True, exist_ok=True)
    eff_file = tmp / "dgmEffects.csv"
    te_file = tmp / "dgmTypeEffects.csv"
    _download(SOURCE_BASE + "dgmEffects.csv", eff_file, "dgmEffects(效果名)")
    _download(SOURCE_BASE + "dgmTypeEffects.csv", te_file, "dgmTypeEffects(物品效果)")

    want_names = {"lopower", "hipower", "medpower", "rigslot"}
    want_ids = set()
    with io.open(eff_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 2:
                continue
            name = row[1].strip().strip('"').lower()
            if name in want_names:
                try:
                    want_ids.add(int(row[0].strip().strip('"')))
                except ValueError:
                    pass
    if not want_ids:
        print("未找到槽位效果定义", flush=True)
        return

    # 全部效果(含 modifierInfo,用于技能对模块需求的影响)
    eff_batch = []
    with io.open(eff_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) <= EFF_MOD_COL:
                continue
            try:
                eid = int(row[0].strip().strip('"'))
            except ValueError:
                continue
            eff_batch.append((eid, row[EFF_MOD_COL]))
            if len(eff_batch) >= 3000:
                with _connect() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO effects (effect_id, modifier_info) VALUES (?, ?)", eff_batch
                    )
                eff_batch = []
    if eff_batch:
        with _connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO effects (effect_id, modifier_info) VALUES (?, ?)", eff_batch
            )

    conn = _connect()
    known = {r["type_id"] for r in conn.execute("SELECT type_id FROM items").fetchall()}
    skill_ids = {r["type_id"] for r in conn.execute("SELECT type_id FROM items WHERE category_id = 16").fetchall()}
    batch = []
    with io.open(te_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 2:
                continue
            try:
                tid = int(row[0].strip().strip('"')); eid = int(row[1].strip().strip('"'))
            except ValueError:
                continue
            if tid not in known:
                continue
            batch.append((tid, eid))  # 导入全部物品效果(供配装属性计算)
            if len(batch) >= 3000:
                with _connect() as c2:
                    c2.executemany(
                        "INSERT OR REPLACE INTO type_effects (type_id, effect_id) VALUES (?, ?)", batch
                    )
                batch = []
    if batch:
        with _connect() as c2:
            c2.executemany(
                "INSERT OR REPLACE INTO type_effects (type_id, effect_id) VALUES (?, ?)", batch
            )
    try:
        eff_file.unlink(); te_file.unlink(); tmp.rmdir()
    except OSError:
        pass
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM type_effects").fetchone()["c"]
    print(f"效果导入完成: {n} 条(效果ID {sorted(want_ids)})", flush=True)
    rebuild_type_slots()


@_memoize_type_ids
def get_effect_modifier_info(effect_ids) -> dict:
    """读取效果表的 modifierInfo(JSON 字符串)。"""
    ids = list(effect_ids)
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT effect_id, modifier_info FROM effects WHERE effect_id IN ({marks})", ids
        ).fetchall()
    return {r["effect_id"]: r["modifier_info"] for r in rows}

@_memoize_type_ids
def get_type_effects(type_ids) -> dict:
    ids = list(type_ids)
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT type_id, effect_id FROM type_effects WHERE type_id IN ({marks})", ids
        ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["type_id"], set()).add(r["effect_id"])
    return out

def rebuild_type_slots() -> None:
    """根据槽位属性与效果重建 type_slots 表(供槽位点选装备过滤)。"""
    init_index()
    with _connect() as conn:
        conn.execute("DELETE FROM type_slots")
        for aid, slot in ((1374, "hi"), (1375, "med"), (1376, "low")):
            conn.execute(
                "INSERT OR IGNORE INTO type_slots (type_id, slot) SELECT type_id, ? FROM type_attrs WHERE attribute_id = ? AND value > 0",
                (slot, aid),
            )
        for eid, slot in ((12, "hi"), (13, "med"), (11, "low"), (2663, "rig")):
            conn.execute(
                "INSERT OR IGNORE INTO type_slots (type_id, slot) SELECT type_id, ? FROM type_effects WHERE effect_id = ?",
                (slot, eid),
            )
        n = conn.execute("SELECT COUNT(*) AS c FROM type_slots").fetchone()["c"]
    print(f"槽位索引完成: {n} 条", flush=True)


def search_by_slot(slot: str, q: str = "", limit: int = 50) -> list:
    """按槽位类型搜索可装配物品(用于槽位点选)。"""
    limit = max(1, min(limit, 100))
    q = (q or "").strip().lower()
    params = [slot]
    where = "s.slot = ?"
    if q:
        like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (i.name_en_lower LIKE ? ESCAPE '\\' OR i.name_zh_lower LIKE ? ESCAPE '\\')"
        params += [like, like]
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT i.type_id, i.name_en, i.name_zh, i.group_id, i.category_id
                FROM type_slots s JOIN items i ON i.type_id = s.type_id
                WHERE {where} AND i.category_id != 9
                ORDER BY (i.name_en_lower LIKE ? ESCAPE '\\') DESC, length(COALESCE(i.name_zh, i.name_en)) ASC
                LIMIT ?""",
            params + [((q + "%") if q else "%"), limit],
        ).fetchall()
    return [
        {"type_id": r["type_id"], "name": r["name_zh"] or r["name_en"] or str(r["type_id"])}
        for r in rows
    ]

def search_category(category_id: int, q: str = "", limit: int = 50, group_id: int | None = None) -> list:
    """按物品分类搜索(用于无人机、技能等无槽位物品)。"""
    limit = max(1, min(limit, 100))
    q = (q or "").strip().lower()
    params = [int(category_id)]
    where = "i.category_id = ?"
    if group_id is not None:
        where += " AND i.group_id = ?"
        params.append(int(group_id))
    if q:
        like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (i.name_en_lower LIKE ? ESCAPE '\\' OR i.name_zh_lower LIKE ? ESCAPE '\\')"
        params += [like, like]
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT i.type_id, i.name_en, i.name_zh FROM items i
                WHERE {where}
                ORDER BY (i.name_en_lower LIKE ? ESCAPE '\\') DESC, length(COALESCE(i.name_zh, i.name_en)) ASC
                LIMIT ?""",
            params + [((q + "%") if q else "%"), limit],
        ).fetchall()
    return [
        {"type_id": r["type_id"], "name": r["name_zh"] or r["name_en"] or str(r["type_id"])}
        for r in rows
    ]


def search_group_charges(group_ids, q: str = "", limit: int = 50) -> list:
    """按弹药组搜索可装填的弹药(category 8)。"""
    groups = [int(g) for g in group_ids if g]
    if not groups:
        return []
    limit = max(1, min(limit, 100))
    q = (q or "").strip().lower()
    marks = ",".join("?" * len(groups))
    params = groups[:]
    where = f"i.category_id = 8 AND i.group_id IN ({marks})"
    if q:
        like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (i.name_en_lower LIKE ? ESCAPE '\\' OR i.name_zh_lower LIKE ? ESCAPE '\\')"
        params += [like, like]
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT i.type_id, i.name_en, i.name_zh FROM items i WHERE {where} LIMIT ?",
            params + [limit],
        ).fetchall()
    return [
        {"type_id": r["type_id"], "name": r["name_zh"] or r["name_en"] or str(r["type_id"])}
        for r in rows
    ]

def category_counts() -> list:
    """各分类下的已发布物品数量，返回 [(category_id, count), ...] 按数量降序。"""
    if not INDEX_DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT category_id, COUNT(*) AS c FROM items
            WHERE category_id IS NOT NULL
            GROUP BY category_id ORDER BY c DESC
            """
        ).fetchall()
    return [(r["category_id"], r["c"]) for r in rows]


def items_by_category(category_id: int, q: str = "", limit: int = 100, offset: int = 0) -> list:
    """某分类下的物品列表（可选关键词过滤，前缀优先）。q 为空返回该分类前 limit 条。"""
    q = (q or "").strip()
    lq = q.lower()
    like = "%" + lq.replace("%", "\\%").replace("_", "\\_") + "%"
    params = [category_id]
    where = "category_id = ?"
    if lq:
        where += " AND (name_en_lower LIKE ? ESCAPE '\\' OR name_zh_lower LIKE ? ESCAPE '\\')"
        params += [like, like]
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT type_id, name_en, name_zh, name_en_lower, name_zh_lower
            FROM items WHERE {where}
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    out = []
    for r in rows:
        en_l = r["name_en_lower"]
        zh_l = r["name_zh_lower"] or ""
        if lq and not (lq in en_l or lq in zh_l):
            continue
        prefix = (en_l.startswith(lq) or zh_l.startswith(lq)) if lq else False
        display = r["name_zh"] or r["name_en"]
        out.append((0 if prefix else 1, len(display), r["type_id"], display))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return [{"type_id": t[2], "name": t[3]} for t in out[:limit]]


def category_names(ids) -> dict:
    """读取本地缓存的分类中文名。"""
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT category_id, name_zh FROM cat_names WHERE category_id IN ({marks})",
            ids,
        ).fetchall()
    return {r["category_id"]: r["name_zh"] for r in rows}


def save_category_names(mapping: dict):
    if not mapping:
        return
    items = [(int(k), str(v)) for k, v in mapping.items()]
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO cat_names (category_id, name_zh) VALUES (?, ?)",
            items,
        )


def skill_ids() -> list:
    """返回本地索引中的全部技能 ID。"""
    if not INDEX_DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute("SELECT type_id FROM items WHERE category_id = 16 ORDER BY type_id").fetchall()
    return [int(r["type_id"]) for r in rows]


def skill_groups() -> list:
    """技能大类及其技能数量(用于追加技能的两级选择)。"""
    if not INDEX_DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute(
            """SELECT i.group_id, COUNT(*) AS count,
                      COALESCE(g.name_zh, '技能组 ' || i.group_id) AS name
               FROM items i LEFT JOIN group_names g ON g.group_id = i.group_id
               WHERE i.category_id = 16 AND i.group_id IS NOT NULL
               GROUP BY i.group_id
               ORDER BY name"""
        ).fetchall()
    return [{"group_id": r["group_id"], "name": r["name"], "count": r["count"]} for r in rows]


def group_names(ids) -> dict:
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT group_id, name_zh FROM group_names WHERE group_id IN ({marks})", ids
        ).fetchall()
    return {r["group_id"]: r["name_zh"] for r in rows}


def save_group_names(mapping: dict):
    if not mapping:
        return
    items = [(int(k), str(v)) for k, v in mapping.items()]
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO group_names (group_id, name_zh) VALUES (?, ?)",
            items,
        )

def build_attributes() -> None:
    """导入配装所需 dogma 属性与物品体积(增量,不重下翻译表)。"""
    init_index()
    tmp = config.DATA_DIR / "_build"
    tmp.mkdir(parents=True, exist_ok=True)
    inv_file = tmp / "invTypes2.csv"
    grp_file = tmp / "invGroups2.csv"
    attr_file = tmp / "dgmTypeAttributes.csv"

    _download(SOURCE_BASE + "invTypes.csv", inv_file, "invTypes(体积/分组)")
    _download(SOURCE_BASE + "invGroups.csv", grp_file, "invGroups(组→类目)")
    _download(SOURCE_BASE + "dgmTypeAttributes.csv", attr_file, "dgmTypeAttributes(属性)")

    grp_cat = {}
    with io.open(grp_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 2:
                continue
            try:
                grp_cat[int(row[0])] = int(row[1])
            except ValueError:
                pass

    type_rows = []
    with io.open(inv_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 11:
                continue
            try:
                tid = int(row[0]); gid = int(row[1])
                mass = float(row[4]) if row[4] else None
                vol = float(row[5]) if row[5] else None
            except ValueError:
                continue
            type_rows.append((gid, grp_cat.get(gid), vol, mass, tid))
    with _connect() as conn:
        conn.executemany(
            "UPDATE items SET group_id = ?, category_id = ?, volume = ?, mass = ? WHERE type_id = ?",
            type_rows,
        )

    known = {r["type_id"] for r in _connect().execute("SELECT type_id FROM items").fetchall()}
    batch = []
    with io.open(attr_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 4:
                continue
            try:
                tid = int(row[0]); aid = int(row[1])
            except ValueError:
                continue
            if tid not in known:  # 导入全部属性(供面板统计与后续 DPS 使用)
                continue
            raw = row[3] if row[3] not in ("", None) else row[2]
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            batch.append((tid, aid, val))
            if len(batch) >= 3000:
                with _connect() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO type_attrs (type_id, attribute_id, value) VALUES (?, ?, ?)",
                        batch,
                    )
                batch = []
    if batch:
        with _connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO type_attrs (type_id, attribute_id, value) VALUES (?, ?, ?)",
                batch,
            )

    try:
        inv_file.unlink(); grp_file.unlink(); attr_file.unlink(); tmp.rmdir()
    except OSError:
        pass
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM type_attrs").fetchone()["c"]
    print(f"属性导入完成: {n} 条", flush=True)
    rebuild_type_slots()


@lru_cache(maxsize=32768)
def _get_item_cached(db_path, type_id: int):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE type_id = ?", (type_id,)).fetchone()
    return dict(row) if row else None


def get_item(type_id: int):
    return _get_item_cached(str(INDEX_DB), int(type_id))


@_memoize_type_ids
def get_items(type_ids) -> dict:
    ids = list(type_ids)
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(f"SELECT * FROM items WHERE type_id IN ({marks})", ids).fetchall()
    return {r["type_id"]: dict(r) for r in rows}


@_memoize_type_ids
def get_type_attrs(type_ids) -> dict:
    ids = list(type_ids)
    marks = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT type_id, attribute_id, value FROM type_attrs WHERE type_id IN ({marks})", ids
        ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["type_id"], {})[r["attribute_id"]] = r["value"]
    return out

def attach_categories() -> None:
    """重新下载 invTypes/invGroups,为已收录物品补齐 group_id / category_id。"""
    import io as _io

    init_index()
    tmp = config.DATA_DIR / "_build"
    tmp.mkdir(parents=True, exist_ok=True)
    inv_file = tmp / "invTypes.csv"
    grp_file = tmp / "invGroups.csv"

    _download(SOURCE_BASE + "invTypes.csv", inv_file, "invTypes(类别信息)")
    _download(SOURCE_BASE + "invGroups.csv", grp_file, "invGroups(组→类目映射)")

    # group_id -> category_id
    grp_cat = {}
    with _io.open(grp_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 3:
                continue
            try:
                grp_cat[int(row[0])] = int(row[1])
            except ValueError:
                pass

    rows = []
    with _io.open(inv_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 3:
                continue
            try:
                tid = int(row[0])
                gid = int(row[1])
            except ValueError:
                continue
            rows.append((gid, grp_cat.get(gid), tid))

    with _connect() as conn:
        conn.executemany(
            "UPDATE items SET group_id = ?, category_id = ? WHERE type_id = ?",
            rows,
        )

    try:
        inv_file.unlink()
        grp_file.unlink()
        tmp.rmdir()
    except OSError:
        pass
    cats = len([c for c, _ in category_counts()])
    print(f"类别补齐完成：共 {cats} 个分类", flush=True)

def search(q: str, limit: int = 20) -> list:
    """本地模糊搜索物品名（中/英文，子串匹配，前缀优先）。"""
    q = (q or "").strip()
    if not q or not INDEX_DB.exists():
        return []
    lq = q.lower()
    like = "%" + lq.replace("%", "\\%").replace("_", "\\_") + "%"
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT type_id, name_en, name_zh, name_en_lower, name_zh_lower
            FROM items
            WHERE name_en_lower LIKE ? ESCAPE '\\' OR name_zh_lower LIKE ? ESCAPE '\\'
            LIMIT 400
            """,
            (like, like),
        ).fetchall()

    found = []
    for r in rows:
        en_l = r["name_en_lower"]
        zh_l = r["name_zh_lower"] or ""
        prefix = en_l.startswith(lq) or zh_l.startswith(lq)
        contains = lq in en_l or lq in zh_l
        if not (prefix or contains):
            continue
        display = r["name_zh"] or r["name_en"]
        found.append((0 if prefix else 1, len(display), r["type_id"], display))

    found.sort(key=lambda t: (t[0], t[1], t[2]))
    return [
        {"type_id": t[2], "name": t[3]}
        for t in found[: max(1, min(limit, 50))]
    ]


def _download(url: str, dest: Path, label: str):
    print(f"下载 {label} ...", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=600, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(1 << 16):
                f.write(chunk)
                total += len(chunk)
    print(f"  {label} 完成: {total / 1024 / 1024:.1f} MB", flush=True)


def build() -> None:
    t0 = time.time()
    tmp = config.DATA_DIR / "_build"
    tmp.mkdir(parents=True, exist_ok=True)
    inv_file = tmp / "invTypes.csv"
    trn_file = tmp / "trnTranslations.csv"

    _download(SOURCE_BASE + "invTypes.csv", inv_file, "invTypes(英文名)")
    _download(SOURCE_BASE + "trnTranslations.csv", trn_file, "trnTranslations(多语言含中文)")

    # 1) 中文 typeName: tcID=8 & language=zh
    print("解析中文翻译 ...", flush=True)
    zh: dict = {}
    with io.open(trn_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0 or len(row) < 4:
                continue
            if row[0] == TYPENAME_TCID and row[2] == ZH_LANG:
                zh[row[1]] = row[3]
    print(f"  中文条目: {len(zh)}", flush=True)

    # 2) invTypes: 只收录 published 物品
    print("解析物品英文名并入库存 ...", flush=True)
    init_index()
    batch = []
    with io.open(inv_file, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0 or len(row) < 11:
                continue
            if row[10] != "1":  # published
                continue
            try:
                tid = int(row[0])
            except ValueError:
                continue
            en = row[2]
            if not en:
                continue
            name_zh = zh.get(str(tid))
            batch.append(
                (tid, en, name_zh, en.lower(), (name_zh or "").lower())
            )
            if len(batch) >= 2000:
                _insert_batch(batch)
                batch = []
    _insert_batch(batch)

    # 清理临时文件
    try:
        inv_file.unlink()
        trn_file.unlink()
        tmp.rmdir()
    except OSError:
        pass

    print(
        f"完成: 共收录 {index_count()} 个已发布物品，耗时 {time.time() - t0:.0f}s",
        flush=True,
    )


def _insert_batch(batch):
    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO items
                (type_id, name_en, name_zh, name_en_lower, name_zh_lower)
            VALUES (?, ?, ?, ?, ?)
            """,
            batch,
        )


def main():
    parser = argparse.ArgumentParser(description="构建/查询本地物品名称索引")
    parser.add_argument("--build", action="store_true", help="下载数据并构建索引")
    parser.add_argument("--count", action="store_true", help="显示索引条目数")
    parser.add_argument("--search", metavar="关键词", help="本地模糊搜索测试")
    parser.add_argument("--attach", action="store_true", help="补齐物品的类别信息(下载 invTypes/invGroups)")
    parser.add_argument("--attrs", action="store_true", help="导入配装所需的 dogma 属性(下载 invTypes/invGroups/dgmTypeAttributes)")
    parser.add_argument("--effects", action="store_true", help="导入槽位相关效果(下载 dgmEffects/dgmTypeEffects)")
    parser.add_argument("--slots", action="store_true", help="重建物品槽位索引")
    args = parser.parse_args()

    if args.build:
        build()
    elif args.attach:
        attach_categories()
    elif args.attrs:
        build_attributes()
    elif args.effects:
        build_effects()
    elif args.slots:
        rebuild_type_slots()
    elif args.count:
        init_index()
        print("索引条目数:", index_count())
    elif args.search:
        for it in search(args.search):
            print(it["type_id"], "|", it["name"])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()





def find_by_name(name: str):
    """按中/英文名匹配单个物品:精确(忽略大小写/空格)→ 模糊回退。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    norm = key.replace(" ", "")
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE name_en_lower = ? OR name_zh_lower = ? LIMIT 1",
            (key, key),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            """SELECT * FROM items
               WHERE replace(name_en_lower, ' ', '') = ? OR replace(name_zh_lower, ' ', '') = ?
               LIMIT 1""",
            (norm, norm),
        ).fetchone()
        if row:
            return dict(row)

        # 模糊回退:取前缀相同的候选做相似度匹配,避免误配
        from difflib import SequenceMatcher
        prefix = norm[:2]
        candidates = conn.execute(
            "SELECT * FROM items WHERE name_en_lower LIKE ? OR name_zh_lower LIKE ? LIMIT 3000",
            (prefix + "%", prefix + "%"),
        ).fetchall()
    best = None
    best_score = 0.0
    second = 0.0
    for cand in candidates:
        en = (cand["name_en_lower"] or "").replace(" ", "")
        zh = (cand["name_zh_lower"] or "").replace(" ", "")
        score = max(SequenceMatcher(None, norm, en).ratio(), SequenceMatcher(None, norm, zh).ratio())
        if score > best_score:
            second = best_score
            best_score, best = score, cand
        elif score > second:
            second = score
    if best is not None and best_score >= 0.8 and (best_score - second) >= 0.05:
        return dict(best)
    return None
















