"""开发者监控的审计落库：把 dev.record 记的每条记录追加进 dev_audit.db，重启后仍在。

内存环形缓冲（dev.py）管实时面板和实时流，这里管持久审计。一条记录一行，异构字段整个
json.dumps 进 data 列，只把 session_id/kind/ts 抽出来当索引列。追加日志、只增不改。
和 memory.db、history.db 分开各管一摊，连的是自己的 dev_audit.db。只依赖标准库，不 import
任何 hunter 模块，好让 dev.py 引它而不破坏「dev 无循环依赖」这条底线。
"""

import json
import sqlite3
import time
from pathlib import Path

# DB 落在项目根的 data/ 下，按文件位置往上回两层到 RepoHunter/，不靠运行时 cwd
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dev_audit.db"

CREATE_DEV_RECORDS = """
CREATE TABLE IF NOT EXISTS dev_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    kind        TEXT,
    ts          INTEGER,
    data        TEXT
)
"""

# 审计常按 session 捞，给 session_id 建索引让它快
CREATE_DEV_RECORDS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_dev_records_session ON dev_records(session_id)
"""


def _connect() -> sqlite3.Connection:
    # 每次操作开一个连接，用完就关。开之前先保证 data/ 目录在
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建 dev_records 表和索引，不存在才建，server 启动时调一次。"""
    conn = _connect()
    try:
        conn.execute(CREATE_DEV_RECORDS)
        conn.execute(CREATE_DEV_RECORDS_INDEX)
        conn.commit()
    finally:
        conn.close()


def append(session_id: str, rec: dict) -> None:
    """追加一条审计记录。rec 是 dev.record 拼好的整条 dict（含 kind/ts），整个 json 存进 data 列。

    kind/ts 从 rec 里取出来单独当列存，取不到给兜底，不让缺字段把整条写崩。
    """
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO dev_records (session_id, kind, ts, data) VALUES (?, ?, ?, ?)",
            (
                session_id,
                rec.get("kind", ""),
                rec.get("ts", int(time.time() * 1000)),
                json.dumps(rec, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit_sessions() -> list[dict]:
    """列出审计过的会话摘要：每个 session 的记录条数和最后一条时间，最近的在前。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT session_id, COUNT(*) AS count, MAX(ts) AS last_ts "
            "FROM dev_records GROUP BY session_id ORDER BY last_ts DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_audit_records(session_id: str) -> list[dict]:
    """取某个 session 落库的全部记录，按时间正序，data 列解析回 dict。不受内存 40 条上限限制。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT data FROM dev_records WHERE session_id = ? ORDER BY ts ASC, id ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["data"]))
        except Exception:
            pass
    return out


def clear_audit(session_id: str | None = None) -> None:
    """清审计记录。给 session_id 清那一个，不给清全部。"""
    conn = _connect()
    try:
        if session_id is None:
            conn.execute("DELETE FROM dev_records")
        else:
            conn.execute("DELETE FROM dev_records WHERE session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
