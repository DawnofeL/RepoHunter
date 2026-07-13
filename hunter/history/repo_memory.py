"""管仓库账本（`repo_memory` 表）：一个仓库占一行，存那些不随每次搜索变的仓库知识（仓库账本）。

这里最值钱的是对一个仓库做的分析（代码里叫 `dissection`），也就是又贵又费劲拆出来的那份，全库只在这存一份。
这份分析还按中文、英文分开各存一份，哪种语言搜的就存哪份、互不覆盖。
搜索跑完，这批仓库靠 `upsert_repos` 存进来或更新一下（由流水账那边的 `save_run` 调它）；下次搜索开头用 `load_memories` 拿一批仓库名来查，谁有现成分析就直接复用、跳过重新拆源码那步。
每个仓库还带一条时间线（`seen_runs`），记着它先后被哪几次搜索搜到过，要看某次完整轨迹再去流水账取。
流水账那边不重抄分析、只记仓库名，翻看旧搜索时用 `get_dissections` 按名字把分析取回去配齐。
"""

import json
import sqlite3

from hunter.history.history_db import _connect, now_str

# 拆解语言包默认落哪一格：老数据、没传语言的调用都归中文
DEFAULT_LANG = "简体中文"


def _diss_pack(raw) -> dict:
    """
    把 dissection 列的原始值统一成「语言 -> 拆解」的包。
    """
    if not raw:
        return {}
    data = raw if isinstance(raw, dict) else json.loads(raw)
    if not data:
        return {}
    # 老格式：一份拆解 dict，有 purpose/architecture 这类内容键、不是语言键
    if "purpose" in data or "architecture" in data or "key_designs" in data:
        return {DEFAULT_LANG: data}
    return data


def _diss_for(raw, language: str) -> dict:
    """按语言从拆解包里取对应那格，没有就返回空 dict（当没存过）。"""
    return _diss_pack(raw).get(language or DEFAULT_LANG, {})

# 一仓库一行，主键 full_name。被 gate 跳过的仓库也占一行，dissection 等字段全空、只有 skip_note。
# seen_runs 是这仓库历次被搜到的轻量摘要列表，供审计时间线用
CREATE_REPO_MEMORY = """
CREATE TABLE IF NOT EXISTS repo_memory (
    full_name     TEXT PRIMARY KEY,
    description   TEXT,
    tags          TEXT,
    dissection    TEXT,
    stars         INTEGER,
    size          INTEGER,
    language      TEXT,
    read_files    TEXT,
    seen_runs     TEXT,
    skip_note     TEXT,
    analysed_at   TEXT,
    last_seen     TEXT
)
"""

# 存 JSON 的列，读写时统一走 json.dumps / json.loads
JSON_COLS = ("tags", "dissection", "read_files", "seen_runs")


def init_repo_memory() -> None:
    """建 repo_memory 表，不存在才建，server 启动时调一次。"""
    conn = _connect()
    try:
        conn.execute(CREATE_REPO_MEMORY)
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row, language: str = DEFAULT_LANG) -> dict:
    """把数据库里查出来的一行仓库记录，整理成方便直接用的字典。

    一个仓库对它的分析（代码里叫 `dissection`、项目里管它叫拆解）在库里按中文、英文各存一份，这里按当前搜索用的语言挑出对应那份拿出来。
    要是那门语言的还没存过，就退一步、库里有哪份先用哪份，而不是干脆给空。
    因为聊天时要把它喂给模型，给一份别的语言的也比什么都没有强，模型会自己用当前语言复述。
    在库里存成一整段文字的那几列（`JSON_COLS` 里列的那几个），这里也顺手还原成能直接读的结构。
    """
    d = dict(row)
    for col in JSON_COLS:
        raw = d.get(col)
        if col == "dissection":
            pack = _diss_pack(raw)
            d[col] = pack.get(language) or (next(iter(pack.values()), {}) if pack else {})
        elif raw:
            d[col] = json.loads(raw)
        else:
            d[col] = []
    return d


def load_memories(full_names: list[str], language: str = DEFAULT_LANG) -> dict[str, dict]:
    """给一批 full_name，一次查回来，返回 {full_name: 记忆行}，没记录的不在字典里。

    搜索侧复用用：召回 top k 后拿这些名字来查，命中且有当前语言那格拆解的才算命中、才跳过 explorer；
    只有别的语言那格、没有当前语言的，当没命中（下面 content_filter 会照常重新深挖出这门语言的版本）。
    full_names 为空直接返回空字典，不碰数据库。
    """
    if not full_names:
        return {}
    conn = _connect()
    try:
        # 用 ? 占位拼出 IN (?, ?, ...)，一次查完这批，不循环单查
        marks = ",".join("?" for _ in full_names)
        rows = conn.execute(
            f"SELECT * FROM repo_memory WHERE full_name IN ({marks})", full_names
        ).fetchall()
    finally:
        conn.close()
    # 只返回有当前语言那格拆解的，别的语言那格不算命中
    out = {}
    for r in rows:
        if _diss_for(r["dissection"], language):
            out[r["full_name"]] = _row_to_dict(r, language)
    return out


def upsert_repos(records: list[dict], run_id: int, run_ts: str, keypoints: list[str],
                 language: str = DEFAULT_LANG) -> None:
    """把一批仓库结果 upsert 进仓库账本，本次搜索的摘要追进每个仓库的 seen_runs。

    每条 record 是 explore_one 的结果 dict，取 full_name、dissection、stars、size、
    read_files、keypoint_hits、keypoint_total、skipped、reason、degraded 这些字段。按 full_name
    去重，一仓库一行。拆解按 language 存进包里对应那格，不碰别的语言那格。分四种情况：
      - 表里没有 → 新建，拆解存进 language 那格。被跳过就 dissection 留空包只写 skip_note。
      - 已有且这门语言那格已存过拆解 → 那格不动，只追 seen_runs、更新 last_seen。
      - 已有但这门语言那格还没拆解（新仓库、或只有别的语言）、这次拆了 → 把这门语言那格补上，清 skip_note。
      - 拆解解析失败（degraded）→ 不写拆解，只追 seen_runs、更新 last_seen；新仓库当跳过存一行。

    seen_runs 每条是 {run_id, ts, keypoints, hits, total}：那次搜索 id、时间、需求、这仓库命中几条。
    整个写入包在 try/except 里，写失败只打日志，绝不影响这次搜索的结果。run_id 由 runs 那边先
    插好 run 行拿到再传进来，保证时间线指得回真实的一次搜索。
    """
    try:
        now = now_str()
        conn = _connect()
        try:
            existing = _fetch_existing(conn, [r.get("full_name", "") for r in records])
            for rec in records:
                _save_one(conn, rec, run_id, run_ts, keypoints, now, existing, language)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[repo_memory] 写回失败，跳过不影响搜索：{e}")


def _fetch_existing(conn: sqlite3.Connection, full_names: list[str]) -> dict[str, dict]:
    """要存一批仓库之前，先查一下这批里哪些库里已经有了。

    有了这份「谁已经在、谁还没在」的对照，写回时（`_save_one`）才好判断每个仓库是头一回见要新建，还是早就有了只需更新，以及它之前有没有存过分析。
    为了快，这批一次查完，不一个一个去问。
    库里这批一个都没有，就直接返回空的、连数据库都不碰。
    """
    names = [n for n in full_names if n]
    if not names:
        return {}
    marks = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT full_name, dissection, seen_runs FROM repo_memory WHERE full_name IN ({marks})",
        names,
    ).fetchall()
    return {r["full_name"]: dict(r) for r in rows}


def _run_entry(rec: dict, run_id: int, run_ts: str, keypoints: list[str]) -> dict:
    """给某个仓库记一小条「它在这次搜索里露过面」的备忘。

    这条备忘写清楚是哪一次搜索、什么时候搜的、当时用户提的是什么需求、这个仓库当时对上了几条。
    一个仓库会被很多次搜索搜到，这些备忘攒起来就是它的一条时间线（代码里叫 `seen_runs`），翻看时能看出它历来被谁搜到过。
    """
    return {
        "run_id": run_id,
        "ts": run_ts,
        "keypoints": keypoints,
        "hits": rec.get("keypoint_hits", 0),
        "total": rec.get("keypoint_total", 0),
    }


def _save_one(conn: sqlite3.Connection, rec: dict, run_id: int, run_ts: str,
              keypoints: list[str], now: int, existing: dict[str, dict],
              language: str = DEFAULT_LANG) -> None:
    """把一个仓库的这次结果写回账本，按这次有没有真正分析出东西，分四种情形。

    不论哪种，都先给它添上这次搜索的一条备忘（追进 `seen_runs` 时间线）。
    四种情形是：这个仓库当前语言的分析早存过了，就不动分析、只添备忘；
    这次新分析出了东西，就把当前语言这份分析补上去（真正写库的活交给 `_upsert_full`）；
    这次没分析成（被提前筛掉、或中途出错），若是头回见的新仓库就先占个位、只记一句为什么没分析，若是老仓库就只添备忘。
    没有名字（`full_name`）的记录直接跳过不处理。
    """
    full_name = rec.get("full_name", "")
    if not full_name:
        return

    old = existing.get(full_name)
    # 已有的拆解包，判断这门语言那格有没有存过（别的语言那格不算）
    old_pack = _diss_pack(old.get("dissection")) if old else {}
    has_this_lang = bool(old_pack.get(language))

    # 这次到底有没有拿到可用的拆解：被 gate 跳过、克隆失败降级、解析失败都算没有
    skipped = rec.get("skipped", False)
    degraded = rec.get("degraded", False)
    diss = rec.get("dissection") or {}
    this_run_has_diss = bool(diss) and not skipped and not degraded

    # seen_runs 读旧的接上这次的摘要
    seen = json.loads(old["seen_runs"]) if old and old.get("seen_runs") else []
    seen.append(_run_entry(rec, run_id, run_ts, keypoints))

    # 表里已有且这门语言那格已存过拆解 → 那格不动，只追 seen_runs、更新 last_seen
    if has_this_lang:
        conn.execute(
            "UPDATE repo_memory SET seen_runs = ?, last_seen = ? WHERE full_name = ?",
            (json.dumps(seen, ensure_ascii=False), now, full_name),
        )
        return

    # 到这里这门语言那格还没拆解（新仓库、或只有别的语言、或之前被跳过）
    # 这次拿到了可用拆解 → 把这门语言那格补进包里（新建或补齐都走同一句 upsert，别的语言那格保留）
    if this_run_has_diss:
        _upsert_full(conn, full_name, rec, diss, seen, now, old is None, old_pack, language)
        return

    # 这次也没拆解（又被跳过或降级）→ 不写拆解。新仓库存一行只记 skip_note，老仓库只追摘要
    if old is None:
        conn.execute(
            "INSERT INTO repo_memory (full_name, seen_runs, skip_note, analysed_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (full_name, json.dumps(seen, ensure_ascii=False), rec.get("reason", ""), now, now),
        )
    else:
        conn.execute(
            "UPDATE repo_memory SET seen_runs = ?, last_seen = ? WHERE full_name = ?",
            (json.dumps(seen, ensure_ascii=False), now, full_name),
        )


def _upsert_full(conn: sqlite3.Connection, full_name: str, rec: dict, diss: dict,
                 seen: list, now: int, is_new: bool, old_pack: dict | None = None,
                 language: str = DEFAULT_LANG) -> None:
    """把一个仓库完整的分析（`dissection`）和基本信息真正写进库里，库里没有就新建一行、已经有就更新那一行。

    分析在库里按语言分开存，这次写的是当前语言这份，另一门语言原来那份原样留着、不会被盖掉。
    顺带记下星标数（`stars`）、体积（`size`）这些基本信息，还从分析里摘一句话当简介、把几个关键设计的名字挑出来当标签。
    """

    # description 取 purpose 截 150 字，tags 抄 key_designs 里各项的 name
    purpose = diss.get("purpose", "") or ""
    description = purpose[:150]
    tags = [d.get("name", "") for d in (diss.get("key_designs") or []) if d.get("name")]

    # 把这门语言那格补进包里，别的语言那格不动
    pack = dict(old_pack or {})
    pack[language] = diss
    pack_json = json.dumps(pack, ensure_ascii=False)

    # analysed_at 只在新建时写，补齐老的跳过行时保留它原来的首次日期
    if is_new:
        conn.execute(
            "INSERT INTO repo_memory "
            "(full_name, description, tags, dissection, stars, size, language, "
            "read_files, seen_runs, skip_note, analysed_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (full_name, description, json.dumps(tags, ensure_ascii=False),
             pack_json, rec.get("stars", 0), rec.get("size", 0),
             rec.get("language", ""), json.dumps(rec.get("read_files") or [], ensure_ascii=False),
             json.dumps(seen, ensure_ascii=False), "", now, now),
        )
    else:
        conn.execute(
            "UPDATE repo_memory SET description = ?, tags = ?, dissection = ?, stars = ?, "
            "size = ?, language = ?, read_files = ?, seen_runs = ?, skip_note = '', last_seen = ? "
            "WHERE full_name = ?",
            (description, json.dumps(tags, ensure_ascii=False),
             pack_json, rec.get("stars", 0), rec.get("size", 0),
             rec.get("language", ""), json.dumps(rec.get("read_files") or [], ensure_ascii=False),
             json.dumps(seen, ensure_ascii=False), now, full_name),
        )


def list_memories() -> list[dict]:
    """列出所有仓库的摘要，按最后搜到时间倒序，不取大块的 dissection。给前端列表页用。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT full_name, description, tags, stars, size, language, "
            "skip_note, analysed_at, last_seen, "
            "(dissection IS NOT NULL AND dissection != '') AS has_dissection "
            "FROM repo_memory ORDER BY last_seen DESC"
        ).fetchall()
    finally:
        conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        d["has_dissection"] = bool(d["has_dissection"])
        out.append(d)
    return out


def get_memory(full_name: str, language: str = DEFAULT_LANG) -> dict | None:
    """取一条完整仓库记忆，JSON 列解析回对象，不存在返回 None。给前端详情页用（含 seen_runs 时间线）。

    dissection 按 language 取对应那格，取不到就退到包里有哪份用哪份（聊天注入宁可给别的语言也别给空）。
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM repo_memory WHERE full_name = ?", (full_name,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row, language) if row is not None else None


def get_dissections(full_names: list[str], language: str = DEFAULT_LANG) -> dict[str, dict]:
    """给一批 full_name，只取各自 language 那格的 dissection，返回 {full_name: 拆解 dict}。

    runs 那边取完整一次搜索时，results 里只存了仓库名不含拆解，用这个按名字把拆解 JOIN 回去，
    拼回前端认识的完整 ranked。取不到 language 那格就退到包里有哪份用哪份。仓库已被删掉的不在
    返回里，调用方标「拆解已删除」。
    """
    if not full_names:
        return {}
    conn = _connect()
    try:
        marks = ",".join("?" for _ in full_names)
        rows = conn.execute(
            f"SELECT full_name, dissection FROM repo_memory WHERE full_name IN ({marks})",
            full_names,
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        pack = _diss_pack(r["dissection"])
        out[r["full_name"]] = pack.get(language) or (next(iter(pack.values()), {}) if pack else {})
    return out


def delete_memory(full_name: str) -> None:
    """删掉一条仓库记忆。它旧的 runs 记录会取不到拆解，读时标「拆解已删除」（容忍悬空引用）。"""
    conn = _connect()
    try:
        conn.execute("DELETE FROM repo_memory WHERE full_name = ?", (full_name,))
        conn.commit()
    finally:
        conn.close()


def clear_repos() -> None:
    """清空所有仓库记忆（搜索流水账 runs 不动，各清各的）。"""
    conn = _connect()
    try:
        conn.execute("DELETE FROM repo_memory")
        conn.commit()
    finally:
        conn.close()
