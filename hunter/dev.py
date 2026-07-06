"""开发者监控 recorder：把对话每轮背后的机关过程记进按 session 的环形缓冲，供前端面板看。

对话白盒化用。请求路径(拼 system、压缩)和后台任务(提取、笔记)都往这里 record，是它们和前端
之间的中转，本身不碰 SSE(webapp 层包)。是横切工具，跟 config、cost 一样放 hunter 顶层，chat
和 memory 都能 import 而不成环。

开关默认关，关时 record 直接 no-op、近零成本。缓冲每 session 环形上限，防长会话内存无限涨。
record 全程裹 try/except——监控绝不能弄坏对话。webapp 那层用 subscribe 拿实时流、snapshot 拿现状。
"""

import asyncio
import time
from collections import deque

from hunter import dev_store

# 每个 session 的环形缓冲最多留多少条记录，超了挤掉最老的
_MAX_RECORDS = 40

# 全局开关，默认关。关时 record no-op
_enabled = False
# session_id -> deque[记录]，环形
_buffers: dict[str, deque] = {}
# session_id -> set[asyncio.Queue]，实时订阅者(webapp 的 SSE 端点)
_subscribers: dict[str, set] = {}


def enable() -> None:
    """开开发者监控，之后 record 才真记。"""
    global _enabled
    _enabled = True


def disable() -> None:
    """关开发者监控，record 变 no-op。"""
    global _enabled
    _enabled = False


def is_enabled() -> bool:
    """现在开着没。"""
    return _enabled


def record(session_id: str, kind: str, payload: dict) -> None:
    """记一条监控记录。dev 关着或没 session_id 就直接返回，全程裹错，绝不弄坏对话。

    Args:
        session_id: 这条记录属于哪次会话。
        kind:       记录类型，如 "context" / "extract" / "notes" / "compact"。
        payload:    这条记录的内容 dict，原样并进记录里。
    """
    if not _enabled or not session_id:
        return
    try:
        rec = {"kind": kind, "ts": int(time.time() * 1000), **payload}
        buf = _buffers.setdefault(session_id, deque(maxlen=_MAX_RECORDS))
        buf.append(rec)
        # 推给这个 session 的实时订阅者，队满或出错都不影响记录本身
        for q in list(_subscribers.get(session_id, ())):
            try:
                q.put_nowait(rec)
            except Exception:
                pass
        # 落库审计，重启后仍在。单独裹错，落库失败绝不连累内存记录和实时流
        try:
            dev_store.append(session_id, rec)
        except Exception:
            pass
    except Exception:
        pass


def subscribe(session_id: str) -> asyncio.Queue:
    """订阅一个 session 的实时记录流，返回一个队列，之后 record 的记录都往里塞。webapp SSE 用。"""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(session_id, set()).add(q)
    return q


def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    """退订，SSE 连接断了就调，别让死队列堆着。"""
    subs = _subscribers.get(session_id)
    if subs:
        subs.discard(q)
        if not subs:
            _subscribers.pop(session_id, None)


def snapshot(session_id: str) -> list[dict]:
    """取一个 session 当前缓冲里的全部记录，面板刚打开时先拉一份补齐历史。"""
    return list(_buffers.get(session_id, ()))


def clear(session_id: str | None = None) -> None:
    """清缓冲。给 session_id 清那一个,不给清全部。"""
    if session_id is None:
        _buffers.clear()
    else:
        _buffers.pop(session_id, None)
