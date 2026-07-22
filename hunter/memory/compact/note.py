"""
会话笔记(session note)：后台把聊过的内容压成一份笔记存库，供压缩时零成本复用（S5 笔记层）。

每聊完一轮在后台检查，游标之后的新消息估出来的 token 数攒够阈值，就拿旧笔记加新消息重写
整份笔记，连同游标和一句简介成对落库。笔记覆盖到第几条由游标记着，压缩时游标之前的旧消息
可被这份笔记顶替。按 token 量触发（不是数条数）、单飞补跑、失败不碰对话，全套照 extract
那边的做法。spawn_note 是入口，对话收完一轮调它。笔记三列加在 S2 的 chat_sessions 上，连库
走共享 memory_db。
"""

import asyncio
import json
import traceback

from hunter.config import call_deepseek, load_skill
from hunter.memory.memory_db import _connect
from hunter.memory.compact.tokens import estimate_tokens

# 游标之后的新消息估出来的 token 数攒够这个数才重写一次笔记，不够就跳过继续攒，
# 闲聊攒很久也攒不到就不触发，内容密的话几条就够，不用凑够特定条数
NOTE_TOKEN_THRESHOLD = 1_500

# 笔记后台任务的单飞状态：同时只跑一个，跑着时新触发压进 pending，跑完补一次。进程内驻留跨请求
_note_running = False
_note_pending: tuple | None = None
# 后台任务引用，防止 create_task 出来的任务被 GC 掉
_bg_tasks: set = set()
# await 版 run_note 用的锁：多轮并发时把笔记重写串起来跑，别两个同时写库挪游标打架
_run_lock = asyncio.Lock()


def init_notes() -> None:
    """给 chat_sessions 补三列笔记字段，不存在才加。server 启动、排在 init_chat_sessions 之后调。

    note 存笔记正文，note_cursor 记笔记覆盖到第几条消息，note_brief 存一句话简介（留给 S4 召回清单用）。
    """
    conn = _connect()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()]
        if "note" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN note TEXT")
        if "note_cursor" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN note_cursor INTEGER DEFAULT 0")
        if "note_brief" not in cols:
            conn.execute("ALTER TABLE chat_sessions ADD COLUMN note_brief TEXT")
        conn.commit()
    finally:
        conn.close()


def get_note(session_id: str) -> dict:
    """读这次会话的笔记，返回 {note, brief, cursor}，没有就都给空/0。压缩层和门面用。"""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT note, note_brief, note_cursor FROM chat_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"note": "", "brief": "", "cursor": 0}
    return {
        "note": row["note"] or "",
        "brief": row["note_brief"] or "",
        "cursor": row["note_cursor"] if row["note_cursor"] is not None else 0,
    }


def list_note_manifest(exclude_session_id: str = "") -> list[dict]:
    """列出有笔记的会话，给 S4 召回清单当第三类条目。每条 {session_id, title, brief, updated_at}。

    只列笔记正文和简介都非空的（能召回正文、也有一句描述供语义匹配）。当前会话自己的笔记是
    这次对话的压缩、不该召回，用 exclude_session_id 排除掉。
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT session_id, title, note_brief, updated_at FROM chat_sessions "
            "WHERE note IS NOT NULL AND note != '' AND note_brief IS NOT NULL AND note_brief != '' "
            "AND session_id != ?",
            (exclude_session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [{"session_id": r["session_id"], "title": r["title"] or "",
             "brief": r["note_brief"], "updated_at": r["updated_at"]} for r in rows]


def set_note_cursor(session_id: str, cursor: int) -> None:
    """只挪笔记游标，正文和简介不动。压缩后消息重排、旧游标失效时重置用。upsert 免得行不存在。"""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, note_cursor) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET note_cursor = excluded.note_cursor",
            (session_id, cursor),
        )
        conn.commit()
    finally:
        conn.close()


def _save_note(session_id: str, note: str, brief: str, cursor: int) -> None:
    """把笔记正文、简介、游标成对写库，upsert。三样一起落，压缩时读到的永远配套。

    session 行可能还没被 save_session 建出来（第一轮就触发），用 upsert 先建一行把笔记存住，
    后面 save_session 会补齐 title/messages 那几列。
    """
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO chat_sessions (session_id, note, note_brief, note_cursor) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "note = excluded.note, note_brief = excluded.note_brief, note_cursor = excluded.note_cursor",
            (session_id, note, brief, cursor),
        )
        conn.commit()
    finally:
        conn.close()


def _parse_note(raw: str) -> dict | None:
    """解析笔记模型的输出 JSON，取 note 和 brief，坏了或没写出正文返回 None。"""
    raw = (raw or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("note"):
        return {"note": data.get("note", ""), "brief": data.get("brief", "")}
    return None


async def _run_note(session_id: str, messages: list[dict], model: str,
                    output_language: str = "简体中文") -> dict:
    """跑一次笔记重写：读游标取新消息，够阈值就拿旧笔记加新消息重写整份，成功才挪游标。

    每次是重写整份不是往后接，输入旧笔记加游标之后的新消息，产出新的完整笔记。写库成功才把
    游标推到最新，失败游标不动、下次从老位置重来不漏。整个过程作为 payload 返回给对话流。
    笔记按 output_language 写；整份重写，会话中途切语言的话下次重写自然换过来。
    """
    prev = await asyncio.to_thread(get_note, session_id)
    cursor = prev["cursor"]
    new_msgs = messages[cursor:]
    new_tokens = estimate_tokens(new_msgs)
    if new_tokens < NOTE_TOKEN_THRESHOLD:
        # 不够阈值这轮不写笔记，把原因返回给对话流，点头像时看得到
        return {
            "fired": False,
            "reason": f"游标 {cursor}，新增 {len(new_msgs)} 条约 {new_tokens} tok < {NOTE_TOKEN_THRESHOLD}",
        }

    none_note = "(no note yet)" if output_language == "English" else "（还没有笔记）"
    skill = (load_skill("session_note")
             .replace("{old_note}", prev["note"] or none_note)
             .replace("{output_language}", output_language))
    convo = "\n\n".join(f"{m.get('role')}: {m.get('content')}" for m in new_msgs)

    resp = await call_deepseek(
        model=model,
        messages=[
            {"role": "system", "content": skill},
            {"role": "user", "content": convo},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or ""
    parsed = _parse_note(raw)
    if parsed is None:
        # 解析坏了这轮不落库、游标不动，下次重来。当作没写成，把原因返回给对话流
        return {"fired": False, "reason": "笔记模型输出解析失败", "raw": raw}

    await asyncio.to_thread(_save_note, session_id, parsed["note"], parsed["brief"], len(messages))
    print(f"[note] session={session_id} 笔记已更新，游标 {cursor} -> {len(messages)}")
    return {
        "fired": True,
        "notes_cursor": len(messages),
        "notes_tokens": estimate_tokens([{"content": parsed["note"]}]),
        "notes": parsed["note"],
        "prompt": [{"role": "system", "content": skill}, {"role": "user", "content": convo}],
        "raw": raw,
        "brief": parsed["brief"],
    }


async def _safe_note(session_id: str, messages: list[dict], model: str,
                     output_language: str = "简体中文") -> None:
    """包一层 try/except，写笔记失败只打日志绝不影响对话，游标也不动、下次重来。"""
    try:
        await _run_note(session_id, messages, model, output_language)
    except Exception:
        traceback.print_exc()


async def _note_single_flight(session_id: str, messages: list[dict], model: str,
                              output_language: str = "简体中文") -> None:
    """单飞：同时只跑一个笔记任务，跑着时新触发压进 pending 覆盖旧的，跑完拿最新的补一次。"""
    global _note_running, _note_pending
    if _note_running:
        _note_pending = (session_id, messages, model, output_language)
        return
    _note_running = True
    try:
        await _safe_note(session_id, messages, model, output_language)
        while _note_pending is not None:
            job = _note_pending
            _note_pending = None
            await _safe_note(*job)
    finally:
        _note_running = False


def spawn_note(session_id: str, messages: list[dict], model: str,
               output_language: str = "简体中文") -> None:
    """一轮聊完起个后台任务重写笔记，不阻塞回答。任务引用存进 _bg_tasks 防被 GC。"""
    if not session_id or not messages:
        return
    task = asyncio.create_task(
        _note_single_flight(session_id, list(messages), model, output_language))
    _bg_tasks.add(task)


async def run_note(session_id: str, messages: list[dict], model: str,
                   output_language: str = "简体中文") -> dict | None:
    """awaited 版：跑一次笔记重写，返回过程 payload 交给对话流 yield 给前端，失败吞掉返回 None。

    对话那边在 done 之后 await 它，把返回的 payload 作为 notes 事件顺着同一条流发出去，前端挂到
    当轮气泡、点头像时看得到。失败只打日志，绝不连累这轮对话。
    """
    if not session_id or not messages:
        return None
    try:
        # 锁住，保证同一时刻只有一个笔记重写在写库，多轮并发也串行执行不撞车
        async with _run_lock:
            return await _run_note(session_id, list(messages), model, output_language)
    except Exception:
        traceback.print_exc()
        return None
    task.add_done_callback(_bg_tasks.discard)
