"""history 库的共享数据库连接。仓库账本和搜索账本两张表都连同一个 history.db。

repo_memory 子模块存跨搜索不变的仓库知识（拆解只存一份），runs 子模块存每次搜索的流水账
（每个仓库只留轻量增量，拆解按 full_name 指向 repo_memory 不重复抄）。两边都从这里取 DB_PATH
和 _connect，不各写一份。跟 dev_audit.db 分开，那个是开发者监控、另有各自的文件。每次操作开
一个连接、用完就关，单用户场景够用。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

# DB 落在项目根的 data/ 下，按文件位置往上回三层到项目根，不靠运行时 cwd
DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "history.db"

# 时间戳统一存成人能读的格式（本地时间），别存毫秒数——直接开库看也一目了然。
# 这个格式还能按字符串正序排（列表页 ORDER BY 靠它），前端 fmtTime 的 new Date 也能解析。
# 两张表的时间列都用它，格式一致
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
