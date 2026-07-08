"""对话侧记忆库的共享数据库连接。会话记录、以后的提取记忆都连同一个 memory.db。

跟搜索侧的 history.db 分开：一个记你跟 AI 聊了什么，一个记搜过哪些仓库，各清各的互不牵连。
角色跟 hunter.history.history_db 一样：给一个 _connect 和统一的可读时间戳，各子模块不各写一份。
每次操作开一个连接、用完就关，单用户场景够用。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

# DB 落在项目根的 data/ 下，按文件位置往上回三层到项目根，不靠运行时 cwd
DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "memory.db"

# 时间戳存成人能读的格式（本地时间），跟 history 侧同一个格式，开库直接看得懂、也能按串排序
TS_FMT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    """当前本地时间的可读字符串，落库时间列都用它。"""
    return datetime.now().strftime(TS_FMT)


def _connect() -> sqlite3.Connection:
    # 每次操作开一个连接，用完就关。开之前先保证 data/ 目录在
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
