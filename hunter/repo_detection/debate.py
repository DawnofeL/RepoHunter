"""
辩论取证与裁决：每条 keypoint 先分诊判拆解够不够，不够就按需抠源码，再交正反裁决出终判。

_triage 判拆解够不够判这条 keypoint、不够就开取证清单，_debate_json 发不带工具的强制 JSON
请求（正反立场和裁决共用），_fill_triage/_fill_stance/_fill_adjudicate 填分诊、正反、裁决的
prompt，_kp_with_standard 把判定标准拼进 keypoint。
"""

import json

from hunter.config import call_deepseek, MODELS
from hunter.cost import track, TokenMeter
from hunter.repo_detection.tool_loop import _try_json
from hunter.repo_detection.log_visual import _fmt_react, _fmt_visual


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


def _fill_triage(template: str, keypoint: str, standards: dict[str, str] | None = None) -> str:
    """把分诊提示词的 keypoint 占位填成真值。拆解在 system 里给，这里只填需求。

    Args:
        template:  triage.md 内容。
        keypoint:  单条 keypoint 原文。
        standards: {keypoint 原文: 判定标准} 映射，有标准就拼进 {keypoint} 占位。
    Returns:
        填好的分诊 user 字符串。
    """
    return template.replace("{keypoint}", _kp_with_standard(keypoint, standards))


def _fill_stance(template: str, keypoint: str, stars: int, size: int,
                 evidence: str, output_language: str,
                 standards: dict[str, str] | None = None) -> str:
    """
    把立场提示词(advocate/skeptic 共用占位)的占位填成真值。每个立场只管一条 keypoint。

    拆解已拼进 system，这里不再填 facts；evidence 是分诊判不够时按需从源码抠出的相关片段，
    够判时是空串。用字符串替换不用 str.format，evidence 正文里有花括号会被 format 当占位符。

    Args:
        template:        advocate.md 或 skeptic.md 内容。
        keypoint:        单条 keypoint 原文。
        stars:           仓库 star 数。
        size:            仓库体积 KB，换算成 MB 填进去。
        evidence:        按需抠出的源码片段，空串表示这条够判、没额外抠码。
        output_language: evidence 字段用的语言。
        standards:       {keypoint 原文: 判定标准} 映射，有标准就拼进 {keypoint} 占位。
    Returns:
        填好的立场 user 字符串。
    """
    size_mb = round(size / 1024, 1)
    ev = evidence or "（这一步没有额外抠取源码，凭上面的架构拆解判断即可）"
    return (template
            .replace("{keypoint}", _kp_with_standard(keypoint, standards))
            .replace("{stars}", str(stars))
            .replace("{size}", str(size_mb))
            .replace("{evidence}", ev)
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


async def _triage(system_debate: str, kp_user: str, meter: TokenMeter,
                  model: str | None = None, full_name: str = "", label: str = "",
                  log: str = "Visual", sink: list | None = None) -> dict:
    """
    分诊：判这份拆解够不够判这条 keypoint，不够就返回要抠的源码清单。

    发一次不带工具的 JSON 请求，system 里已带拆解。解析不出、或结构不对一律当「够判」
    （need 空）退回，让格式问题不卡链路；模型说的 sufficient 只作参考，真正要不要取证由
    返回的 need 交给 build_evidence 核验后决定。全部计入 debate 成本阶段。

    Args:
        system_debate: 本仓库预拼的 system + 拆解，所有 debate 调用共享这个前缀。
        kp_user:       填好的分诊 user（含单条 keypoint）。
        meter:         本仓库的 token 累计器。
        model:         模型名，不传用 content_filter 默认。
        full_name:     owner/name，日志用。
        label:         日志标签，如 "分诊[必须是多 agent]"。
        log:           "ReAct" 攒原样消息和回复；"Visual" 攒整理过的摘要。
        sink:          日志收集 list，把格式化文本 append 进去；None 就不攒。
    Returns:
        {"sufficient": bool, "need": list}，解析不出时给 {"sufficient": True, "need": []}。
    """
    resp = await call_deepseek(
        model=model or MODELS["content_filter"],
        messages=[
            {"role": "system", "content": system_debate},
            {"role": "user", "content": kp_user},
        ],
        response_format={"type": "json_object"},
    )
    track("debate", resp)
    meter.add(resp)
    raw = resp.choices[0].message.content or ""
    if sink is not None:
        if log == "ReAct":
            sink.append(_fmt_react(f"{label} LLM 原始回复", raw))
        else:
            sink.append(_fmt_visual(f"{label} 完成", raw))

    # 解析不出或结构不对，退回「够判」不卡链路；need 不是 list 也当空
    parsed = _try_json(raw)
    if not isinstance(parsed, dict):
        return {"sufficient": True, "need": []}
    need = parsed.get("need")
    return {"sufficient": parsed.get("sufficient", True),
            "need": need if isinstance(need, list) else []}
