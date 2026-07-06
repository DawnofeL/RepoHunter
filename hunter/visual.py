"""结果可视化：把成本统计和排序结果摆成 DataFrame 表格。

本模块提供两张表。`cost_table` 把全局 COST 各阶段的 token 用量和缓存命中率汇成一张
带合计行的表，跑完一轮看花销用。`ranked_table` 把Content Filter排序后的结果摆成概览，
keypoint 显示命中数，stars 和 size 原样列出给人判体量。
"""

import pandas as pd
from IPython.display import display, HTML

from hunter.cost import COST


def cost_table() -> pd.DataFrame:
    """把 COST 各阶段汇成一张表，看每阶段调用数、token、缓存命中率，带合计行。

    Returns:
        每行一个阶段、末行是合计的 DataFrame，含调用数、输入输出 token、命中率等列。
    """
    rows = []

    # 逐阶段算各自的命中率，输入为 0 时给 0 避免除零
    for stage, c in COST.items():
        hit_rate = c["hit"] / c["prompt"] if c["prompt"] else 0.0
        rows.append({
            "阶段":     stage,
            "调用数":   c["calls"],
            "输入token": c["prompt"],
            "命中":     c["hit"],
            "未命中":   c["miss"],
            "输出token": c["completion"],
            "命中率":   f"{hit_rate * 100:.1f}%",
        })

    # 有数据才补合计行，把各阶段对应字段加总再算总命中率
    if COST:
        tot = {k: sum(c[k] for c in COST.values()) for k in ("calls", "prompt", "hit", "miss", "completion")}
        tot_rate = tot["hit"] / tot["prompt"] if tot["prompt"] else 0.0
        rows.append({
            "阶段":     "合计",
            "调用数":   tot["calls"],
            "输入token": tot["prompt"],
            "命中":     tot["hit"],
            "未命中":   tot["miss"],
            "输出token": tot["completion"],
            "命中率":   f"{tot_rate * 100:.1f}%",
        })

    return pd.DataFrame(rows)


def ranked_table(ranked: list[dict]) -> pd.DataFrame:
    """把Content Filter排序结果摆成概览表，列从左到右：keypoint 命中、stars、size、token。

    keypoint 是「命中数/总数」，排序就按它降序、平手看 stars。stars、size 是原始数字，
    只摆出来让用户自己判体量，不参与判断和排序。

    Args:
        ranked: Repo_Detection 返回的 ranked 列表，每项含 keypoint_hits、keypoint_total 等字段。
    Returns:
        一行一个仓库的概览 DataFrame。
    """
    rows = []
    for r in ranked:

        # keypoint 显示命中数比总数，size 从 KB 换算成 MB 保留一位小数
        rows.append({
            "full_name": r["full_name"],
            "keypoint":  f"{r['keypoint_hits']}/{r['keypoint_total']}",
            "stars":     r.get("stars", 0),
            "size(MB)":  round(r.get("size", 0) / 1024, 1),
            "tokens":    r["tokens"],
            "缓存命中":  f"{float(r['hit_rate']) * 100:.0f}%",
        })
    return pd.DataFrame(rows)


def emoji_progress(repos: list[dict]):
    """给 notebook 用的进度回调工厂，返回一个 on_event，内部维护 emoji 表格并实时刷新。

    Repo_Detection 自己不画进度，只吐纯数据，画表这件事留在这里。每收到一个事件就按阶段和
    状态映射成 emoji 更新对应仓库那一行，再重画整张 HTML 表。webapp 不用这个，自己把事件转 SSE。

    Args:
        repos: 候选仓库列表，每项含 full_name，用来初始化每行。
    Returns:
        on_event(full_name, stage, status, data) 回调，传给 Repo_Detection 的 on_event。
    """
    n = len(repos)

    # 每个仓库一行 content 状态，初始白色待机，看得出当前在哪一步
    state = {r["full_name"]: {"content": "⚪", "round": 0, "tools": 0,
                              "tokens": 0, "hit_rate": 0.0} for r in repos}

    # content 状态映射：白待机、蓝在跑、绿跑完、红降级、灰跳过
    content_emoji = {"running": "🔵", "done": "🟢", "degraded": "🔴", "skipped": "⚫"}

    def render() -> HTML:
        """把所有仓库的 content 状态拼成一张 HTML 进度表。"""
        done = sum(1 for s in state.values() if s["content"] in ("🟢", "🔴", "⚫"))
        rows = "".join(
            f"<tr><td>{fn}</td><td>{s['content']}</td>"
            f"<td>{s['round']}</td><td>{s['tools']}</td>"
            f"<td>{s['tokens']} (命中{s['hit_rate'] * 100:.0f}%)</td></tr>"
            for fn, s in state.items()
        )
        return HTML(
            f"<b>Repo_Detection 进度 {done}/{n}</b>"
            f"<table><tr><th>仓库</th><th>Content</th><th>Round</th><th>Tools</th><th>Tokens</th></tr>{rows}</table>"
        )

    handle = display(render(), display_id=True)

    def on_event(full_name: str, stage: str, status: str, data: dict) -> None:
        """收一个 content 进度事件，更新对应那一行再重画整张表。data 里没带的字段保持原值。"""
        s = state[full_name]
        s["content"] = content_emoji.get(status, s["content"])
        s["round"] = data.get("round", s["round"])
        s["tools"] = data.get("tools", s["tools"])
        s["tokens"] = data.get("tokens", s["tokens"])
        s["hit_rate"] = data.get("hit_rate", s["hit_rate"])
        if handle:
            handle.update(render())

    return on_event