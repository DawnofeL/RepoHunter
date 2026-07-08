"""落库前的密钥扫描。

提取记忆时对话里万一贴了 API key、token,提取出的 content 可能带进去。落库前用一组
高置信度的前缀正则扫一遍,命中就把那段替换成 [REDACTED],绝不让密钥进库。只收前缀特征
明显、几乎不会误判的规则(通用关键词规则不要,免得把正常文本打码)。Anthropic 前缀在
运行时拼出来,不把字面量写死在文件里。
"""

import re

# 每条是(规则名, 正则)。前缀都很有辨识度,命中基本就是真密钥
SECRET_RULES: list[tuple[str, re.Pattern]] = [
    ("anthropic", re.compile("-".join(["sk", "ant"]) + r"-[A-Za-z0-9\-_]{20,}")),
    ("openai", re.compile(r"sk-(?!ant-)[A-Za-z0-9\-_]{20,}")),
    ("github-pat", re.compile(r"gh[posru]_[A-Za-z0-9]{36,}")),
    ("github-fine-grained-pat", re.compile(r"github_pat_[A-Za-z0-9_]{40,}")),
    ("gitlab-pat", re.compile(r"glpat-[A-Za-z0-9\-_]{20,}")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("stripe-key", re.compile(r"[rs]k_live_[A-Za-z0-9]{20,}")),
    ("huggingface", re.compile(r"hf_[A-Za-z0-9]{20,}")),
    ("npm-token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def scan_and_redact(text: str) -> tuple[str, list[str]]:
    """扫一段文本里的密钥,命中的替换成 [REDACTED],返回(打码后的文本, 命中的规则名列表)。

    没命中就原样返回、列表为空。命中的规则名去重,只报告哪类密钥被打了,不回显密钥本身。

    Args:
        text: 待扫描的文本,通常是一条记忆的 content。
    Returns:
        (redacted, hits) 二元组。redacted 是打码后的文本,hits 是命中的规则名去重列表。
    """
    if not text:
        return text, []

    hits: list[str] = []
    redacted = text
    for name, pattern in SECRET_RULES:
        if pattern.search(redacted):
            hits.append(name)
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, hits
