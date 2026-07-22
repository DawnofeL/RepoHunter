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
from hunter.chat.tools import get_toolbox, MAX_TOOL_CALLS
from hunter.memory import (
    run_extraction, run_note, maybe_compact, reset_cursors_after_compact, force_truncate,
    estimate_tokens, COMPACT_THRESHOLD,
)


# DeepSeek 报上下文超长时消息里常见的词，命中就 reactive 硬截重试一次
_TOO_LONG_MARKS = ("context length", "context_length", "too long", "maximum context", "reduce")


def _looks_too_long(err: Exception) -> bool:
    """看这个异常像不像 DeepSeek 嫌上下文太长，命中就走 reactive 兜底再截一刀。"""
    s = str(err).lower()
    return any(m in s for m in _TOO_LONG_MARKS)


# 撞上限后模型还硬要调工具，最多拒这么多轮就强制收尾，防它犯轴反复空调工具卡死
REFUSAL_CAP = 2

# 撞线拒绝、快到上限提醒、把调用写成文字时的纠正、以及反复漏时兜底给用户的收尾，中英各一套
_BUDGET_ZH = {
    "refuse": "查证次数已用完，不能再调工具了。请基于上面已经查到的材料作答，并说清楚还有哪些没能确认、为什么。",
    "nudge": "查证次数快用完了，还剩 {n} 次。请优先查最关键的地方，准备收口作答。",
    "leak_fix": "上一条回复把工具调用写成了文字，没有真正调用。要么用工具正常调用，要么直接基于已查到的材料作答，不要再把调用写成文字。",
    "leak_net": "抱歉，这一轮查证出了点岔子，没能查全。你可以再问我一次，或者换个说法。",
}
_BUDGET_EN = {
    "refuse": "You've used up your lookup budget and can't call tools any more. Answer from the material already gathered above, and say clearly what's still unconfirmed and why.",
    "nudge": "Your lookup budget is almost gone, {n} left. Check the most important spots first and get ready to wrap up.",
    "leak_fix": "Your last reply wrote a tool call out as text instead of actually calling it. Either call the tool properly, or answer from what you've already gathered. Don't write calls out as text.",
    "leak_net": "Sorry, something went wrong with this round of lookups and I couldn't finish. Try asking again, or rephrase it.",
}


def _budget_texts(output_language: str) -> dict:
    """撞线、提醒、纠正、收尾这几句按对话语言取，English 用英文，其余中文。"""
    return _BUDGET_EN if output_language == "English" else _BUDGET_ZH


def _has_tool_leak(text: str) -> bool:
    """看正文里有没有混进工具调用的特殊 token（模型偶尔把调用写成文字漏出来）。

    DeepSeek 的工具 token 用全角竖线包裹，正常 markdown 不会出现 `<｜` 或连着两个全角竖线。
    """
    return "<｜" in text or "｜｜" in text


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
    有注入仓库时模型挂着四个只读工具，它可以先读几段源码再作答：每执行一次工具就传一个
    「tool」事件带一句活动文案（如「读 xxx.py」），工具结果灌回历史再来一轮，直到它不再调
    工具，那轮的文字就是最终回答。每轮对话的工具次数有上限，到上限强制作答。
    这轮正常聊完，传一个「done」事件；中途出错，传一个「error」事件。

    Args:
        messages: 这次对话到目前为止的完整历史，前端传过来，每条消息是"谁说的、说了什么"这样一条记录。
        context: 用户手动点"+"注入的仓库名单，这些仓库的分析和相关事实会被拼进发给模型看的提示词里。
        session_id: 这次会话的编号。这轮聊完会靠它判断该不该在后台把这轮里的重点记下来；传空字符串就只负责回答，不做这个记录动作。这次对话本身存不存、什么时候存，跟这个函数无关，是前端收完这轮回复之后自己另外发一个请求去存的。
        output_language: 界面选的语言（简体中文或英文），决定助手用哪种语言回答，也决定提示词里各种说明文字、以及后台生成的摘要、笔记、记忆用哪种语言写。
    Yields:
        一连串小事件包裹：prompt（这轮完整提示词）、tool（正在执行的工具活动）、delta（新蹦出来的一小段文字）、done（这轮说完了）、error（出错了）。
    """
    try:

        # 回答前先试着压缩历史（只精简 messages，不碰下面才拼的 system），没到触发线就原样返回不动
        work_msgs, did_compact, compact_path, compact_detail = await maybe_compact(
            session_id, messages, MODELS["extract"], output_language)

        # 拼这轮的 system 提示词、拆成人设和召回记忆等好几个板块，召回要看最近几条消息才能判断用户提到了哪个仓库或记忆
        segments = await build_segments(context, work_msgs, MODELS["recall"], session_id, output_language)

        # 有仓库段（注入或召回补入）就取工具箱，挂四个只读工具让模型能翻这些仓库的真源码；
        # 同一会话跨轮共用一个工具箱，已读缓存和工具日志都在里面；没有仓库就不挂工具
        tool_repos = [s["label"] for s in segments if s.get("kind") == "repo"]
        toolbox = get_toolbox(session_id, tool_repos, output_language) if tool_repos else None

        # 之前几轮查过的代码拼成一段注入 system：结果还在的模型直接引用不重查，超预算被降级
        # 的只留一行指针、需要时限次重读取回。要插在拼 system 和吐 prompt 事件之前，监控才看得到
        if toolbox is not None:
            hist = toolbox.context_block()
            if hist:
                segments.append({"label": toolbox.history_label(), "kind": "tool_history", "text": hist})

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

        async def _open_stream(with_tools: bool):
            """按要不要挂工具发一次流式请求，工具由模型自己决定调不调。"""
            kwargs = {"tools": toolbox.schemas(), "tool_choice": "auto"} if with_tools else {}
            return await call_deepseek(model=MODELS["chat"], messages=payload, stream=True, **kwargs)

        try:
            stream = await _open_stream(toolbox is not None)
        except Exception as e:

            # 上面已经按估算压过一次历史，但估算终究是估的，服务端仍可能嫌太长报错，命中"太长"关键词就再砍一刀历史重试
            if not _looks_too_long(e):
                raise
            work_msgs = force_truncate(work_msgs, session_id)
            did_compact = True
            payload = _clean_payload(system, work_msgs)
            stream = await _open_stream(toolbox is not None)

        # 小循环：模型要调工具就执行完灌回历史再来一轮，不调了这轮的文字就是最终回答。
        # reply_parts 跨轮攒吐给用户的文字，used 计已用的工具次数，refusals 计撞线后还硬调工具的轮数
        bt = _budget_texts(output_language)
        reply_parts = []
        used = 0
        refusals = 0
        warned = False
        while True:

            # 流式收这一轮：文字增量推给前端，工具调用的增量按 index 攒起来等收完拼整。
            # leaked 标记这轮把工具调用写成了文字，一旦检出就不再往前端外显后面的字
            round_parts = []
            calls: dict = {}
            leaked = False
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    round_parts.append(delta.content)
                    if leaked or _has_tool_leak("".join(round_parts)):
                        leaked = True
                    else:
                        reply_parts.append(delta.content)
                        yield {"type": "delta", "text": delta.content}
                # 工具调用是流式分片来的：名字先到、参数串一段段续，按 index 归并到同一个调用上
                for tc in (delta.tool_calls or []):
                    cur = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        cur["id"] = tc.id
                    if tc.function and tc.function.name:
                        cur["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        cur["args"] += tc.function.arguments

            # 这轮把调用写成了文字漏出来（没走正规通道）：外显已经掐了，纠正一句让它下轮好好说话；
            # 反复漏就兜底给用户一句收尾，别留个空泡
            if leaked and not calls:
                refusals += 1
                if refusals >= REFUSAL_CAP:
                    if not reply_parts:
                        yield {"type": "delta", "text": bt["leak_net"]}
                        reply_parts.append(bt["leak_net"])
                    break
                payload.append({"role": "assistant", "content": "".join(round_parts)})
                payload.append({"role": "user", "content": bt["leak_fix"]})
                stream = await _open_stream(toolbox is not None)
                continue

            # 这一轮没有工具调用，刚收的文字就是最终回答，跳出循环走收尾
            if not calls:
                break

            # 模型的工具调用请求原样进历史（格式必须合法，下一轮请求才认）
            ordered = [calls[i] for i in sorted(calls)]
            payload.append({"role": "assistant", "content": "".join(round_parts),
                            "tool_calls": [{"id": c["id"], "type": "function",
                                            "function": {"name": c["name"], "arguments": c["args"]}}
                                           for c in ordered]})

            # 次数已用满：这轮的调用全部拒绝执行，回一句拒绝话术逼它作答；拒够 REFUSAL_CAP 轮就强制收尾。
            # 工具通道故意不撤，撤了模型会退回把调用写成文字漏给用户，靠这里的拒绝来控次数
            if used >= MAX_TOOL_CALLS:
                refusals += 1
                for c in ordered:
                    payload.append({"role": "tool", "tool_call_id": c["id"], "content": bt["refuse"]})
                if refusals >= REFUSAL_CAP:
                    # 拒到上限还只调工具、一个字没说，兜底给用户一句收尾别留空泡
                    if not reply_parts:
                        yield {"type": "delta", "text": bt["leak_net"]}
                        reply_parts.append(bt["leak_net"])
                    break
                stream = await _open_stream(toolbox is not None)
                continue

            # 没撞线：逐个执行
            for c in ordered:

                # 目标仓库本地还没克隆会现场浅克隆（要几秒），先吐一条活动提示别让用户干等
                repo_to_clone = toolbox.needs_clone(c["args"])
                if repo_to_clone:
                    yield {"type": "tool", "text": toolbox.describe_clone(repo_to_clone)}
                yield {"type": "tool", "text": toolbox.describe(c["name"], c["args"])}
                result = await toolbox.exec(c["name"], c["args"])
                # 执行完再吐一条审计事件：工具名 + 入参 + 结果预览，前端折叠成可查的审计行
                yield {"type": "tool_io", **toolbox.audit(c["name"], c["args"], result)}
                payload.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                used += 1

            # 软着陆：刚跨进「快用完」的区间，往最后一条工具结果上贴一句提醒让它准备收口，只贴一次
            if not warned and MAX_TOOL_CALLS - 2 <= used < MAX_TOOL_CALLS:
                payload[-1]["content"] += "\n\n" + bt["nudge"].format(n=MAX_TOOL_CALLS - used)
                warned = True

            # 工具一直挂着，下一轮接着来
            stream = await _open_stream(toolbox is not None)

        # 把收到的所有增量拼回完整回复，跟这轮发送的消息拼在一起，构成这轮聊完之后的完整历史
        reply = "".join(reply_parts)
        final_msgs = work_msgs + ([{"role": "assistant", "content": reply}] if reply else [])

        # 这轮的请求和回复都写完了占用条的真实 token 数才算得出来，随 done 事件一并给前端，提前算会比实际占用小一截
        yield {"type": "done", "context_tokens": estimate_tokens(final_msgs)}

        if did_compact:

            # 这轮触发了压缩，把压缩后的历史加上这轮回复一起吐给前端替换掉本地存的旧历史，下一轮发送的内容就变小了
            yield {"type": "compacted", "messages": final_msgs}

            # 压缩明细（走哪条路径、压缩前后、熔断计数）跟着一条 compact 事件发出去，前端挂到这轮气泡，点头像看得到
            if compact_detail:
                yield {"type": "compact", **compact_detail}

            # 消息数组因为压缩被重新排过，提取记忆和写笔记原来记的游标位置全部失效，按压缩后的新长度重置这两个游标
            if session_id:
                await asyncio.to_thread(reset_cursors_after_compact, session_id, len(final_msgs))
        elif session_id and reply:

            # 前端要等这轮走完才把 assistant 的回复补进它自己的历史，收到的 messages 参数还没有这轮回复，手动接上凑成完整一轮
            full_messages = messages + [{"role": "assistant", "content": reply}]

            # 提取长期记忆和续写会话笔记并发跑，跑完把各自过程作为 extract / notes 事件顺着同一条流发出去。
            # done 已经吐过、回答早就到齐，这里只是让流多开几秒收这两件后台事的结果，各自失败吞掉不连累对话
            ex_info, note_info = await asyncio.gather(
                run_extraction(session_id, full_messages, MODELS["extract"], output_language),
                run_note(session_id, full_messages, MODELS["extract"], output_language),
            )
            if ex_info:
                yield {"type": "extract", **ex_info}
            if note_info:
                yield {"type": "notes", **note_info}
    except Exception as e:

        # 先把完整报错堆栈打到服务器日志方便排查，再把异常包成 error 事件传出去让这轮对话体面结束
        traceback.print_exc()
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
