"""SQLite 数据存取（标准库 sqlite3，按调用打开短连接）。"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_write_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS characters (
                character_id   INTEGER PRIMARY KEY,
                character_name TEXT NOT NULL,
                access_token   TEXT NOT NULL,
                refresh_token  TEXT NOT NULL,
                expires_at     INTEGER NOT NULL,
                total_sp       INTEGER,
                unallocated_sp INTEGER,
                wallet_isk     REAL,
                skills_json    TEXT,
                attributes_json TEXT,
                implants_json  TEXT,
                queue_json     TEXT,
                last_updated   TEXT,
                needs_reauth   INTEGER NOT NULL DEFAULT 0,
                auth_error     TEXT,
                owner_hash     TEXT,
                account_name   TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_states (
                state      TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sp_history (
                date             TEXT PRIMARY KEY,
                total_extractors INTEGER NOT NULL,
                trained_sp_total INTEGER NOT NULL,
                combined_sp_total INTEGER NOT NULL,
                char_count       INTEGER NOT NULL,
                updated_at       TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fittings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL,
                ship_type_id INTEGER NOT NULL,
                data_json    TEXT NOT NULL,
                created_at   TEXT,
                updated_at   TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kind         TEXT NOT NULL,
                date         TEXT NOT NULL,
                account_name TEXT,
                amount       REAL NOT NULL,
                category     TEXT,
                qty          INTEGER,
                note         TEXT,
                created_at   TEXT,
                unit_price   REAL,
                auto         INTEGER NOT NULL DEFAULT 0,
                character_id INTEGER,
                character_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_names (
                skill_id  INTEGER PRIMARY KEY,
                name_zh   TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(skill_names)").fetchall()}
        if "description_zh" not in scols:
            conn.execute("ALTER TABLE skill_names ADD COLUMN description_zh TEXT")
        # 旧库迁移：为 characters 补上新列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(characters)").fetchall()}
        if "wallet_isk" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN wallet_isk REAL")
        if "owner_hash" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN owner_hash TEXT")
        if "account_name" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN account_name TEXT")
        if "attributes_json" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN attributes_json TEXT")
        if "implants_json" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN implants_json TEXT")
        fcols = {r["name"] for r in conn.execute("PRAGMA table_info(finance_entries)").fetchall()}
        if "unit_price" not in fcols:
            conn.execute("ALTER TABLE finance_entries ADD COLUMN unit_price REAL")
        if "auto" not in fcols:
            conn.execute("ALTER TABLE finance_entries ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
        if "character_id" not in fcols:
            conn.execute("ALTER TABLE finance_entries ADD COLUMN character_id INTEGER")
        if "character_name" not in fcols:
            conn.execute("ALTER TABLE finance_entries ADD COLUMN character_name TEXT")


def _to_dict(row) -> dict:
    d = dict(row)
    d["needs_reauth"] = bool(d["needs_reauth"])
    return d


def list_characters() -> list:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM characters ORDER BY character_name COLLATE NOCASE"
        ).fetchall()
    return [_to_dict(r) for r in rows]


def get_character(character_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE character_id = ?", (character_id,)
        ).fetchone()
    return _to_dict(row) if row else None


def upsert_character(
    character_id: int,
    character_name: str,
    access_token: str,
    refresh_token: str,
    expires_at: int,
    owner_hash=None,
):
    with _write_lock, connect() as conn:
        conn.execute(
            """
            INSERT INTO characters
                (character_id, character_name, access_token, refresh_token, expires_at,
                 needs_reauth, auth_error, owner_hash)
            VALUES (?, ?, ?, ?, ?, 0, NULL, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                character_name   = excluded.character_name,
                access_token     = excluded.access_token,
                refresh_token    = excluded.refresh_token,
                expires_at       = excluded.expires_at,
                needs_reauth     = 0,
                auth_error       = NULL,
                owner_hash       = COALESCE(excluded.owner_hash, characters.owner_hash)
            """,
            (character_id, character_name, access_token, refresh_token, expires_at, owner_hash),
        )


def set_owner_hash(character_id: int, owner_hash: str):
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE characters SET owner_hash = ? WHERE character_id = ?",
            (owner_hash, character_id),
        )


def update_tokens(character_id: int, access_token: str, refresh_token: str, expires_at: int):
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE characters SET access_token = ?, refresh_token = ?, expires_at = ? WHERE character_id = ?",
            (access_token, refresh_token, expires_at, character_id),
        )


def update_character_data(
    character_id: int,
    total_sp,
    unallocated_sp,
    skills_json: str,
    queue_json: str,
    wallet_isk=None,
    attributes_json: str | None = None,
    implants_json: str | None = None,
):
    with _write_lock, connect() as conn:
        conn.execute(
            """
            UPDATE characters
            SET total_sp = ?, unallocated_sp = ?, wallet_isk = ?, skills_json = ?,
                attributes_json = COALESCE(?, attributes_json),
                implants_json = COALESCE(?, implants_json), queue_json = ?,
                last_updated = ?, needs_reauth = 0, auth_error = NULL
            WHERE character_id = ?
            """,
            (total_sp, unallocated_sp, wallet_isk, skills_json, attributes_json,
             implants_json, queue_json, _now_iso(), character_id),
        )


def set_reauth(character_id: int, message: str):
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE characters SET needs_reauth = 1, auth_error = ? WHERE character_id = ?",
            (message, character_id),
        )


def delete_character(character_id: int):
    with _write_lock, connect() as conn:
        conn.execute("DELETE FROM characters WHERE character_id = ?", (character_id,))


def add_state(state: str):
    with _write_lock, connect() as conn:
        # 顺带清理过期状态
        conn.execute("DELETE FROM pending_states WHERE created_at < ?", (time.time() - 3600,))
        conn.execute(
            "INSERT OR REPLACE INTO pending_states (state, created_at) VALUES (?, ?)",
            (state, time.time()),
        )


def pop_state(state: str) -> bool:
    """取出并删除一个登录状态；仅当存在且未超过 10 分钟时返回 True。"""
    with _write_lock, connect() as conn:
        row = conn.execute(
            "SELECT created_at FROM pending_states WHERE state = ?", (state,)
        ).fetchone()
        conn.execute("DELETE FROM pending_states WHERE state = ?", (state,))
    if row is None:
        return False
    return (time.time() - row["created_at"]) < 600


def get_skill_names(skill_ids) -> dict:
    """读取本地缓存的技能中文名，返回 {skill_id: name_zh}。"""
    ids = [int(i) for i in skill_ids]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT skill_id, name_zh FROM skill_names WHERE skill_id IN ({marks})",
            ids,
        ).fetchall()
    return {r["skill_id"]: r["name_zh"] for r in rows}


def save_skill_names(mapping: dict):
    """批量保存技能中文名缓存。"""
    if not mapping:
        return
    now = _now_iso()
    items = [(int(k), str(v), now) for k, v in mapping.items()]
    with _write_lock, connect() as conn:
        conn.executemany(
            """INSERT INTO skill_names (skill_id, name_zh, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(skill_id) DO UPDATE SET name_zh=excluded.name_zh, updated_at=excluded.updated_at""",
            items,
        )


def get_skill_details(skill_ids) -> dict:
    """读取技能中文名与官方描述，返回 {skill_id: {name, description}}。"""
    ids = [int(i) for i in skill_ids]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT skill_id, name_zh, description_zh FROM skill_names WHERE skill_id IN ({marks})",
            ids,
        ).fetchall()
    return {r["skill_id"]: {"name": r["name_zh"], "description": r["description_zh"] or ""} for r in rows}


def save_skill_details(mapping: dict):
    """保存技能中文名与官方描述，缺失字段保留旧值。"""
    if not mapping:
        return
    now = _now_iso()
    items = []
    for key, value in mapping.items():
        if isinstance(value, dict):
            name = value.get("name") or value.get("name_zh") or ""
            description = value.get("description") or value.get("description_zh") or ""
        else:
            name, description = str(value), ""
        items.append((int(key), str(name), str(description), now))
    with _write_lock, connect() as conn:
        conn.executemany(
            """INSERT INTO skill_names (skill_id, name_zh, description_zh, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(skill_id) DO UPDATE SET
                   name_zh=CASE WHEN excluded.name_zh != '' THEN excluded.name_zh ELSE skill_names.name_zh END,
                   description_zh=CASE WHEN excluded.description_zh != '' THEN excluded.description_zh ELSE skill_names.description_zh END,
                   updated_at=excluded.updated_at""",
            items,
        )




def delete_all_characters() -> int:
    """删除全部角色及其本地数据，返回删除数量。"""
    with _write_lock, connect() as conn:
        cur = conn.execute("DELETE FROM characters")
        return cur.rowcount


def set_account_name(character_id: int, account_name):
    with _write_lock, connect() as conn:
        conn.execute(
            "UPDATE characters SET account_name = ? WHERE character_id = ?",
            (account_name, character_id),
        )


def upsert_history(date: str, total_extractors: int, trained_sp_total: int,
                   combined_sp_total: int, char_count: int):
    with _write_lock, connect() as conn:
        conn.execute(
            """
            INSERT INTO sp_history
                (date, total_extractors, trained_sp_total, combined_sp_total, char_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_extractors  = excluded.total_extractors,
                trained_sp_total  = excluded.trained_sp_total,
                combined_sp_total = excluded.combined_sp_total,
                char_count        = excluded.char_count,
                updated_at        = excluded.updated_at
            """,
            (date, total_extractors, trained_sp_total, combined_sp_total, char_count, _now_iso()),
        )


def get_history(limit: int = 30) -> list:
    limit = max(1, min(limit, 365))
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sp_history ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]



def add_finance_entry(kind: str, date: str, account_name, amount: float,
                      category=None, qty=None, note=None,
                      unit_price=None, auto: int = 0,
                      character_id=None, character_name=None) -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO finance_entries
                (kind, date, account_name, amount, category, qty, note, created_at,
                 unit_price, auto, character_id, character_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, date, account_name, amount, category, qty, note, _now_iso(),
             unit_price, 1 if auto else 0, character_id, character_name),
        )
        return cur.lastrowid


def update_finance_entry(entry_id: int, kind: str, date: str, account_name,
                         amount: float, category=None, qty=None, note=None) -> bool:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            """
            UPDATE finance_entries
            SET kind = ?, date = ?, account_name = ?, amount = ?, category = ?,
                qty = ?, note = ?
            WHERE id = ?
            """,
            (kind, date, account_name, amount, category, qty, note, entry_id),
        )
        return cur.rowcount > 0


def delete_finance_entry(entry_id: int) -> bool:
    with _write_lock, connect() as conn:
        cur = conn.execute("DELETE FROM finance_entries WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def get_finance_entries(month: str = "", account=None, kind: str = "") -> list:
    clauses = []
    params = []
    if month:
        clauses.append("substr(date, 1, 7) = ?")
        params.append(month)
    if account == "__none__":
        clauses.append("account_name IS NULL")
    elif account:
        clauses.append("account_name = ?")
        params.append(account)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM finance_entries{where} ORDER BY date DESC, id DESC",
            params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("account_name") is None:
            d["account_name"] = ""  # 前端统一用空串表示“未归属”
        out.append(d)
    return out


def get_finance_entry(entry_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM finance_entries WHERE id = ?", (entry_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    if d.get("account_name") is None:
        d["account_name"] = ""
    return d





def list_fittings() -> list:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM fittings ORDER BY updated_at DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


def get_fitting(fitting_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM fittings WHERE id = ?", (fitting_id,)).fetchone()
    return dict(row) if row else None


def add_fitting(name: str, ship_type_id: int, data_json: str) -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            "INSERT INTO fittings (name, ship_type_id, data_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, ship_type_id, data_json, _now_iso(), _now_iso()),
        )
        return cur.lastrowid


def update_fitting(fitting_id: int, name: str, ship_type_id: int, data_json: str) -> bool:
    with _write_lock, connect() as conn:
        cur = conn.execute(
            "UPDATE fittings SET name = ?, ship_type_id = ?, data_json = ?, updated_at = ? WHERE id = ?",
            (name, ship_type_id, data_json, _now_iso(), fitting_id),
        )
        return cur.rowcount > 0


def delete_fitting(fitting_id: int) -> bool:
    with _write_lock, connect() as conn:
        cur = conn.execute("DELETE FROM fittings WHERE id = ?", (fitting_id,))
        return cur.rowcount > 0
