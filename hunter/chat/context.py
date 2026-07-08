"""
拼对话的 system：llm_skill 人设 + 长期记忆索引 + 注入仓库的拆解和事实 + 按需召回。

用户在结果卡点「+」注入的仓库全名列表传进来，每个仓库去 history 仓库账本取拆解、去 memory
取之前聊过的 repo 类事实，拼成一段接在人设后面。四类通用记忆（user/feedback/project/reference）
不再每轮全量塞正文，只塞一份索引（每条一句摘要加「N 天前」）；正文等这轮用得上，由召回补进来。
召回还负责用户嘴里提到、但没点「+」的仓库：拼 system 前跑一次挑选器（memory.recall），把相关
的仓库拆解、相关记忆的正文补进来，几个仓库分不清就补一段候选清单交给助手反问。没注入没拆过、
挑选器也没挑中的仓库不拼进来。DB 读是同步的，量不大就直接在协程里读，不再单独起线程。

system 拼的时候不揉成一坨，而是拆成带标签的段（`build_segments`）：人设、记忆索引、每个仓库、
召回的记忆、候选清单各一段。发给模型时 `segments_to_system` 拼成整串，分段结构留给提示词监控
（session 每轮吐个 prompt 事件带上，点头像能看到注入了哪些记忆，召回补入的段带 recalled 标记）。
"""

from datetime import datetime

from hunter.config import load_skill
from hunter import history
from hunter import memory
from hunter import dev
from hunter.memory.recall import select_from_manifest


def _days_ago(ts: str) -> str:
    """把可读时间戳换算成「今天/昨天/N 天前」。模型不擅长日期算术，相对天数才触发过时判断。"""
    if not ts:
        return "时间不明"
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "时间不明"
    d = (datetime.now() - t).days
    if d <= 0:
        return "今天"
    if d == 1:
        return "昨天"
    return f"{d} 天前"


def _general_index(rows: list[dict]) -> str:
    """四类通用记忆拼成一份索引，每条一行「[类型] 名字（N 天前）：一句摘要」，不带正文。空返回空串。"""
    if not rows:
        return ""
    lines = ["# 长期记忆索引（跨对话记住的，每条只列一句摘要，相关的正文会另外补上）", ""]
    for r in rows:
        days = _days_ago(r.get("updated_at"))
        lines.append(f"- [{r['type']}] {r['name']}（{days}）：{r.get('description', '')}")
    return "\n".join(lines)


def _repo_block(full_name: str) -> str:
    """拼一个仓库块：元数据（star/体积）+ 拆解 + 之前聊过的事实 + 被搜到的记录。没拆过或没内容的返回空串。"""
    mem = history.get_memory(full_name)
    if not mem:
        return ""
    d = mem.get("dissection") or {}

    # 先拼实质内容（拆解 + 事实），没有就当没这仓库、不注入（被 gate 跳过的仓库 stars/size 是 0 也不塞）
    body: list[str] = []
    if d.get("purpose"):
        body.append(f"用途：{d['purpose']}")
    if d.get("tech_stack"):
        body.append(f"技术栈：{d['tech_stack']}")
    if d.get("architecture"):
        body.append(f"架构：{d['architecture']}")
    designs = d.get("key_designs") or []
    if designs:
        body.append("关键设计：")
        for kd in designs:
            name = kd.get("name", "")
            detail = kd.get("detail", "")
            where = kd.get("where", "")
            line = f"- {name}：{detail}"
            if where:
                line += f"（{where}）"
            body.append(line)
    facts = memory.list_by_repo(full_name)
    if facts:
        body.append("之前聊过的事实：")
        for f in facts:
            anchor = f"（{f['where_anchor']}）" if f.get("where_anchor") else ""
            body.append(f"- {f['content']}{anchor}")
    if not body:
        return ""

    # 有实质内容才拼：标题 + 元数据（star/体积）+ 正文 + 被搜到的记录（seen_runs，不含探查轨迹）
    stars = mem.get("stars") or 0
    size_mb = round((mem.get("size") or 0) / 1024, 1)
    parts = [f"## {full_name}", f"star {stars} · 体积约 {size_mb} MB"]
    parts += body
    seen = mem.get("seen_runs") or []
    if seen:
        parts.append("被搜到的记录：")
        for s in seen:
            kps = " / ".join(s.get("keypoints") or [])
            parts.append(f"- {s.get('ts', '')} 搜「{kps}」，命中 {s.get('hits', 0)}/{s.get('total', 0)}")
    return "\n".join(parts)


def _recall_manifest(generals: list[dict], injected: set,
                     session_id: str = "") -> tuple[list[dict], dict, dict]:
    """组一份给挑选器的混合清单：有拆解没注入的仓库 + 四类通用记忆 + 有笔记的过往会话，各带一句描述。

    返回 (manifest, kind_map, note_meta)：manifest 每条 {name, description} 交给挑选器，kind_map
    记每个 name 是仓库、记忆还是会话笔记，拿回挑中的名字后照它分派。会话笔记条目的 name 是会话 id、
    描述是那次对话的一句简介，note_meta 另存它的标题和时间供补段显示。仓库描述用「一句用途 + tags」，
    已注入的仓库、当前这次会话自己的笔记都不列（已在上下文、或就是本次对话的压缩）。
    """
    manifest: list[dict] = []
    kind_map: dict = {}
    note_meta: dict = {}
    for m in history.list_memories():
        fn = m.get("full_name")
        if not fn or not m.get("has_dissection") or fn in injected:
            continue
        desc = m.get("description") or ""
        tags = m.get("tags") or []
        if tags:
            desc = f"{desc}；{' / '.join(tags)}" if desc else " / ".join(tags)
        manifest.append({"name": fn, "description": desc})
        kind_map[fn] = "repo"
    for r in generals:
        manifest.append({"name": r["name"], "description": r.get("description", "")})
        kind_map[r["name"]] = "memory"
    # 有 session_id 才列会话笔记，排除当前这次会话（它的笔记就是本次对话的压缩、不该召回）
    if session_id:
        for nt in memory.list_note_manifest(session_id):
            sid = nt["session_id"]
            manifest.append({"name": sid, "description": nt["brief"]})
            kind_map[sid] = "note"
            note_meta[sid] = {"title": nt["title"], "updated_at": nt["updated_at"]}
    return manifest, kind_map, note_meta


def _recall_done(segs: list[dict], debug: dict, session_id: str) -> list[dict]:
    """收尾：把召回判断记进 dev 监控，再作为一个不发给模型的段挂末尾，给前端点头像时可视化看。

    recall_debug 段的 kind 不在 segments_to_system 的白名单里，拼 system 时自动跳过、不发给模型；
    它只随 prompt 事件流到前端，让用户点头像就能看到这轮小模型召回判了什么。
    """
    if session_id:
        dev.record(session_id, "recall", debug)
    return segs + [{"label": "召回判断", "kind": "recall_debug", "text": "", "recall": debug}]


async def _recall_segments(generals: list[dict], injected: set, messages: list[dict],
                           model: str, session_id: str = "") -> list[dict]:
    """跑一次召回，把挑中的仓库拆解、相关记忆正文、歧义候选拼成补充段，末尾再挂一段召回判断。

    仓库命中补全量拆解段（标 recalled，前面加一行「N 天前分析」提示可能过时）；记忆命中把正文合成
    「召回的记忆」一段，每条带「N 天前记的」；几个仓库分不清就拼「候选仓库」一段交助手反问。不管
    有没有命中，末尾都挂一个 recall_debug 段带完整判断（清单、模型原始返回、三档挑了谁），既记进
    dev 监控，也随 prompt 事件给前端点头像时看；这段不发给模型。
    """
    manifest, kind_map, note_meta = _recall_manifest(generals, injected, session_id)
    if not manifest:
        return _recall_done([], {"fired": False, "reason": "没有可召回的仓库、记忆或会话笔记"}, session_id)
    result = await select_from_manifest(manifest, messages, model)
    if result.get("skipped"):
        return _recall_done([], {"fired": False, "reason": "消息太短或纯应答，跳过召回"}, session_id)

    gen_by_name = {r["name"]: r for r in generals}
    desc_by_name = {m["name"]: m["description"] for m in manifest}

    segs: list[dict] = []
    mem_lines: list[str] = []
    note_blocks: list[str] = []
    hit_repos: list[str] = []
    hit_memories: list[str] = []
    hit_notes: list[str] = []
    for name in result["picks"]:
        kind = kind_map.get(name)
        if kind == "repo":
            block = _repo_block(name)
            if not block:
                continue
            mem = history.get_memory(name) or {}
            days = _days_ago(mem.get("analysed_at"))
            text = f"（{days}分析的架构快照，可能已更新）\n{block}"
            segs.append({"label": name, "kind": "repo", "text": text, "recalled": True})
            hit_repos.append(name)
        elif kind == "memory":
            r = gen_by_name.get(name)
            if not r:
                continue
            days = _days_ago(r.get("updated_at"))
            mem_lines.append(f"- {r.get('content', '')}（{r['type']}，{days}记的）")
            hit_memories.append(name)
        elif kind == "note":
            body = memory.get_note(name).get("note") or ""
            if not body:
                continue
            meta = note_meta.get(name, {})
            title = meta.get("title") or name
            days = _days_ago(meta.get("updated_at"))
            note_blocks.append(f"## 会话「{title}」（{days}）\n{body}")
            hit_notes.append(name)

    if mem_lines:
        text = "# 召回的记忆（跟这次问题相关，从长期记忆里补出的正文）\n\n" + "\n".join(mem_lines)
        segs.append({"label": "召回的记忆", "kind": "recall_memory", "text": text})

    if note_blocks:
        text = "# 召回的会话笔记（跟这次问题相关的过往对话摘要）\n\n" + "\n\n".join(note_blocks)
        segs.append({"label": "召回的会话笔记", "kind": "recall_note", "text": text})

    hint_names = [n for n in result["ambiguous"] if n in desc_by_name]
    if hint_names:
        lines = [f"- {n}：{desc_by_name[n]}" for n in hint_names]
        text = "# 用户可能提到这些仓库（分不清就反问确认，别猜一个开答）\n\n" + "\n".join(lines)
        segs.append({"label": "候选仓库", "kind": "recall_hint", "text": text})

    debug = {
        "fired": True,
        "manifest": manifest,
        "prompt": result.get("prompt"),
        "raw": result.get("raw", ""),
        "picks": result["picks"],
        "ambiguous": result["ambiguous"],
        "hit_repos": hit_repos,
        "hit_memories": hit_memories,
        "hit_notes": hit_notes,
    }
    return _recall_done(segs, debug, session_id)


async def build_segments(context: list[str], messages: list[dict] | None = None,
                         model: str = "", session_id: str = "") -> list[dict]:
    """把 system 拆成带标签的段：人设、记忆索引、每个注入仓库、召回补入的段。

    每段是 {label, kind, text}：label 是显示名，kind 供前端监控分色，text 是这一块的正文；召回
    补入的仓库段多带一个 recalled=True。人设永远头一段；记忆只塞索引（有就成段）；仓库段只给拆过
    的仓库。传了 messages 和 model 才跑召回（挑用户提到的仓库、相关记忆补进来），不传就退化成只拼
    人设、索引、注入仓库。

    Args:
        context:    用户点「+」注入的 repo 全名列表。
        messages:   这轮的对话历史，跑召回要看最近几条判用户提了什么；不传则不召回。
        model:      召回挑选器用的模型名；不传则不召回。
        session_id: 会话 id，传了就把召回决策记进 dev 监控；空则不记。
    Returns:
        段列表，头一段永远是人设。
    """
    segs = [{"label": "人设", "kind": "persona", "text": load_skill("llm_skill")}]
    generals = memory.list_general()
    idx = _general_index(generals)
    if idx:
        segs.append({"label": "记忆索引", "kind": "memory", "text": idx})
    injected = set(context or [])
    for fn in (context or []):
        block = _repo_block(fn)
        if block:
            segs.append({"label": fn, "kind": "repo", "text": block})
    if messages and model:
        segs += await _recall_segments(generals, injected, messages, model, session_id)
    return segs


def segments_to_system(segments: list[dict]) -> str:
    """把分段拼成发给模型的完整 system 字符串：人设 + 记忆索引 + 仓库 + 召回的记忆 + 候选清单。"""
    persona = next((s["text"] for s in segments if s["kind"] == "persona"), "")
    memory_blk = next((s["text"] for s in segments if s["kind"] == "memory"), "")
    repos = [s["text"] for s in segments if s["kind"] == "repo"]
    recall_mems = [s["text"] for s in segments if s["kind"] == "recall_memory"]
    recall_notes = [s["text"] for s in segments if s["kind"] == "recall_note"]
    hints = [s["text"] for s in segments if s["kind"] == "recall_hint"]
    system = persona
    if memory_blk:
        system += "\n\n" + memory_blk
    if repos:
        system += "\n\n# 当前注入的仓库\n\n" + "\n\n".join(repos)
    if recall_mems:
        system += "\n\n" + "\n\n".join(recall_mems)
    if recall_notes:
        system += "\n\n" + "\n\n".join(recall_notes)
    if hints:
        system += "\n\n" + "\n\n".join(hints)
    return system


async def build_system(context: list[str], messages: list[dict] | None = None,
                       model: str = "", session_id: str = "") -> str:
    """拼这轮对话的完整 system 字符串（人设 + 记忆索引 + 注入仓库 + 召回）。分段版是 build_segments。"""
    return segments_to_system(await build_segments(context, messages, model, session_id))
