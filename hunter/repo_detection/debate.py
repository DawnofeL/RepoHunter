"""辩论取证与裁决：每条 keypoint 一对正反 worker 带工具取证，裁决者权衡出终判。

_stance_loop 是单个立场 worker 的小号工具循环，_debate_json 发不带工具的强制 JSON 请求（立场
收尾和裁决共用），_fill_stance/_fill_adjudicate 填正反和裁决的 prompt，_kp_with_standard 把
判定标准拼进 keypoint。
"""

import asyncio
import json

from hunter.config import call_deepseek, MODELS
from hunter.cost import track, TokenMeter
from hunter.repo_detection.agent_tools import TOOL_SCHEMAS, make_dispatch
from hunter.repo_detection.tool_loop import (
    _assistant_dict, _run_tool, _guard_dispatch, _try_json, FORCE_STOP_MSG,
)
from hunter.repo_detection.log_visual import _fmt_react, _fmt_visual


# 每个立场 worker 最多调几次工具。worker 只管单条 keypoint,锚点直达打底 4 次够;
# 预算是天花板不是配额,简单条用不满,复合条(一条揉几个意思)才吃得到上限
STANCE_MAX_TOOLS = 4


def _kp_with_standard(keypoint: str, standards: dict[str, str] | None) -> str:
    """把一条 keypoint 拼成「原文（判定标准：...）」的形式，供辩论各方看统一标准。

    standards 是 keypoint 原文到编译出的标准的映射。这条没标准（编译失败、或压根没编译）
    就只回原文，等价于没有 Keypoint_Understanding 这个模块。gate、正反方两处共用这个格式，
    保证判定各方看到的标准表述一致（裁决不拼标准，只权衡双方已在标准下取的证据）。

    Args:
        keypoint:  单条 keypoint 原文。
        standards: {原文: 标准} 映射，None 或查不到就只用原文。
    Returns:
        带标准的 keypoint 文本，或纯原文。
    """
    std = (standards or {}).get(keypoint, "")
    return f"{keypoint}（判定标准：{std}）" if std else keypoint


def _fill_stance(template: str, keypoint: str, facts: dict, stars: int, size: int,
                 output_language: str, standards: dict[str, str] | None = None) -> str:
    """
    把立场提示词(advocate/skeptic 共用占位)的五个占位填成真值。每个立场 worker 只管一条 keypoint。

    facts 是 content 摆出的项目架构拆解，立场 worker 拿它当取证起点。
    用字符串替换不用 str.format，facts 正文里有花括号会被 format 当占位符。

    Args:
        template:        advocate.md 或 skeptic.md 内容。
        keypoint:        单条 keypoint 原文。
        facts:           content 输出的事实 dict。
        stars:           仓库 star 数。
        size:            仓库体积 KB，换算成 MB 填进去。
        output_language: evidence 用的语言。
        standards:       {keypoint 原文: 判定标准} 映射，有标准就拼进 {keypoint} 占位。
    Returns:
        填好的立场 user 字符串。
    """
    size_mb = round(size / 1024, 1)
    return (template
            .replace("{keypoint}", _kp_with_standard(keypoint, standards))
            .replace("{stars}", str(stars))
            .replace("{size}", str(size_mb))
            .replace("{facts}", json.dumps(facts, ensure_ascii=False))
            .replace("{output_language}", output_language))


def _fill_adjudicate(template: str, keypoint: str, stars: int, size: int,
                     adv_case: dict, ske_case: dict, output_language: str) -> str:
    """把裁决提示词的占位填成真值，只裁一条 keypoint，双方论据是过完锚点审计的单条 case。

    {keypoint} 填纯原文不拼标准：keypoint 由代码绑定回结果，裁决只权衡双方已在标准下取的证据。
    """
    size_mb = round(size / 1024, 1)
    return (template
            .replace("{keypoint}", keypoint)
            .replace("{stars}", str(stars))
            .replace("{size}", str(size_mb))
            .replace("{advocate_case}", json.dumps(adv_case, ensure_ascii=False))
            .replace("{skeptic_case}", json.dumps(ske_case, ensure_ascii=False))
            .replace("{output_language}", output_language))


async def _debate_json(messages: list, meter: TokenMeter, model: str | None = None,
                       full_name: str = "", label: str = "", log: str = "Visual",
                       sink: list | None = None) -> dict | None:
    """
    辩论阶段的不带工具 JSON 请求：发一次强制 JSON，坏了修格式重试一次，仍坏返回 None。

    立场 agent 的收尾和裁决共用这个出口，全部计入 debate 成本阶段。日志不实时打，一律攒进
    sink（一个 list），由调用方跑完后统一分块输出，避免正反方并行时日志交叉。

    Args:
        messages:  当前对话历史。
        meter:     本仓库的 token 累计器。
        model:     模型名，不传用 content_filter 默认。
        full_name: owner/name，日志用。
        label:     日志里这次请求的标签，如 "advocate 收尾"、"裁决"。
        log:       "ReAct" 攒原样完整的 messages 和回复；"Visual" 攒整理过的摘要。
        sink:      日志收集 list，把格式化好的文本 append 进去；None 就不攒（不打日志）。
    Returns:
        解析出的 dict；重试后仍解析不出返回 None。
    """
    async def _ask(msgs: list) -> str:
        resp = await call_deepseek(
            model=model or MODELS["content_filter"],
            messages=msgs,
            response_format={"type": "json_object"},
        )
        track("debate", resp)
        meter.add(resp)
        return resp.choices[0].message.content or ""

    if sink is not None and log == "ReAct":
        sink.append(_fmt_react(f"===== {label} 发给 LLM 的消息 =====", messages))
    raw = await _ask(messages)
    if sink is not None:
        if log == "ReAct":
            sink.append(_fmt_react(f"{label} LLM 原始回复", raw))
        else:
            sink.append(_fmt_visual(f"{label} 完成", raw))

    parsed = _try_json(raw)
    if parsed is not None:
        return parsed

    # 坏了让它只改格式重试一次，前缀没变能命中缓存
    retry = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": "输出的 JSON 格式不合法。内容不变，只修复格式，重新输出合法 JSON。"},
    ]
    return _try_json(await _ask(retry))


async def _stance_loop(system: str, stance_user: str, local_root: str,
                       meter: TokenMeter, model: str | None = None,
                       full_name: str = "", side: str = "", log: str = "Visual") -> tuple:
    """
    一个立场 worker 的小号工具循环：只管单条 keypoint，带四件套取证，预算 STANCE_MAX_TOOLS 次，
    收尾出单个 case JSON（{"evidence","where","searched"}，没有 keypoint 字段，由调用方按派发时
    的 keypoint 绑定，对齐由代码保证、不靠模型照抄）。

    system 复用本仓库那份资料页(和 gate/explorer 字节一致，前缀缓存直接命中)，所以照套
    _guard_dispatch 拦掉重复读 README 和根目录。没有锚点重修，立场锚点的核验在外面
    _audit_cases 做，核不上作废即可，不值得打回。

    所有 worker 并行跑，日志实时打会交叉，所以这里不实时打，把每一步日志攒进本地 logs
    （格式化好的文本），连同结果一起返回，由外层 explore_one 跑完后分块统一输出。

    Args:
        system:      本仓库预拼的 system。
        stance_user: 填好的 advocate 或 skeptic user（含单条 keypoint）。
        local_root:  克隆根目录。
        meter:       本仓库的 token 累计器。
        model:       模型名。
        full_name:   owner/name，透传给 _debate_json（仅日志标签用）。
        side:        日志标签，如 "正方[必须是多 agent]"。
        log:         "ReAct" 攒原样完整的消息和回复；"Visual" 攒整理过的摘要。
    Returns:
        (case_dict, logs) 二元组。case_dict 解析不出为 None；logs 是这个 worker 全部日志文本行。
    """
    dispatch = _guard_dispatch(make_dispatch(local_root, set()))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": stance_user},
    ]
    used = 0
    rnd = 0

    # 这一方的日志攒这里，跑完连同 cases 一起返回，不实时打，避免和另一方交叉
    logs: list = []

    while True:
        rnd += 1
        if log == "ReAct":
            logs.append(_fmt_react(f"===== {side} Round {rnd} 发给 LLM 的新增消息 =====", messages[-1]))
        else:
            logs.append(_fmt_visual(f"{side} Round {rnd}"))

        resp = await call_deepseek(
            model=model or MODELS["content_filter"],
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )
        track("debate", resp)
        meter.add(resp)
        msg = resp.choices[0].message

        # 不调工具了，这条回复就是 cases JSON，坏了走 _debate_json 修格式
        if not msg.tool_calls:
            if log == "ReAct":
                logs.append(_fmt_react(f"{side} Round {rnd} LLM 原始回复（无工具调用，即最终 cases）", msg.content))
            else:
                logs.append(_fmt_visual(f"{side} 无工具调用，结论", msg.content))
            parsed = _try_json(msg.content or "")
            if parsed is not None:
                return parsed, logs
            retry = messages + [
                {"role": "assistant", "content": msg.content or ""},
                {"role": "user", "content": "输出的 JSON 格式不合法。内容不变，只修复格式，重新输出合法 JSON。"},
            ]
            fixed = await _debate_json(retry, meter, model, full_name, f"{side} 修格式重试", log, logs)
            return fixed, logs

        # 还要调工具：按预算分配，超额的填占位，套路和 explorer 主循环一致
        assistant_dict = _assistant_dict(msg)
        messages.append(assistant_dict)
        if log == "ReAct":
            logs.append(_fmt_react(f"{side} Round {rnd} LLM 原始回复", assistant_dict))
        else:
            # 模型调工具时顺带说的思考先打出来，别丢，和 explorer 主循环的 💭 一致
            if msg.content:
                logs.append(_fmt_visual(f"{side} 💭", msg.content))
            for tc in msg.tool_calls:
                logs.append(_fmt_visual(f"{side} → {tc.function.name}({tc.function.arguments})"))

        results_msgs: list = [None] * len(msg.tool_calls)
        to_run = []
        for i, tc in enumerate(msg.tool_calls):
            if used < STANCE_MAX_TOOLS:
                used += 1
                to_run.append((i, tc))
            else:
                results_msgs[i] = {"role": "tool", "tool_call_id": tc.id,
                                   "content": "工具预算已用完，请用现有信息收尾"}
        outs = await asyncio.gather(*[_run_tool(dispatch, tc) for _, tc in to_run])
        for (i, tc), out in zip(to_run, outs):
            results_msgs[i] = {"role": "tool", "tool_call_id": tc.id, "content": out}
            if log == "ReAct":
                logs.append(_fmt_react(f"{side} Round {rnd} 工具结果消息", results_msgs[i]))
            else:
                logs.append(_fmt_visual(f"{side} {tc.function.name} 返回", out))
        messages += results_msgs

        # 预算耗尽，追加逼停消息强制出 JSON
        if used >= STANCE_MAX_TOOLS:
            messages.append({"role": "user", "content": FORCE_STOP_MSG})
            forced = await _debate_json(messages, meter, model, full_name, f"{side} 预算耗尽逼停", log, logs)
            return forced, logs
