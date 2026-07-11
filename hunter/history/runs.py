"""runs 表：每次搜索的流水账，一次搜索一行（搜索账本）。

results 存这次每个仓库的轻量记录（判定、轨迹、token、命中数），但不抄 dissection，只留
full_name 指向 repo_memory；点开一次搜索回放时用 get_run 按名字把拆解 JOIN 回来拼成完整 ranked。
save_run 是唯一写入口，一次搞定两张表：先插 run 行拿 run_id，再把这批仓库 upsert 进 repo_memory
（拆解全库只存一份，并把这次搜索追进各仓库的 seen_runs 时间线）。摘要列（query/keypoints/total/
top_name）单独存，列表页只查它们不碰大块的 results。连库走 hunter.history.history_db 的共享 _connect。
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
    # 把每个仓库结果里的 dissection 摘掉再落库，拆解只归 repo_memory 存一份；读时按名字 JOIN 回来
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
