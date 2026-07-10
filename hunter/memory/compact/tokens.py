"""
按 DeepSeek 真实 tokenizer 算 token 数的小工具（S5 压缩层底件）。

压缩每一处判断「够不够长、压完超没超线」都问它。分词器文件（tokenizer.json）首次用时从
HuggingFace 现下一份缓存进 data/，之后直接读缓存复用，不重复下载。下载或加载失败就退回
按字符折算的老办法：中文一个字约一个 token，其余约四个字符一个 token，往大了取一点更安全，
不让压缩这一步因为一时拿不到词表就整条卡死。给 compact 和 fallback 量长短用。
"""

import urllib.request
from pathlib import Path

from tokenizers import Tokenizer

# DeepSeek 官方词表，公开托管在 HuggingFace，首次用时下载缓存，之后不再联网
_TOKENIZER_URL = "https://huggingface.co/deepseek-ai/DeepSeek-V3/resolve/main/tokenizer.json"
_TOKENIZER_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "deepseek_tokenizer.json"

# 进程内单例，首次用时建一次；下载或解析失败就标记住，后面每次都直接走字符估算，不重复重试
_tokenizer: Tokenizer | None = None
_tokenizer_failed = False


def _load_tokenizer() -> Tokenizer | None:
    """拿分词器单例，没缓存就现下一份存进 data/。下载或解析失败返回 None，调用方退回字符估算。"""
    global _tokenizer, _tokenizer_failed
    if _tokenizer is not None or _tokenizer_failed:
        return _tokenizer
    try:
        if not _TOKENIZER_PATH.exists():
            _TOKENIZER_PATH.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(_TOKENIZER_URL, _TOKENIZER_PATH)
        _tokenizer = Tokenizer.from_file(str(_TOKENIZER_PATH))
    except Exception:
        _tokenizer_failed = True
    return _tokenizer


def _text_tokens_fallback(text: str) -> int:
    """分词器拿不到时的老办法：中文字一字一 token，其余按四字符一 token 往上取整。"""
    cjk = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            cjk += 1
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _text_tokens(text: str) -> int:
    """估一段文本值多少 token：分词器可用就精确算，不可用退回字符估算。"""
    tokenizer = _load_tokenizer()
    if tokenizer is not None:
        return len(tokenizer.encode(text).ids)
    return _text_tokens_fallback(text)


def estimate_tokens(messages: list[dict]) -> int:
    """估一串对话消息一共值多少 token，逐条把 content 折算加起来。"""
    total = 0
    for m in messages:
        total += _text_tokens(str(m.get("content") or ""))
    return total
