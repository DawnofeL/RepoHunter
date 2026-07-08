"""chat_memories 表的建表、按 action 落库、按类型/仓库/会话读，外加提取游标（S3 提取记忆存储层）。

提取模型返回一批 `{action, type, name, ...}`，由 apply_actions 逐条执行：add 就 INSERT、
update 按 name 找行覆盖、delete 按 name 删，落库前每条 content 过密钥扫描。注入侧两路：
list_general 全量带四类通用记忆、list_by_repo 按仓库名捞 repo 类。list_manifest 给提取前的
「已有记忆清单」用，list_by_session 给开发者面板看某次会话产出了啥。get/set_extract_cursor
读写 chat_sessions 上的提取游标（那列由本模块的 init 往 S2 的表上加）。连库走共享 memory_db。
"""

import json
import sqlite3

from hunter.memory.memory_db import _connect, now_str
from hunter.memory.extract.secret_scan import scan_and_redact

# 一条记忆一行，把 CC 记忆文件头部的 name/description/type 和正文拍平成列。
# type 是 user/feedback/project/reference/repo；name 唯一，update/delete 按它找行；
# full_name/where_anchor 只有 repo 类才填。时间列用可读格式（memory_db.now_str）
CREATE_CHAT_MEMORIES = """
CREATE TABLE IF NOT EXISTS chat_memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    type          TEXT,
    name          TEXT UNIQUE,
    description   TEXT,
    content       TEXT,
    full_name     TEXT,
    where_anchor  TEXT,
    session_id    TEXT,
    created_at    TEXT,
    updated_at    TEXT
)
"""

# 注入时按仓库名捞 repo 类记忆，full_name 建普通索引让它快（name 已经是 UNIQUE 自带索引）
CREATE_CHAT_MEMORIES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_chat_mem_full_name ON chat_memories(full_name)
"""

# 四类通用记忆，注入时全量带上；repo 类不在此列，它按仓库名单独捞
GENERAL_TYPES = ("user", "feedback", "project", "reference")


def init_chat_memories() -> None:
    """建 chat_memories 表和 full_name 索引，并给 S2 的 chat_sessions 补一列 extract_cursor。

    server 启动时调，要排在 init_chat_sessions（建 chat_sessions）之后。extract_cursor 记提取
    处理到第几条消息了，是提取模块用的，列物理上在 chat_sessions 表上，缺就用 migration 补。
    """
    conn = _connect()
    try:
        conn.execute(CREATE_CHAT_MEMORIES)
        conn.execute(CREATE_CHAT_MEMORIES_INDEX)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()]
        if "extract_cursor" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN extract_cursor INTEGER DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def get_extract_cursor(session_id: str) -> int:
    """读这次会话的提取游标，记提取处理到第几条消息了，没有就 0。"""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT extract_cursor FROM chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["extract_cursor"] if row and row["extract_cursor"] is not None else 0


def set_extract_cursor(session_id: str, cursor: int) -> None:
    """挪提取游标，提取成功后才调，把它推到最新处理过的消息位置。

    用 upsert：session 行还没被 save_session 建出来时（比如第一轮就说「记住」触发提取），
    也先建一行把游标存住，不然纯 UPDATE 会因为没这行而静默丢失。后面 save_session 会补齐
    title/messages 那几列。
    """
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, extract_cursor) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET extract_cursor = excluded.extract_cursor",
            (session_id, cursor),
        )
        conn.commit()
    finally:
        conn.close()


def apply_actions(rows: list[dict], session_id: str) -> dict:
    """把提取模型返回的一批动作落库，按 action 分发，返回各类计数供日志和监控。

    每条 add/update 的 content 落库前过密钥扫描，命中就打码。update/delete 按 name 找行，
    name 不存在就跳过（这就是 update/delete 的名字校验，编出来的名字自然落空）。add 遇到
    已存在的 name 也当更新处理，不重复插。全程一个连接一次提交。

    Args:
        rows:       提取模型返回的动作列表，每条含 action、type、name、description、content，
                    repo 类另带 full_name、where（注意模型用 where，表里列名是 where_anchor）。
        session_id: 这批记忆出自哪次会话，记进 session_id 列。
    Returns:
        {"added": n, "updated": n, "deleted": n, "skipped": n, "redacted": n} 计数。
    """
    now = now_str()
    stat = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "redacted": 0}
    conn = _connect()
    try:
        for row in rows:
            action = (row.get("action") or "").strip()
            name = (row.get("name") or "").strip()
            if not name:
                stat["skipped"] += 1
                continue

            existing = conn.execute(
                "SELECT id FROM chat_memories WHERE name = ?", (name,)
            ).fetchone()

            if action == "delete":
                if existing:
                    conn.execute("DELETE FROM chat_memories WHERE name = ?", (name,))
                    stat["deleted"] += 1
                else:
                    stat["skipped"] += 1
                continue

            if action not in ("add", "update"):
                stat["skipped"] += 1
                continue

            # 落库前扫密钥，命中就打码
            content, hits = scan_and_redact(row.get("content") or "")
            if hits:
                stat["redacted"] += 1

            values = (
                row.get("type") or "",
                row.get("description") or "",
                content,
                row.get("full_name") or "",
                row.get("where") or "",
                session_id,
            )

            if existing:
                # 已有这条 name（update，或 add 到了已存在的），覆盖正文不动 created_at
                conn.execute(
                    "UPDATE chat_memories SET type = ?, description = ?, content = ?, "
                    "full_name = ?, where_anchor = ?, session_id = ?, updated_at = ? WHERE name = ?",
                    (*values, now, name),
                )
                stat["updated"] += 1
            else:
                conn.execute(
                    "INSERT INTO chat_memories (name, type, description, content, "
                    "full_name, where_anchor, session_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, *values, now, now),
                )
                stat["added"] += 1
        conn.commit()
    finally:
        conn.close()
    return stat


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def list_general() -> list[dict]:
    """全量取四类通用记忆(user/feedback/project/reference)，注入时全带上。按更新时间倒序。"""
    marks = ",".join("?" for _ in GENERAL_TYPES)
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM chat_memories WHERE type IN ({marks}) ORDER BY updated_at DESC",
            GENERAL_TYPES,
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def list_by_repo(full_name: str) -> list[dict]:
    """取某个仓库的 repo 类记忆，注入这个仓库时跟着拆解一起拼进去。按更新时间倒序。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM chat_memories WHERE type = 'repo' AND full_name = ? ORDER BY updated_at DESC",
            (full_name,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def list_by_session(session_id: str) -> list[dict]:
    """取这次会话提取出的全部记忆（各类），给开发者监控面板看本会话产出了啥。按更新时间倒序。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM chat_memories WHERE session_id = ? ORDER BY updated_at DESC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def list_manifest() -> list[dict]:
    """列所有记忆的 name 加 description 加 type，提取前注入给模型当「已有记忆清单」防重复。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT name, description, type FROM chat_memories ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
