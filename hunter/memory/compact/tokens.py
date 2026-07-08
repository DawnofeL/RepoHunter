"""
按字符估 token 的小工具（S5 压缩层底件）。

压缩每一处判断「够不够长、压完超没超线」都问它。不接真 tokenizer，按字符折算：中文一个字
约一个 token，其余约四个字符一个 token，往大了取一点更安全。估得偏大没关系，触发线离模型
真上限很远，兜得住。给 compact 和 fallback 量长短用。
"""


def _text_tokens(text: str) -> int:
    """估一段文本值多少 token：中文字一字一 token，其余按四字符一 token 往上取整。"""
    cjk = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            cjk += 1
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def estimate_tokens(messages: list[dict]) -> int:
    """估一串对话消息一共值多少 token，逐条把 content 折算加起来。"""
    total = 0
    for m in messages:
        total += _text_tokens(str(m.get("content") or ""))
    return total
