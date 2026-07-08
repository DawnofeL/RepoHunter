"""chat_sessions 表的建表、存、读（S2 对话记录）。

一个会话对应一行，messages 存这次对话从头到尾的完整消息数组。同一次对话再聊一轮，
session_id 不变、前端把追加了新消息的完整快照发上来，走 UPDATE 整行覆盖；点新对话前端换个
session_id，走 INSERT 新插一行。session_id 用创建时间戳，一眼看出啥时候开的、也能排序。
title 只在新建时按第一条用户消息截一段当占位，往后每轮 save_session 只覆盖 context/messages，
不碰 title，用户改过的名字不会被聊天流程冲掉。改名走 rename_session。连库走共享 memory_db。
"""

import json
import sqlite3

from hunter.memory.memory_db import _connect, now_str

# 一个会话一行。context 是注入的仓库全名列表，messages 是消息数组，都存成 JSON 字符串。
# 提取记忆的 extract_cursor 那列留给 S3 往这张表上加，S2 不建
CREATE_CHAT_SESSIONS = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  TEXT PRIMARY KEY,
    title       TEXT,
    context     TEXT,
    messages    TEXT,
    pinned      INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
)
"""

# 存 JSON 的列，读写时统一走 json.dumps / json.loads
JSON_COLS = ("context", "messages")


def init_chat_sessions() -> None:
    """建 chat_sessions 表，不存在才建，server 启动时调一次。"""
    conn = _connect()
    try:
        conn.execute(CREATE_CHAT_SESSIONS)
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    # 把一行转成 dict，JSON 列 loads 回对象，空的给 []
    d = dict(row)
    for col in JSON_COLS:
        raw = d.get(col)
        d[col] = json.loads(raw) if raw else []
    return d


def _title_from(messages: list) -> str:
    # 新建会话时拿第一条用户消息截一段当标题占位，没有就给个默认名
    for msg in messages or []:
        if msg.get("role") == "user":
            text = (msg.get("content") or "").strip().replace("\n", " ")
            if text:
                return text[:40]
    return "新对话"


def save_session(session_id: str, context: list, messages: list) -> None:
    """存一次对话，upsert：已存在就覆盖 context/messages，没有就新建并给个标题占位。

    前端每聊完一轮调一次，messages 是从头到尾的完整快照，直接覆盖不用拼历史。
    created_at 和 title 只在新建时写，更新时保留原来的首次日期和名字。
    """
    now = now_str()
    ctx_json = json.dumps(context or [], ensure_ascii=False)
    msg_json = json.dumps(messages or [], ensure_ascii=False)
    conn = _connect()
    try:
        exists = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE chat_sessions SET context = ?, messages = ?, updated_at = ? "
                "WHERE session_id = ?",
                (ctx_json, msg_json, now, session_id),
            )
        else:
            conn.execute(
                "INSERT INTO chat_sessions (session_id, title, context, messages, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, _title_from(messages), ctx_json, msg_json, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def rename_session(session_id: str, title: str) -> None:
    """单独改一个会话的标题，不动 context/messages/updated_at。"""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE chat_sessions SET title = ? WHERE session_id = ?", (title, session_id)
        )
        conn.commit()
    finally:
        conn.close()


def set_pinned(session_id: str, pinned: bool) -> None:
    """单独改一个会话的置顶状态，不动其它列。"""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE chat_sessions SET pinned = ? WHERE session_id = ?",
            (1 if pinned else 0, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_session(session_id: str) -> dict | None:
    """取一次完整对话，JSON 列解析回对象，不存在返回 None。点开旧会话用。"""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row is not None else None


def list_sessions() -> list[dict]:
    """列出所有会话的摘要，置顶的排最前、同组内按 updated_at 倒序，不取大块的 messages。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT session_id, title, updated_at, pinned FROM chat_sessions "
            "ORDER BY pinned DESC, updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_session(session_id: str) -> None:
    """删掉一次会话。"""
    conn = _connect()
    try:
        conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
