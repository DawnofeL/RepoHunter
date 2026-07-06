"""搜索历史的 SQLite 存取：每次搜索留一份完整档，供列表页回看。

一次搜索一行，摘要列(query/languages/keypoints/total/top_name)单独存好，列表页只查它们
不用碰大块的 ranked/cost/process。跟仓库记忆(memory.db)分开存 history.db：历史是「每次
搜索留档」可随便清，记忆是「跨搜索的知识」不该跟着清。是纯数据层，webapp 只管调不管存。
"""

import json
import sqlite3
import time
from pathlib import Path

# DB 落在项目根的 data/ 下，按文件位置往上回两层到 RepoHunter/，不靠运行时 cwd
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "history.db"


def _connect() -> sqlite3.Connection:
    # 每次操作开一个连接，用完就关，单用户场景够用。开之前先保证 data/ 目录在
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建 queries 表，不存在才建，server 启动时调一次。老库缺 process 列就补上。"""
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queries (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER NOT NULL,
                query     TEXT,
                languages TEXT,
                keypoints TEXT,
                total     INTEGER,
                top_name  TEXT,
                ranked    TEXT,
                cost      TEXT,
                process   TEXT
            )
            """
        )
        # 早先建的库缺 process / keypoints 列就补上，老记录这列是 NULL
        cols = [r[1] for r in conn.execute("PRAGMA table_info(queries)").fetchall()]
        if "process" not in cols:
            conn.execute("ALTER TABLE queries ADD COLUMN process TEXT")
        if "keypoints" not in cols:
            conn.execute("ALTER TABLE queries ADD COLUMN keypoints TEXT")
        conn.commit()
    finally:
        conn.close()


def save_query(params: dict, ranked: list, total: int, cost: dict,
               process: dict | None = None) -> int:
    """存一条完整查询结果，返回新行 id。

    摘要列单独存一份，列表页只查它们时不用碰大块的 ranked/cost/process。
    top_name 取排序后的置顶仓库，没结果给空串。process 是搜索过程（QU、探查 trace），
    传 None 就当没存过程，存 NULL。
    """
    top_name = ranked[0]["full_name"] if ranked else ""

    # keypoints 结构化存进 keypoints 列；query 列存一份分号拼接的摘要，给列表页直接显示
    keypoints = params.get("keypoints", [])
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO queries (ts, query, languages, keypoints, total, top_name, ranked, cost, process) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(time.time() * 1000),
                "；".join(keypoints),
                json.dumps(params.get("languages", []), ensure_ascii=False),
                json.dumps(keypoints, ensure_ascii=False),
                total,
                top_name,
                json.dumps(ranked, ensure_ascii=False),
                json.dumps(cost, ensure_ascii=False),
                json.dumps(process, ensure_ascii=False) if process is not None else None,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_queries() -> list[dict]:
    """列出所有历史的摘要，按时间倒序最新在前，不取 ranked/cost 大块。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, ts, query, languages, keypoints, total, top_name, "
            "(process IS NOT NULL) AS has_process "
            "FROM queries ORDER BY ts DESC"
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["languages"] = json.loads(d["languages"]) if d["languages"] else []
        d["keypoints"] = json.loads(d["keypoints"]) if d.get("keypoints") else []
        d["has_process"] = bool(d["has_process"])
        out.append(d)
    return out


def get_query(qid: int) -> dict | None:
    """取一条完整历史，ranked/cost 解析回对象，不存在返回 None。"""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM queries WHERE id = ?", (qid,)).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    d = dict(row)
    d["languages"] = json.loads(d["languages"]) if d["languages"] else []
    d["keypoints"] = json.loads(d["keypoints"]) if d.get("keypoints") else []
    d["ranked"] = json.loads(d["ranked"]) if d["ranked"] else []
    d["cost"] = json.loads(d["cost"]) if d["cost"] else {}
    d["process"] = json.loads(d["process"]) if d.get("process") else None
    return d


def delete_repo(qid: int, full_name: str) -> bool:
    """从一条历史里删掉一个仓库，改写它的 ranked，并同步更新 total 和 top_name。

    删完这条历史还在，只是少一个仓库。该条不存在返回 False。
    """
    conn = _connect()
    try:
        row = conn.execute("SELECT ranked FROM queries WHERE id = ?", (qid,)).fetchone()
        if row is None:
            return False
        ranked = json.loads(row["ranked"]) if row["ranked"] else []
        ranked = [r for r in ranked if r.get("full_name") != full_name]
        top_name = ranked[0]["full_name"] if ranked else ""
        conn.execute(
            "UPDATE queries SET ranked = ?, total = ?, top_name = ? WHERE id = ?",
            (json.dumps(ranked, ensure_ascii=False), len(ranked), top_name, qid),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_process(qid: int) -> None:
    """只删一条历史的搜索过程，结果照样留着。把 process 列置回 NULL。"""
    conn = _connect()
    try:
        conn.execute("UPDATE queries SET process = NULL WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()


def delete_query(qid: int) -> None:
    """删掉一整条历史。"""
    conn = _connect()
    try:
        conn.execute("DELETE FROM queries WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()


def clear_all() -> None:
    """清空所有历史。"""
    conn = _connect()
    try:
        conn.execute("DELETE FROM queries")
        conn.commit()
    finally:
        conn.close()
