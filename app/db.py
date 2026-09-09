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
            CREATE TABLE IF NOT EXISTS skill_names (
                skill_id  INTEGER PRIMARY KEY,
                name_zh   TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        # 旧库迁移：为 characters 补上新列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(characters)").fetchall()}
        if "wallet_isk" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN wallet_isk REAL")
        if "owner_hash" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN owner_hash TEXT")
        if "account_name" not in cols:
            conn.execute("ALTER TABLE characters ADD COLUMN account_name TEXT")


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
):
    with _write_lock, connect() as conn:
        conn.execute(
            """
            UPDATE characters
            SET total_sp = ?, unallocated_sp = ?, wallet_isk = ?, skills_json = ?, queue_json = ?,
                last_updated = ?, needs_reauth = 0, auth_error = NULL
            WHERE character_id = ?
            """,
            (total_sp, unallocated_sp, wallet_isk, skills_json, queue_json, _now_iso(), character_id),
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
            "INSERT OR REPLACE INTO skill_names (skill_id, name_zh, updated_at) VALUES (?, ?, ?)",
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
