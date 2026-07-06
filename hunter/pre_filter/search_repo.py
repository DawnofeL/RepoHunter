"""检索阶段：把 Query Understanding 的查询发给 GitHub 搜成候选池。

本模块只有一个入口 `Search_Repositories`，它把多路查询并发发给 GitHub 仓库搜索，
并集去重成候选池。内部三个闭包各管一段：`explode` 把一条查询转译成 GitHub 不会
静默吞掉的若干条，`search_one` 跑单路搜索并拼上语言、归档、fork 限定，`slim` 把
返回的仓库对象削成后面用得上的几个字段。
"""

import asyncio
import re
import warnings

from mcp import ClientSession

from hunter.clients import call_tool, parse_tool_result
from hunter.pre_filter.repo_languages import normalize_languages


async def Search_Repositories(
    session: ClientSession,
    queries: dict,
    languages: list[str] | None = None,
    archived: bool = False,
    top_k: int = 100,
) -> list[dict]:
    """按 Query_Understanding 给的查询搜 GitHub 仓库，并集去重成候选池。

    GitHub 仓库搜索碰到「带引号短语和别的项 OR 在一起」会静默返回 0，所以搜之前先把每条
    query 规范一遍：剥掉所有引号、去重、合进一个 OR 组，一条 query 只发一路。语言由用户
    单独填，是统一的硬过滤，对所有查询都加同一组 language: 限定。fork 和 archived 默认排除。

    Args:
        session:   mcp_session() 拿到的 MCP 连接对象。
        queries:   Query_Understanding 的输出，形如 {"queries": [{"q"}, ...]}。
        languages: 用户填的语言列表，先经 normalize_languages 纠错归一加耦合，再对每条
                   查询加 language: 过滤，空或 None 不限语言。
        archived:  是否包含已归档仓库，默认 False 排除；fork 也默认排除。
        top_k:     每条查询召回多少，最大 100。规范后每条 query 只发一路，单条 query 时去重后
                   的候选数不超过 top_k；QU 给多条 query 才可能更多（各一路取并集）。
    Returns:
        去重后的仓库列表，每项保留 full_name、description、topics、stars、size。
    """

    # 用户填的语言先纠错归一成规范名，并补上耦合语言（如 Python 带上 Jupyter Notebook）
    languages = normalize_languages(languages)

    def explode(route: dict) -> list[dict]:
        """把一条 query 规范成 GitHub 能正常处理的单条：剥掉引号、去重，合成一个 OR 组。

        模型本该按 skill 不吐引号，这里再兜一道。引号短语和别的项 OR 在一起 GitHub 会静默
        返回 0，所以把每一项的引号都剥掉、合进一条 OR，永远只发一路，去重后数量也天然不超 top_k。
        """
        q = route["q"]

        # 把 in:... 这类尾部修饰符摘出来，拼回去时带上
        m = re.search(r"\s+(in:\S+)", q)
        tail = " " + m.group(1) if m else ""
        core = (q[:m.start()] if m else q).strip().strip("()")

        # 按 OR 拆开，每项剥掉首尾引号、空项丢掉、去重保序
        seen = []
        for p in core.split(" OR "):
            p = p.strip().strip('"').strip()
            if p and p not in seen:
                seen.append(p)
        if not seen:
            return [route]

        # 全部合进一个 OR 组，永远只发一条
        grp = seen[0] if len(seen) == 1 else "(" + " OR ".join(seen) + ")"
        return [{"q": grp + tail}]

    async def search_one(route: dict) -> list[dict]:
        """跑单路搜索，拼上语言、归档、fork 限定，返回削过字段的仓库列表。"""

        query = route["q"]

        # 语言是函数级统一过滤，带空格的语言名要加引号（如 language:"Jupyter Notebook"）
        if languages:
            for lang in languages:
                if " " in lang:
                    query += f' language:"{lang}"'
                else:
                    query += f" language:{lang}"

        query += f" archived:{'true' if archived else 'false'}"
        query += " fork:false"

        # minimal_output 默认 true 不带 size，显式关掉拿完整对象，stars 和 size 才有值
        args = {"query": query, "page": 1, "perPage": top_k, "minimal_output": False}
        res = await call_tool(session, "search_repositories", args)
        data = parse_tool_result(res)

        # 返回可能是带 items 的 dict，也可能直接是 list，两种都兼容
        if isinstance(data, dict):
            items = data.get("items", [])
        else:
            items = data if isinstance(data, list) else []

        # 某路 0 结果多半是查询写法有问题，别再静默吞掉，警告一声好排查
        if not items:
            warnings.warn(f"这条查询 0 结果：{query}")

        def slim(item: dict) -> dict:
            """把 GitHub 返回的仓库对象削成后面用得上的几个字段。"""
            return {
                "full_name":   item.get("full_name"),
                "description": item.get("description"),
                "topics":      item.get("topics", []),
                "stars":       item.get("stargazers_count", 0),
                "size":        item.get("size", 0),
            }

        return [slim(item) for item in items]

    # 先把每条查询转译成若干条，再并发搜，gather 保留顺序
    routes = [r for route in queries.get("queries", []) for r in explode(route)]
    results = await asyncio.gather(*[search_one(r) for r in routes])

    # 按 full_name 并集去重，多路命中同一个仓库只留一份
    pool = {}
    for items in results:
        for item in items:
            pool[item["full_name"]] = item

    return list(pool.values())