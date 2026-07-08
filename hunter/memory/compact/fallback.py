"""
兜底摘要（S5 压缩层）：笔记不可用时现调一次模型把完整历史写成摘要。

笔记还没攒够、或笔记跟当前消息对不上，压缩就退到这条路：把整段对话发给模型现写一份摘要。
照 CC 的做法让模型先在 <analysis> 里理思路、再写 <summary> 正文，注入前把草稿剥掉不占 token。
写摘要失败返回 None，交给 compact 那边硬截保命。提示词在 skills/compact_fallback.md。
"""

import re

from hunter.config import call_deepseek, load_skill


def _extract_summary(raw: str) -> str | None:
    """剥掉 <analysis> 草稿，取 <summary> 正文；没有标签就用整段。空的返回 None。"""
    body = re.sub(r"<analysis>.*?</analysis>", "", raw or "", flags=re.DOTALL)
    m = re.search(r"<summary>(.*?)</summary>", body, re.DOTALL)
    text = (m.group(1) if m else body).strip()
    return text or None


async def summarise_fallback(messages: list[dict], model: str) -> str | None:
    """现调一次模型把完整历史写成摘要，返回摘要正文，调用或解析失败返回 None。"""
    skill = load_skill("compact_fallback")
    convo = "\n\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
    try:
        resp = await call_deepseek(
            model=model,
            messages=[
                {"role": "system", "content": skill},
                {"role": "user", "content": convo},
            ],
        )
    except Exception:
        return None
    raw = resp.choices[0].message.content or ""
    return _extract_summary(raw)
