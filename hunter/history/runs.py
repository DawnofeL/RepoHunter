"""这个文件管搜索流水账：用户每发起一次搜索，就在这儿记一行（对应数据库里的 `runs` 表）。

一行里只放这次搜索的轻巧信息，像搜的关键词、结果怎么排、每个仓库对上了几条需求、花了多少钱。
至于每个仓库那份又大又重的分析（代码里叫 `dissection`），不在这儿存，另有一张仓库账本（`repo_memory` 表）专门存它、全库只存一份；这行只写个仓库名（`full_name`）指过去，免得每搜一次就把同一份大东西重抄一遍。

真正干活的是两个函数。
`save_run` 存一次搜索：先写下这一行、拿到这次搜索的编号，再把这批仓库交给仓库账本更新，并在每个仓库名下记一笔「这次搜过它」。
`get_run` 翻看一次旧搜索：把那一行读出来，再拿每个仓库名去仓库账本取回它的分析，拼成完整结果给页面显示。

另外几个是常规操作：`list_runs` 给列表页列出所有搜索的简表，`delete_run`、`delete_repo_from_run`、`delete_process`、`clear_runs` 分别删掉一整次搜索、从某次搜索里去掉一个仓库、只清掉某次搜索的过程记录、清空所有搜索。
"""

import json
import sqlite3

from hunter.history.history_db import _connect, now_str
import hunter.history.repo_memory as repo_store

# 一次搜索一行。results 是这次每个仓库的轻量记录列表（不含 dissection），cost 是阶段花费明细，
# process 是搜索过程元信息（QU 查询、候选池等）
CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    query           TEXT,
    languages       TEXT,
    keypoints       TEXT,
    top_k           INTEGER,
    output_language TEXT,
    total           INTEGER,
    top_name        TEXT,
    cost            TEXT,
    process         TEXT,
    results         TEXT
)
"""

# 存 JSON 的列，读写时统一走 json.dumps / json.loads
JSON_COLS = ("languages", "keypoints", "cost", "process", "results")


def init_runs() -> None:
    """建 runs 表，不存在才建，server 启动时调一次。"""
    conn = _connect()
    try:
        conn.execute(CREATE_RUNS)
        conn.commit()
    finally:
        conn.close()


def _strip_dissection(ranked: list[dict]) -> list[dict]:
    """把每个仓库那份又大又重的分析（`dissection`）先摘掉，再返回这批精简后的结果。

    每次搜索的流水账里不重复抄这份分析，它只在仓库账本里存一份，省地方。
    摘掉的只是分析这一块，仓库名和其它信息都照留；将来翻看这次搜索时（`get_run`），再按仓库名到仓库账本把分析取回来配上。
    """
    return [{k: v for k, v in r.items() if k != "dissection"} for r in ranked]


def save_run(params: dict, ranked: list[dict], total: int, cost: dict,
             process: dict | None = None) -> int:
    """存一次搜索：先插 run 行拿 run_id，再把这批仓库 upsert 进 repo_memory，返回 run_id。

    results 落库前摘掉 dissection（只留 full_name 指向 repo_memory），拆解由 repo_memory 存一份。
    run 行先插好拿到 id，再传给 repo upsert 让它把这次搜索追进各仓库的 seen_runs 时间线，
    保证时间线指得回真实的一次搜索。

    Args:
        params:  含 keypoints、languages、top_k、output_language。
        ranked:  Content Filter 后按命中排序的结果，每项含 full_name、dissection、detail、trace 等。
        total:   仓库总数。
        cost:    这次搜索按阶段汇总的 token 花费。
        process: 搜索过程元信息（QU 查询、编译标准、候选池），传 None 存 NULL。
    Returns:
        新插 run 行的 id。
    """
    ts = now_str()
    keypoints = params.get("keypoints", [])
    top_name = ranked[0]["full_name"] if ranked else ""
    light = _strip_dissection(ranked)

    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO runs (ts, query, languages, keypoints, top_k, output_language, "
            "total, top_name, cost, process, results) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                "；".join(keypoints),
                json.dumps(params.get("languages", []), ensure_ascii=False),
                json.dumps(keypoints, ensure_ascii=False),
                params.get("top_k", 0),
                params.get("output_language", ""),
                total,
                top_name,
                json.dumps(cost, ensure_ascii=False),
                json.dumps(process, ensure_ascii=False) if process is not None else None,
                json.dumps(light, ensure_ascii=False),
            ),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    # run 行落好、拿到 id，再把这批仓库 upsert 进仓库账本，本次搜索追进各自 seen_runs 时间线。
    # 拆解按这次搜索的语言存进包里对应那格，不碰别的语言那格
    repo_store.upsert_repos(ranked, run_id, ts, keypoints, params.get("output_language", "简体中文"))
    return run_id


def list_runs() -> list[dict]:
    """列出所有搜索的摘要，按时间倒序最新在前，不取大块的 results/cost。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, ts, query, languages, keypoints, total, top_name, "
            "(process IS NOT NULL) AS has_process "
            "FROM runs ORDER BY ts DESC"
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


def get_run(qid: int) -> dict | None:
    """取一次完整搜索，results 里的拆解按 full_name 从 repo_memory JOIN 回来拼成完整 ranked。

    仓库已被删掉的，拆解取不到就给空 dict（前端渲染时那块自然空着，等于「拆解已删除」）。
    返回结构跟老 history 的 get_query 一致（ranked/cost/process），前端不用改。
    """
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (qid,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None

    d = dict(row)
    for col in JSON_COLS:
        d[col] = json.loads(d[col]) if d.get(col) else ([] if col != "process" else None)

    # results 只存了仓库名不含拆解，按名字一次取回所有拆解 JOIN 回去，拼成前端认识的完整 ranked。
    # 按这次搜索当时的语言取对应那格拆解，回放的语言跟当时一致
    results = d.pop("results")
    names = [r.get("full_name", "") for r in results if r.get("full_name")]
    diss_map = repo_store.get_dissections(names, d.get("output_language") or "简体中文")
    for r in results:
        r["dissection"] = diss_map.get(r.get("full_name", ""), {})
    d["ranked"] = results
    return d


def delete_repo_from_run(qid: int, full_name: str) -> bool:
    """从一次搜索里删掉一个仓库，改写它的 results，同步更新 total 和 top_name。

    删完这次搜索还在，只是少一个仓库。该条不存在返回 False。仓库账本 repo_memory 不动。
    """
    conn = _connect()
    try:
        row = conn.execute("SELECT results FROM runs WHERE id = ?", (qid,)).fetchone()
        if row is None:
            return False
        results = json.loads(row["results"]) if row["results"] else []
        results = [r for r in results if r.get("full_name") != full_name]
        top_name = results[0]["full_name"] if results else ""
        conn.execute(
            "UPDATE runs SET results = ?, total = ?, top_name = ? WHERE id = ?",
            (json.dumps(results, ensure_ascii=False), len(results), top_name, qid),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_process(qid: int) -> None:
    """只删一次搜索的过程元信息，结果照样留着。把 process 列置回 NULL。"""
    conn = _connect()
    try:
        conn.execute("UPDATE runs SET process = NULL WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()


def delete_run(qid: int) -> None:
    """删掉一整次搜索（仓库账本 repo_memory 不动，各清各的）。"""
    conn = _connect()
    try:
        conn.execute("DELETE FROM runs WHERE id = ?", (qid,))
        conn.commit()
    finally:
        conn.close()


def clear_runs() -> None:
    """清空所有搜索流水账（仓库账本 repo_memory 不动）。"""
    conn = _connect()
    try:
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()
