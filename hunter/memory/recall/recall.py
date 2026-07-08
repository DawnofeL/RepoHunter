"""
记忆召回的挑选器：给一份清单和最近几条对话，挑出相关的、标出分不清的（S4）。

select_from_manifest 是通用挑选原语，不认仓库也不认记忆，只认 {name, description} 清单。
每轮对话前跑一次：先过跳过门（消息太短或纯应答就不调模型、省钱），再把清单和最近几条消息发一次
便宜调用让模型挑，拿回的名字对清单核一遍、编造的丢掉，调用或解析失败一律当空结果。召回是锦上
添花，绝不因为它出错弄坏对话。context 那边把仓库和记忆拼成一份清单喂进来、拿回结果自己分派。
"""

import json
import re

from hunter.config import call_deepseek, load_skill

# 消息去掉首尾空白短于这个字数就不召回，太短没法判用户指什么
MIN_CHARS = 4

# 单独成句的纯应答词不触发召回，省一次调用
ACK_WORDS = {"嗯", "嗯嗯", "好", "好的", "行", "对", "是", "哦", "ok", "OK", "谢谢", "收到"}

# 挑中的最多几个、分不清的候选最多几个，防模型一次吐太多
MAX_PICKS = 5
MAX_AMBIGUOUS = 5

# 挑选器只看最近这几条消息，不看整段历史，接住「再展开讲讲」这类靠上下文的追问
RECENT_TURNS = 3


def _last_user_msg(messages: list[dict]) -> str:
    """取最近一条 user 消息的正文去掉首尾空白，没有就返回空串。跳过门按它判。"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return (msg.get("content") or "").strip()
    return ""


def _should_skip(text: str) -> bool:
    """跳过门：消息太短或整句就是个应答词，不召回。"""
    if len(text) < MIN_CHARS:
        return True
    return text in ACK_WORDS


def _fmt_manifest(manifest: list[dict]) -> str:
    """把清单拼成文本填进提示词，一行一条「name：description」。"""
    return "\n".join(f"- {m['name']}：{m.get('description', '')}" for m in manifest)


def _fmt_messages(messages: list[dict]) -> str:
    """把最近几条对话拼成文本，让模型看上下文、不只看最后一句。"""
    recent = messages[-RECENT_TURNS:]
    return "\n".join(f"{m.get('role')}: {m.get('content')}" for m in recent)


def _parse(raw: str) -> dict:
    """解析模型返回的 {"picks": [...], "ambiguous": [...]}，坏了抠花括号，再不行返回空。"""
    raw = (raw or "").strip()
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        return {"picks": [], "ambiguous": []}
    picks = data.get("picks")
    ambiguous = data.get("ambiguous")
    return {
        "picks": picks if isinstance(picks, list) else [],
        "ambiguous": ambiguous if isinstance(ambiguous, list) else [],
    }


async def select_from_manifest(
    manifest: list[dict],
    messages: list[dict],
    model: str,
    limit: int = MAX_PICKS,
) -> dict:
    """从清单里挑跟用户最近消息相关的条目，返回 {"picks": [...], "ambiguous": [...]}。

    picks 是有把握相关的 name，ambiguous 是几个都沾边、分不清用户指哪个的 name，都只含
    清单里真有的名字。先过跳过门（消息太短或纯应答直接返回空、不调模型）；再把清单和最近几条
    消息发一次调用、强制 JSON；拿回的名字对清单核验，编造的丢掉，picks 截到 limit；调用或
    解析出错一律返回空。它不区分条目是仓库还是记忆，那是调用方的事。

    返回里还带 prompt（发给小模型的完整 system + user 两条消息）、raw（模型原始返回）、
    skipped（是否被跳过门拦下没调模型），给开发者监控看，调用方拼段只用 picks 和 ambiguous。

    Args:
        manifest: 待挑清单，每条 {name, description}。空清单直接返回空。
        messages: 最近几条对话 {role, content}，跳过门按最后一条 user 消息判。
        model:    挑选用的模型名。
        limit:    picks 上限。
    Returns:
        {"picks": [...], "ambiguous": [...], "raw": str, "skipped": bool}，名字都在清单里。
    """
    if not manifest or _should_skip(_last_user_msg(messages)):
        return {"picks": [], "ambiguous": [], "raw": "", "skipped": True, "prompt": None}

    skill = load_skill("recall_memories")
    user = f"最近的对话：\n{_fmt_messages(messages)}\n\n可选清单：\n{_fmt_manifest(manifest)}"
    call_messages = [
        {"role": "system", "content": skill},
        {"role": "user", "content": user},
    ]
    try:
        resp = await call_deepseek(
            model=model,
            messages=call_messages,
            response_format={"type": "json_object"},
        )
    except Exception:
        return {"picks": [], "ambiguous": [], "raw": "", "skipped": False, "prompt": call_messages}
    raw = resp.choices[0].message.content or ""
    result = _parse(raw)

    # 只留清单里真有的名字，编出来的丢掉；picks 截上限、ambiguous 也封顶
    names = {m["name"] for m in manifest}
    picks = [n for n in result["picks"] if n in names][:limit]
    ambiguous = [n for n in result["ambiguous"] if n in names][:MAX_AMBIGUOUS]
    return {"picks": picks, "ambiguous": ambiguous, "raw": raw, "skipped": False, "prompt": call_messages}
