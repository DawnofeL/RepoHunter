"""
按分诊开出的清单，从本地克隆里抠出相关源码片段，拼成一份按需证据。

辩论方不再自己带工具翻代码，改由分诊列出要看哪些文件和符号，build_evidence 去克隆目录里
把每个符号周围的一小段源码截出来，核验、限量后拼成文本。清单里指向的路径或符号在仓库里
对不上就丢掉，抠出来的总量有上限，防止一条需求把整份源码搬进上下文。
"""

import os

from hunter.repo_detection.agent_tools import _safe_join


# 一个符号周围截取的行数，前后各这么多行，够看清这个符号干什么就行
CONTEXT_LINES = 60

# 单个片段最多多少字符，再长就截断，防一个大函数吃掉整个预算
PER_CHARS = 6000

# 一次取证所有片段加起来最多多少字符，封住一条需求的取证总量
TOTAL_CHARS = 30000

# 分诊清单最多认几项，多的丢掉，逼分诊挑最相关的
MAX_ITEMS = 6


def _extract_symbol(text: str, symbol: str) -> str:
    """从文件全文里截出 symbol 第一次出现那行的上下文，带行号，搜不到返回空串。

    行号从 1 起、和编辑器一致，方便辩论方在证据里引用具体行。搜不到符号返回空串，
    调用方据此把这项作废。

    Args:
        text:   文件全文。
        symbol: 要定位的类名或函数名。
    Returns:
        带行号的源码片段；符号不在文件里返回空串。
    """
    lines = text.split("\n")

    # 找符号第一次出现在哪一行，找不到就作废
    hit = -1
    for i, line in enumerate(lines):
        if symbol in line:
            hit = i
            break
    if hit < 0:
        return ""

    # 截符号所在行的上下各 CONTEXT_LINES 行
    start = max(hit - CONTEXT_LINES, 0)
    end = min(hit + CONTEXT_LINES + 1, len(lines))
    chunk = lines[start:end]
    numbered = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(chunk))
    return numbered[:PER_CHARS]


def _read_head(text: str) -> str:
    """没给符号、只给了文件路径时，取文件开头一段带行号的内容当证据。"""
    lines = text.split("\n")
    numbered = "\n".join(f"{i + 1}\t{line}" for i, line in enumerate(lines))
    return numbered[:PER_CHARS]


def build_evidence(local_root: str, need: list) -> str:
    """按分诊清单从克隆里抠源码，拼成一份按需证据文本；清单空或全作废返回空串。

    每项是 {"path": 相对路径, "symbol": 符号可空}。路径必须在仓库内且存在、指向文件，
    给了符号就截符号周围、符号搜不到作废，没给符号就取文件开头；越界、不存在、指向目录
    都丢。抠够 TOTAL_CHARS 或认满 MAX_ITEMS 项就停。

    Args:
        local_root: 克隆根目录绝对路径。
        need:       分诊开出的取证清单。
    Returns:
        拼好的证据文本，每段前标了它的来源路径；没有可用片段返回空串。
    """
    if not isinstance(need, list):
        return ""

    blocks = []
    total = 0
    for item in need[:MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        path = (item.get("path") or "").strip()
        symbol = (item.get("symbol") or "").strip()
        if not path:
            continue

        # 路径必须落在仓库内、且真实存在，越界或编造的直接丢
        full = _safe_join(local_root, path)
        if full is None or not os.path.exists(full):
            continue

        # 只处理文件，指向目录的跳过：要看目录内容分诊该点到具体文件
        if not os.path.isfile(full):
            continue

        # 二进制或非 utf-8 用替换符兜底，一个坏文件不该让整条取证崩
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            continue

        # 给了符号截符号周围，搜不到作废；没给符号取文件开头
        seg = _extract_symbol(text, symbol) if symbol else _read_head(text)
        if not seg:
            continue

        loc = f"{path}:{symbol}" if symbol else path
        block = f"### {loc}\n{seg}"
        blocks.append(block)
        total += len(block)
        if total >= TOTAL_CHARS:
            break

    return "\n\n".join(blocks)