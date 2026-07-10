"""memory 子包：对话侧记忆的门面，外部统一从这里 import。

S2 对话记录（chat_sessions）和 S3 提取记忆（chat_memories 存储 + extractor 编排）已经落地，
从 chat_history、extract.chat_memories、extract.extractor 真实 re-export。会话笔记（S3.3）还没
做，仍放占位空壳。子模块间的交叉引用各走对方叶子模块、不走门面，免得循环 import。
"""

from hunter.memory.chat_history import (
    init_chat_sessions,
    save_session,
    get_session,
    list_sessions,
    delete_session,
    rename_session,
    set_pinned,
)
from hunter.memory.extract.chat_memories import (
    init_chat_memories,
    apply_actions,
    list_general,
    list_by_repo,
    list_by_session,
    list_manifest,
    get_extract_cursor,
    set_extract_cursor,
)
from hunter.memory.extract.extractor import spawn_extraction
from hunter.memory.compact import (
    init_notes, get_note, spawn_note, list_note_manifest,
    maybe_compact, reset_cursors_after_compact, force_truncate,
    estimate_tokens, COMPACT_THRESHOLD,
)


# —— 会话笔记（S5 笔记层）——

def get_notes(sid: str) -> str:
    """取这次会话的笔记正文，没有就空串。开发者面板展示用，只要正文这一样。"""
    return get_note(sid)["note"]
