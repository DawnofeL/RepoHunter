"""
recall 子包（S4）：对话侧的记忆召回，memory 下跟 extract、compact 并列。

核心是 recall 里的 select_from_manifest，一个通用挑选原语：给一份「名字加描述」的
清单和最近几条对话，挑出跟用户当前消息相关的、标出分不清的。它不认「仓库」也不认「记忆」，
给什么清单挑什么，仓库、记忆、以后的 compact 笔记都走同一个口。外部从这里 import。
"""

from hunter.memory.recall.recall import select_from_manifest

__all__ = ["select_from_manifest"]
