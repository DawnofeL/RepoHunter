"""
compact 子包（S5）：对话侧的会话压缩，memory 下跟 extract、recall 并列。

分两层：笔记层平时在后台把聊过的压成一份笔记（note），压缩层到长度线时拿笔记顶替旧消息
（compact）。外部从这里 import。
"""

from hunter.memory.compact.note import init_notes, get_note, spawn_note, list_note_manifest
from hunter.memory.compact.compact import (
    maybe_compact, reset_cursors_after_compact, force_truncate,
)

__all__ = [
    "init_notes", "get_note", "spawn_note", "list_note_manifest",
    "maybe_compact", "reset_cursors_after_compact", "force_truncate",
]
