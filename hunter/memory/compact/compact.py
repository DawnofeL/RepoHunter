"""
压缩编排（S5 压缩层总入口）：每次回答前把过长的对话历史换成一份摘要，最近消息原样保留。

maybe_compact 是入口，一进一出：给完整消息列表，返回可能压缩过的列表加一个「压没压」的标记。
到线才压。压缩优先用后台早写好的笔记（零模型），笔记不可用就退 fallback 现写摘要，兜底也连续
失败就硬截最老几轮保命。压缩就是把旧消息删掉、换成一条包着笔记/摘要的说明消息，游标或下限之后
的最近消息原样留着。压缩后消息数组重排、提取和笔记的旧游标失效，reset_cursors_after_compact
把两个游标重置到压缩后长度。tokens 量长短，note 读笔记，fallback 现写摘要。
"""

import asyncio

from hunter.memory.compact.tokens import estimate_tokens
from hunter.memory.compact.note import get_note, set_note_cursor
from hunter.memory.compact.fallback import summarise_fallback
from hunter.memory.extract.chat_memories import set_extract_cursor

# 到这个 token 数就触发压缩。DeepSeek 窗口 128K，设得远比它小：聊天提早压没损失，
# 笔记加保留段足够接住追问，还省钱（历史越长每轮重发越贵）加保质（超长上下文稀释回答）
COMPACT_THRESHOLD = 40_000

# 保留段的下限和上限：不足下限往前多留几条给足近期上下文，到上限封顶防压完还是太大
KEEP_MIN_TOKENS = 4_000
KEEP_MIN_MSGS = 4
KEEP_MAX_TOKENS = 16_000

# 熔断：兜底摘要同一会话连续失败到这个数，就不再试压缩、直接硬截保命
_MAX_FAIL = 2
# 进程内按会话记兜底连续失败次数，压缩成功归零
_fail_count: dict = {}

# 压缩后那条说明消息的话术：告诉模型这是延续、别复述、直接接着聊。用 replace 填笔记，避开花括号。
# 中英各一套，按 output_language 挑，说明消息的语言跟界面语言一致，免得英文对话里插一段中文把模型带偏
_SUMMARY_HEADER = (
    "这段对话从更早的部分延续而来，下面的笔记覆盖了被压缩掉的前半段：\n\n"
    "{summary}\n\n"
    "最近的消息原样保留在后面。直接接着聊，不要跟用户确认这份笔记、不要复述聊过的内容、"
    "不要说「我接着上面」之类的开场，就当对话没断过继续。笔记里没提到的细节别硬编，需要就问用户。"
)
_SUMMARY_HEADER_EN = (
    "This conversation continues from an earlier part. The note below covers the compacted first half:\n\n"
    "{summary}\n\n"
    "The most recent messages are kept as-is after this. Just keep chatting: don't confirm this note "
    "with the user, don't repeat what was already discussed, don't open with anything like "
    "\"continuing from above\". Don't make up details the note doesn't mention; ask the user if needed."
)


def _align_user(messages: list[dict], i: int) -> int:
    """把保留起点往后挪到第一条 user 消息：切在 assistant 中间会让保留段开头像它自说自话。

    往后找不到 user（末尾全是 assistant）就退回原位，宁可 assistant 开头也别把保留段清空。
    """
    j = i
    while j < len(messages) and messages[j].get("role") != "user":
        j += 1
    return j if j < len(messages) else i


def _expand_keep(messages: list[dict], start: int) -> int:
    """算保留起点：从 start 往后到末尾，不足下限就往前多吃几条，到上限停，最后对齐 user 开头。"""
    kept = messages[start:]
    toks = estimate_tokens(kept)
    cnt = len(kept)
    i = start
    while i > 0 and (toks < KEEP_MIN_TOKENS or cnt < KEEP_MIN_MSGS):
        t = estimate_tokens([messages[i - 1]])
        if toks + t > KEEP_MAX_TOKENS:
            break
        i -= 1
        toks += t
        cnt += 1
    return _align_user(messages, i)


def _assemble(summary: str, kept: list[dict], output_language: str = "简体中文") -> list[dict]:
    """拼压缩后的消息：一条包着笔记/摘要的 user 说明消息，后面接原样保留的最近消息。

    说明消息多挂一个 _compact_summary 字段存纯笔记正文，只给前端监控单独渲染成 compact 框用；
    发给模型前 session 那层会把下划线开头的字段剥掉，不会进 payload。
    """
    tmpl = _SUMMARY_HEADER_EN if output_language == "English" else _SUMMARY_HEADER
    header = {"role": "user", "content": tmpl.replace("{summary}", summary),
              "_compact_summary": summary}
    return [header] + kept


def _try_note_compact(messages: list[dict], note: dict, output_language: str = "简体中文") -> list[dict] | None:
    """用笔记压缩，过四道检查：笔记非空、游标在范围、保留段非空、压完低于触发线。任一不过给 None。"""
    text = (note.get("note") or "").strip()
    cursor = note.get("cursor") or 0
    if not text:
        return None
    if not (0 < cursor <= len(messages)):
        return None
    start = _expand_keep(messages, cursor)
    kept = messages[start:]
    if not kept:
        return None
    out = _assemble(text, kept, output_language)
    if estimate_tokens(out) >= COMPACT_THRESHOLD:
        return None
    return out


def _hard_truncate(messages: list[dict]) -> list[dict]:
    """最后保命：不带摘要，硬留最近一段、对齐 user 开头，旧的直接丢。留不出就留最后两条。"""
    start = _expand_keep(messages, len(messages))
    kept = messages[start:]
    return kept if kept else messages[-2:]


def _compact_detail(session_id: str, path: str, before: list[dict], after: list[dict]) -> dict:
    """拼这次压缩的明细：走了哪条路径、压缩前后的完整消息数组、当前熔断计数。

    maybe_compact 顺带把它 return 给对话流，点头像时看得出这轮压了什么，能逐条对照原来几十条
    压成了「一条说明 + 最近几条」的样子。
    """
    return {
        "path": path,
        "before": before,
        "after": after,
        "fail_count": _fail_count.get(session_id, 0),
    }


async def maybe_compact(session_id: str, messages: list[dict], model: str,
                        output_language: str = "简体中文") -> tuple[list[dict], bool, str, dict | None]:
    """回答前试压缩：没到线原样返回；到线先笔记、再兜底、最后硬截。

    返回（可能压过的消息，压没压，走的哪条路径，这次压缩的明细）。path 在没压缩时是空串，压了
    就是那条路径的标识（note_replace / summary_fallback / hard_truncate_circuit /
    hard_truncate_failed）。明细在没压缩时是 None，压了就是压缩前后数组和熔断计数，session 那层
    拿它塞进 prompt 事件和 compact 事件，让点头像时看得出这轮压没压、压了什么。output_language
    决定压缩说明消息和兜底摘要用什么语言写。
    """
    if estimate_tokens(messages) < COMPACT_THRESHOLD:
        return messages, False, "", None

    # 兜底连续失败过多，别再烧钱试，直接硬截让用户能继续发
    if _fail_count.get(session_id, 0) >= _MAX_FAIL:
        out = _hard_truncate(messages)
        detail = _compact_detail(session_id, "hard_truncate_circuit", messages, out)
        return out, True, "hard_truncate_circuit", detail

    note = await asyncio.to_thread(get_note, session_id) if session_id else {"note": "", "cursor": 0}
    kept = _try_note_compact(messages, note, output_language)
    if kept is not None:
        _fail_count.pop(session_id, None)
        detail = _compact_detail(session_id, "note_replace", messages, kept)
        return kept, True, "note_replace", detail

    # 笔记不可用，现写一份摘要兜底
    summary = await summarise_fallback(messages, model, output_language)
    if summary:
        _fail_count.pop(session_id, None)
        start = _expand_keep(messages, len(messages))
        out = _assemble(summary, messages[start:], output_language)
        detail = _compact_detail(session_id, "summary_fallback", messages, out)
        return out, True, "summary_fallback", detail

    # 兜底也失败，计一次熔断，硬截保命
    if session_id:
        _fail_count[session_id] = _fail_count.get(session_id, 0) + 1
    out = _hard_truncate(messages)
    detail = _compact_detail(session_id, "hard_truncate_failed", messages, out)
    return out, True, "hard_truncate_failed", detail


def force_truncate(messages: list[dict], session_id: str = "") -> list[dict]:
    """强制硬截、不看阈值。reactive 用：回答前压过了、模型仍嫌太长时再砍到最近一段。"""
    return _hard_truncate(messages)


def reset_cursors_after_compact(session_id: str, new_len: int) -> None:
    """压缩后消息数组重排、旧下标失效，把提取和笔记两个游标都重置到压缩后长度。

    含义是「压缩后的历史整体当已处理」，下一轮从新消息续，不会重复提取、也不会把笔记说明当对话再压。
    """
    set_extract_cursor(session_id, new_len)
    set_note_cursor(session_id, new_len)
