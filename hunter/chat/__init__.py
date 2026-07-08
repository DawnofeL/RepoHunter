"""chat 子包：工作台右栏对话，用 hunter.history 的仓库拆解答题。

`session` 的 stream_chat 拼 system、流式调模型、吐中性事件（不碰传输格式）。`context` 拼 system
（人设 + 注入仓库的拆解）。这是对话的最小版，提取/笔记/召回是 S3 往上的事，这里不做。
外部从这里 import。
"""

from hunter.chat.session import stream_chat

__all__ = ["stream_chat"]
