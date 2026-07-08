"""history 子包：搜索侧的记忆，合并了原来的仓库记忆和搜索历史，两张表一个库（history.db）。

分两个子模块，用 full_name 挂钩、拆解只存一份：

- repo_memory/：仓库账本，一仓库一行，存跨搜索不变的拆解知识（贵，全库只一份）。搜索命中直接
  复用跳 explorer，点开仓库看 seen_runs 时间线审计。
- runs/：搜索账本，一次搜索一行，每个仓库只留那次的轻量增量（判定、轨迹、token），拆解按名字
  指向 repo_memory 不重复抄；回放一次搜索时 JOIN 回来拼成完整结果。

外部统一从这里 import（`from hunter.history import ...`）。写入只走 save_run 一个口，它一次落两张表。
读侧：content_filter 用 load_memories 命中跳 explorer；server 的 /memory 路由走仓库账本那组、
/history 路由走搜索账本那组。
"""

from hunter.history.repo_memory import (
    init_repo_memory,
    load_memories,
    list_memories,
    get_memory,
    delete_memory,
    clear_repos,
)
from hunter.history.runs import (
    init_runs,
    save_run,
    list_runs,
    get_run,
    delete_repo_from_run,
    delete_process,
    delete_run,
    clear_runs,
)

__all__ = [
    "init_repo_memory",
    "load_memories",
    "list_memories",
    "get_memory",
    "delete_memory",
    "clear_repos",
    "init_runs",
    "save_run",
    "list_runs",
    "get_run",
    "delete_repo_from_run",
    "delete_process",
    "delete_run",
    "clear_runs",
]
