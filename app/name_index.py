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
import time
from pathlib import Path

import httpx

from . import config

INDEX_DB = config.DATA_DIR / "item_index.db"
SOURCE_BASE = "https://www.fuzzwork.co.uk/dump/latest/csv/"
TYPENAME_TCID = "8"
ZH_LANG = "zh"


def _connect():
    conn = sqlite3.connect(str(INDEX_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


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


def index_count() -> int:
    if not INDEX_DB.exists():
        return 0
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()
    return row["c"]


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
    args = parser.parse_args()

    if args.build:
        build()
    elif args.attach:
        attach_categories()
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


