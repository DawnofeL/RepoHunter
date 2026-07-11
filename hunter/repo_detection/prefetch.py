"""预抓：深挖前、克隆之前就要的资料，全走 GitHub API。

fetch_readme 拿 README 原文，clean_readme 洗掉判方向用不上的噪声；fetch_tree 抓根加一级
两层目录树拼成缩进文本，fetch_meta 抓 stars 和 size。这些在预抓阶段跑、克隆之前就要，走 API
不依赖本地克隆。目录条目格式化复用 agent_tools 的 _fmt_line 和 MAX_TREE_ENTRIES。
"""

import asyncio
import re

import httpx

from hunter.repo_detection.agent_tools import _fmt_line, MAX_TREE_ENTRIES


async def fetch_readme(client: httpx.AsyncClient, owner: str, repo: str) -> str:
    """
    抓仓库 README 原文，走 GitHub readme 接口拿 raw 文本，自动识别文件名大小写。

    Args:
        client: 共享的 httpx 异步客户端（带 GitHub PAT）。
        owner:  仓库所有者用户名或 org 名。
        repo:   仓库名。
    Returns:
        README 原始文本，抓不到（非 200）返回空字符串。
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/readme"

    # Accept 头要 raw，让接口直接返回正文而不是带 base64 的 JSON 元数据
    res = await client.get(url, headers={"Accept": "application/vnd.github.raw"})
    if res.status_code != 200:
        return ""
    return res.text


# 标题里整词命中这些关键词就把整段砍掉，不分大小写、整词匹配（Newspaper 不触发 News）
DROP_HEADINGS = re.compile(
    r"^(#+)\s*.*?\b("
    r"news|changelog|change\s*log|release\s*notes|what's\s*new|updates?|"
    r"table\s*of\s*contents|contents|acknowledge?ments?|licen[cs]e|"
    r"citation|cite|bibtex|star\s*history|contribut(?:ing|ors?|ion)|"
    r"sponsors?|roadmap|code\s*of\s*conduct|"
    r"get(?:ting)?\s*started|start(?:ed)?|quick\s?start|"
    r"install(?:ation|ing)?|cli|powershell|api|support|community|security"
    r")\b",
    re.IGNORECASE,
)

# 整条标题就是一个 license 名（可带版本号）也砍掉，防 "## MIT" "## Apache 2.0" 这种裸标题
DROP_LICENSE = re.compile(
    r"^#+\s*(?:mit|apache|l?gpl|agpl|gplv?\d?|bsd|mpl|isc|mozilla|unlicense|cc0|cc-by[\w-]*|boost|zlib)"
    r"(?:[\s-]*v?\d[\w.\s-]*)?\s*$",
    re.IGNORECASE,
)


def drop_sections(md: str) -> str:
    """
    按标题砍掉无关 section，从命中标题一直删到下一个同级或更高级标题。

    Args:
        md: README 原文。
    Returns:
        砍掉无关 section 后的文本。
    """
    lines = md.split("\n")
    out = []

    # skip_level 记当前正在跳过的 section 标题级数，None 表示没在跳过
    skip_level = None

    for line in lines:
        head = re.match(r"^(#+)\s", line)
        level = len(head.group(1)) if head else None

        # 正在跳过时，遇到同级或更高级标题就结束跳过，否则这一行继续丢掉
        if skip_level is not None:
            if level is not None and level <= skip_level:
                skip_level = None
            else:
                continue

        # 标题命中关键词或整条是 license 名，任一成立就从这里开始跳过这一段
        if level is not None and (DROP_HEADINGS.match(line) or DROP_LICENSE.match(line)):
            skip_level = level
            continue

        out.append(line)

    return "\n".join(out)


def clean_readme(md: str, limit: int = 3500) -> str:
    """剥掉 README 里的无关 section、注释、HTML、图片、链接 url、代码块，截断到 limit 字。

    Args:
        md:    README 原文。
        limit: 截断长度，默认 3500 字。
    Returns:
        清洗并截断后的纯介绍文本。
    """

    # 先按标题砍掉 changelog、license 这类整段
    md = drop_sections(md)

    # 去掉 HTML 注释整块
    md = re.sub(r"<!--.*?-->", "", md, flags=re.DOTALL)

    # 去掉所有 HTML 标签，比如 <img>、<div>
    md = re.sub(r"<[^>]+>", "", md)

    # 去掉图片语法 ![alt](url)，整条不留
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)

    # 链接 [文字](url) 只留文字，丢掉占地方又没信息的 url
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)

    # 去掉三反引号围起来的代码块整段
    md = re.sub(r"```.*?```", "", md, flags=re.DOTALL)

    # 把三个以上连续换行压成两个，去掉砍东西留下的大片空白
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md[:limit]


async def _list_dir(client: httpx.AsyncClient, owner: str, name: str, path: str = "") -> list[dict] | None:
    """拉某个目录的 contents 条目列表，path 指向文件或请求失败时返回 None。

    给 fetch_tree 用，只管取数据不管格式化。

    Args:
        client: 共享的 httpx 异步客户端（带 GitHub PAT）。
        owner:  仓库所有者用户名或 org 名。
        name:   仓库名。
        path:   目录相对路径，空字符串列根目录。
    Returns:
        contents API 的条目列表，每项含 name、path、type、size。
        path 指向文件、目录不存在或请求失败一律返回 None。
    """
    url = f"https://api.github.com/repos/{owner}/{name}/contents/{path}"
    res = await client.get(url)
    if res.status_code != 200:
        return None
    data = res.json()
    return data if isinstance(data, list) else None


async def fetch_tree(client: httpx.AsyncClient, owner: str, name: str) -> str:
    """抓根目录加一级子目录的两层结构，拼成带缩进的文本树，喂进 system 给模型当全貌。

    根目录列一层，每个一级子目录并发再列一层，子条目缩进显示。在 Content Filter 预抓阶段跑、克隆
    之前就要，所以走 API，不依赖本地克隆。

    Args:
        client: 共享的 httpx 异步客户端（带 GitHub PAT）。
        owner:  仓库所有者用户名或 org 名。
        name:   仓库名。
    Returns:
        带缩进的两层目录树文本，根目录拿不到时返回空串（由 _fill_system 按语言填占位）。
    """
    root = await _list_dir(client, owner, name, "")
    if root is None:
        return ""

    # 一级子目录并发各列一层，再按 path 对回去
    dirs = [it for it in root if it.get("type") == "dir"]
    subs = await asyncio.gather(*[_list_dir(client, owner, name, d["path"]) for d in dirs])
    sub_map = {d["path"]: s for d, s in zip(dirs, subs)}

    lines = []
    for it in root[:MAX_TREE_ENTRIES]:
        lines.append(_fmt_line(it))
        if it.get("type") == "dir":
            sub = sub_map.get(it["path"])
            if sub:
                for s in sub[:MAX_TREE_ENTRIES]:
                    lines.append(_fmt_line(s, indent=1))
    return "\n".join(lines)


async def fetch_meta(client: httpx.AsyncClient, owner: str, name: str) -> tuple[int, int]:
    """抓仓库的 stars 和 size，size 给模型判体量、stars 给排序和展示。取不到当 0。

    在 Content Filter 预抓阶段跑、克隆之前就要（size 要拼进 system 让模型判大小），所以走 API。

    Args:
        client: 共享的 httpx 异步客户端（带 PAT）。
        owner:  仓库所有者。
        name:   仓库名。
    Returns:
        (stars, size) 二元组，size 单位是 KB；抓不到返回 (0, 0)。
    """
    try:
        res = await client.get(f"https://api.github.com/repos/{owner}/{name}")
        if res.status_code == 200:
            d = res.json()
            return d.get("stargazers_count", 0), d.get("size", 0)
    except Exception:
        pass
    return 0, 0
