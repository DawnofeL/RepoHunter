"""这个文件管一轮对话怎么跑：用户发一条消息过来，这里拼好要给模型看的完整提示词、把模型的回复一点点吐出来，聊完再在后台把这轮里值得记住的东西悄悄存下。

入口是 `stream_chat`，前端每发一条消息就调一次它。
它不是攒好整段答案才一次性返回，而是像水管一样边收模型吐出来的字边往外传：往外传的是几种标准事件（比如「这段文字来了」「这轮聊完了」「出错了」），这套事件跟具体走什么通道传给前端无关，所以除了网页，以后 notebook、命令行想接也能直接复用同一套逻辑。

发给模型看的完整提示词（代码里叫 `system`）由好几块拼成：助手的人设、跨对话记住的长期信息、用户当前注入的仓库分析和相关事实。
这轮聊完后，会在后台悄悄起一个任务（`spawn_extraction`），不会卡住这轮的回复、就算失败也不影响对话；等攒够几轮，或者用户说了句「记住」，就把这段对话里值得留下的重点挑出来存起来。
"""

import asyncio
import traceback

from hunter.config import call_deepseek, MODELS
from hunter.chat.context import build_segments, segments_to_system
from hunter.memory import (
    spawn_extraction, spawn_note, maybe_compact, reset_cursors_after_compact, force_truncate,
    estimate_tokens, COMPACT_THRESHOLD,
)


# DeepSeek 报上下文超长时消息里常见的词，命中就 reactive 硬截重试一次
_TOO_LONG_MARKS = ("context length", "context_length", "too long", "maximum context", "reduce")


def _looks_too_long(err: Exception) -> bool:
    """看这个异常像不像 DeepSeek 嫌上下文太长，命中就走 reactive 兜底再截一刀。"""
    s = str(err).lower()
    return any(m in s for m in _TOO_LONG_MARKS)


def _clean_payload(system: str, msgs: list[dict]) -> list[dict]:
    """拼发给模型的 payload，顺手剥掉消息里下划线开头的字段（如压缩说明的 _compact_summary）。

    那些字段只给前端监控渲染用，不能进 payload。每条只留 role/content 以及别的正常字段。
    """
    cleaned = [{k: v for k, v in m.items() if not k.startswith("_")} for m in msgs]
    return [{"role": "system", "content": system}] + cleaned


async def stream_chat(messages: list[dict], context: list[str], session_id: str = "",
                      output_language: str = "简体中文"):
    """跑完一轮对话，模型每吐出一点新内容，就立刻往外传一个事件，而不是等全部答完才一次性给结果。

    一开始先传一个「prompt」事件，把这轮发给模型看的完整提示词（人设、注入的仓库分析、对话历史等拼起来的那一整份，按板块拆开）和这轮的消息一起带上，方便前端做"提示词监控"、点开看这轮到底给模型看了什么。
    接下来模型每吐一小段新文字，就传一个「delta」事件（也就是模块开头说的"增量"）。
    这轮正常聊完，传一个「done」事件；中途出错，传一个「error」事件。

    Args:
        messages: 这次对话到目前为止的完整历史，前端传过来，每条消息是"谁说的、说了什么"这样一条记录。
        context: 用户手动点"+"注入的仓库名单，这些仓库的分析和相关事实会被拼进发给模型看的提示词里。
        session_id: 这次会话的编号。这轮聊完会靠它判断该不该在后台把这轮里的重点记下来；传空字符串就只负责回答，不做这个记录动作。这次对话本身存不存、什么时候存，跟这个函数无关，是前端收完这轮回复之后自己另外发一个请求去存的。
        output_language: 界面选的语言（简体中文或英文），决定助手用哪种语言回答，也决定提示词里各种说明文字、以及后台生成的摘要、笔记、记忆用哪种语言写。
    Yields:
        一连串小事件包裹，种类有四种：prompt（这轮完整提示词）、delta（新蹦出来的一小段文字）、done（这轮说完了）、error（出错了）。
    """
    try:

        # 回答前先试着压缩历史（只精简 messages，不碰下面才拼的 system），没到触发线就原样返回不动
        work_msgs, did_compact, compact_path = await maybe_compact(
            session_id, messages, MODELS["extract"], output_language)

        # 拼这轮的 system 提示词、拆成人设和召回记忆等好几个板块，召回要看最近几条消息才能判断用户提到了哪个仓库或记忆
        segments = await build_segments(context, work_msgs, MODELS["recall"], session_id, output_language)
        system = segments_to_system(segments, output_language)

        # 把这轮完整提示词（system 各板块加实际发送的消息）吐给前端做监控，真实占了多少 token 要等模型回完才算得出来、留到下面 done 事件里补
        yield {"type": "prompt", "segments": segments, "messages": work_msgs,
               "compacted": did_compact, "compact_path": compact_path,
               "context_threshold": COMPACT_THRESHOLD}

        # segments 里带 recalled 标记的仓库是这轮被召回自动补进来的，吐一个转正通知让前端在右栏给它补一个常驻的"召回"芯片
        recalled = [s["label"] for s in segments if s.get("kind") == "repo" and s.get("recalled")]
        if recalled:
            yield {"type": "recall", "repos": recalled}

        # 清理一遍要发的消息（剥掉内部专用字段），跟 system 拼成最终发给模型的完整请求体
        payload = _clean_payload(system, work_msgs)
        try:
            stream = await call_deepseek(model=MODELS["chat"], messages=payload, stream=True)
        except Exception as e:

            # 上面已经按估算压过一次历史，但估算终究是估的，服务端仍可能嫌太长报错，命中"太长"关键词就再砍一刀历史重试
            if not _looks_too_long(e):
                raise
            work_msgs = force_truncate(work_msgs, session_id)
            did_compact = True
            payload = _clean_payload(system, work_msgs)
            stream = await call_deepseek(model=MODELS["chat"], messages=payload, stream=True)

        # 攒着流式收到的每一小段文字，等模型说完了拼回完整的一整段回复
        reply_parts = []
        async for chunk in stream:

            # chunk 是 openai 库的流式返回结构（DeepSeek 走这套兼容协议），choices[0].delta.content 是这一小块新增的文字、没有新文字时是 None 要跳过
            if chunk.choices:
                delta = chunk.choices[0].delta.content
                if delta:
                    reply_parts.append(delta)
                    yield {"type": "delta", "text": delta}

        # 把收到的所有增量拼回完整回复，跟这轮发送的消息拼在一起，构成这轮聊完之后的完整历史
        reply = "".join(reply_parts)
        final_msgs = work_msgs + ([{"role": "assistant", "content": reply}] if reply else [])

        # 这轮的请求和回复都写完了占用条的真实 token 数才算得出来，随 done 事件一并给前端，提前算会比实际占用小一截
        yield {"type": "done", "context_tokens": estimate_tokens(final_msgs)}

        if did_compact:

            # 这轮触发了压缩，把压缩后的历史加上这轮回复一起吐给前端替换掉本地存的旧历史，下一轮发送的内容就变小了
            yield {"type": "compacted", "messages": final_msgs}

            # 消息数组因为压缩被重新排过，提取记忆和写笔记原来记的游标位置全部失效，按压缩后的新长度重置这两个游标
            if session_id:
                await asyncio.to_thread(reset_cursors_after_compact, session_id, len(final_msgs))
        elif session_id and reply:

            # 前端要等这轮走完才把 assistant 的回复补进它自己的历史，收到的 messages 参数还没有这轮回复，手动接上凑成完整一轮
            full_messages = messages + [{"role": "assistant", "content": reply}]

            # 分别起一个提取长期记忆、一个续写会话笔记的后台任务，不阻塞这轮回答，各自失败也不连累这轮对话本身
            spawn_extraction(session_id, full_messages, MODELS["extract"], output_language)
            spawn_note(session_id, full_messages, MODELS["extract"], output_language)
    except Exception as e:

        # 先把完整报错堆栈打到服务器日志方便排查，再把异常包成 error 事件传出去让这轮对话体面结束
        traceback.print_exc()
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
