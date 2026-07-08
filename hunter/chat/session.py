"""对话主流程：拼 system + 流式调模型，边收边吐中性事件，收完后台提取记忆。

stream_chat 是入口，前端每发一条消息就调一次。它吐的是中性事件 dict（{"type": "delta", ...}
之类），跟传输格式无关，webapp 那层再把中性事件包成 SSE。这样 notebook、CLI 也能直接复用。
system 由人设、跨对话的长期记忆、注入仓库的拆解和事实拼成；收完这轮在后台起个提取任务
（spawn_extraction，不阻塞回答、失败不影响对话），攒够轮数或用户说记住就把重点抠出来存下。
"""

import asyncio
import traceback

from hunter.config import call_deepseek, MODELS
from hunter.chat.context import build_segments, segments_to_system
from hunter.memory import (
    spawn_extraction, spawn_note, maybe_compact, reset_cursors_after_compact, force_truncate,
)


# DeepSeek 报上下文超长时消息里常见的词，命中就 reactive 硬截重试一次
_TOO_LONG_MARKS = ("context length", "context_length", "too long", "maximum context", "reduce")


def _looks_too_long(err: Exception) -> bool:
    """看这个异常像不像 DeepSeek 嫌上下文太长，命中就走 reactive 兜底再截一刀。"""
    s = str(err).lower()
    return any(m in s for m in _TOO_LONG_MARKS)


async def stream_chat(messages: list[dict], context: list[str], session_id: str = ""):
    """流式跑一轮对话，边收 DeepSeek 的增量边 yield 中性事件。

    system 由 llm_skill 人设和注入 repo 的拆解拼成，后面接对话历史。开头先 yield 一个 prompt
    事件带上 system 的分段和这轮消息，给前端提示词监控用；然后逐 chunk yield delta，正常收完
    yield done，出错 yield error。session_id 这一版只透传、不落别的（存对话在前端收完后调
    /chat/session）。

    Args:
        messages:   前端带来的对话历史，每条 {role, content}。
        context:    用户注入的 repo 全名列表，取拆解和事实拼进 system。
        session_id: 会话 id，收完这轮按它触发提取；空串就只答不提。
    Yields:
        中性事件 dict：prompt（这轮完整提示词，分段）/ delta（流式增量）/ done / error。
    """
    try:
        # 回答前先试压缩，只动 messages 不碰 system。到线才压，压缩走 extract 档模型（兜底摘要用它）
        work_msgs, did_compact, compact_path = await maybe_compact(session_id, messages, MODELS["extract"])

        # 分段拼 system：召回要看最近几条消息判用户提了什么，用压缩后的消息一起传进去。
        # DB 读同步、召回是一次便宜调用，量不大就直接 await，不再单独起线程。分段结构随 prompt 事件给监控
        segments = await build_segments(context, work_msgs, MODELS["recall"], session_id)
        system = segments_to_system(segments)
        # 开头先把这轮完整提示词吐给前端监控：system 的分段 + 这轮的消息（压缩后的）。
        # 带上压缩标记，点头像时能看出这轮历史被压过、看到的 messages 是压缩后注入模型的样子
        yield {"type": "prompt", "segments": segments, "messages": work_msgs,
               "compacted": did_compact, "compact_path": compact_path}
        # 召回补进来的仓库（recalled 标记），吐一个转正通知，前端据此在右栏补「召回」芯片、往后常驻
        recalled = [s["label"] for s in segments if s.get("kind") == "repo" and s.get("recalled")]
        if recalled:
            yield {"type": "recall", "repos": recalled}

        # 建流。回答前压过一轮，但估算可能失手，DeepSeek 仍嫌太长就 reactive 再硬截一刀重试一次
        payload = [{"role": "system", "content": system}] + work_msgs
        try:
            stream = await call_deepseek(model=MODELS["chat"], messages=payload, stream=True)
        except Exception as e:
            if not _looks_too_long(e):
                raise
            work_msgs = force_truncate(work_msgs, session_id)
            did_compact = True
            payload = [{"role": "system", "content": system}] + work_msgs
            stream = await call_deepseek(model=MODELS["chat"], messages=payload, stream=True)

        reply_parts = []
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta.content
                if delta:
                    reply_parts.append(delta)
                    yield {"type": "delta", "text": delta}
        yield {"type": "done"}

        reply = "".join(reply_parts)
        if did_compact:
            # 压缩发生：把压缩后历史加本轮回复吐给前端替换本地历史，下轮发的就小了。
            # 消息数组重排、提取和笔记的旧游标失效，重置到压缩后长度；这轮不再 spawn（游标已重置）
            final = work_msgs + ([{"role": "assistant", "content": reply}] if reply else [])
            yield {"type": "compacted", "messages": final}
            if session_id:
                await asyncio.to_thread(reset_cursors_after_compact, session_id, len(final))
        elif session_id and reply:
            # 前端要 finally 里才把 assistant 回复 push 进历史，这轮的回复不在 messages 里。
            # 把刚生成的回复补上凑齐完整一轮，后台起提取和笔记任务；不阻塞回答，失败各自吞掉
            full_messages = messages + [{"role": "assistant", "content": reply}]
            spawn_extraction(session_id, full_messages, MODELS["extract"])
            spawn_note(session_id, full_messages, MODELS["extract"])
    except Exception as e:
        traceback.print_exc()
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
