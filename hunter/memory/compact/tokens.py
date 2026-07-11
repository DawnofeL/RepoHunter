"""
按 DeepSeek 真实 tokenizer 算 token 数的小工具（S5 压缩层底件）。

压缩每一处判断「够不够长、压完超没超线」都问它。分词器文件（tokenizer.json）项目自带一份在
assets/，clone 下来就有、开箱即用不联网。万一自带那份丢了，再退到从 HuggingFace 现下一份缓存
进 data/。两者都拿不到就退回按字符折算的老办法：中文一个字约一个 token，其余约四个字符一个
token，往大了取一点更安全，不让压缩这一步因为一时拿不到词表就整条卡死。给 compact 和 fallback 量长短用。
"""

import urllib.request
from pathlib import Path

from tokenizers import Tokenizer

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
# 项目自带的词表，clone 下来就有，优先读它，开箱即用
_BUNDLED_PATH = _ROOT / "assets" / "deepseek_tokenizer.json"
# 自带那份万一丢了，再从 HuggingFace 现下一份缓存进 data/
_TOKENIZER_URL = "https://huggingface.co/deepseek-ai/DeepSeek-V3/resolve/main/tokenizer.json"
_CACHE_PATH = _ROOT / "data" / "deepseek_tokenizer.json"

# 进程内单例，首次用时建一次；加载失败就标记住，后面每次都直接走字符估算，不重复重试
_tokenizer: Tokenizer | None = None
_tokenizer_failed = False


def _load_tokenizer() -> Tokenizer | None:
    """拿分词器单例：先读项目自带的，没有再读下载缓存，都没有就现下一份。全失败返回 None，调用方退回字符估算。"""
    global _tokenizer, _tokenizer_failed
    if _tokenizer is not None or _tokenizer_failed:
        return _tokenizer
    try:
        if _BUNDLED_PATH.exists():
            path = _BUNDLED_PATH
        else:
            if not _CACHE_PATH.exists():
                _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(_TOKENIZER_URL, _CACHE_PATH)
            path = _CACHE_PATH
        _tokenizer = Tokenizer.from_file(str(path))
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
