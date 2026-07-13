"""GitHub MCP 连接与工具调用封装。

本模块管 GitHub MCP server。`mcp_session` 是异步上下文管理器，
连上 server、握手，把可直接调工具的 session 交出去。`call_tool` 在 session 上调
某个 MCP 工具，带失败重试。`parse_tool_result` 把工具返回统一成 dict 或 list，
方便后面取值。检索阶段就是先用 mcp_session 拿连接，再用 call_tool 搜仓库。
"""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from hunter import config
from hunter.config import MCP_URL


@asynccontextmanager
async def mcp_session():
    """连上 GitHub MCP server，握手，把可以用的 session 交出去。

    用 async with mcp_session() as s 调用，退出 with 块时连接自动断开。

    Yields:
        session: 可以直接调 list_tools() 和 call_tool() 的 MCP ClientSession 对象。
    """

    # PAT 运行时从 config 取，前端注入后这里才拿到真值，不能在 import 时固化
    http_client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {config.GITHUB_PAT}"},
        timeout=30.0,
    )

    # streamable_http_client 和 ClientSession 都来自 mcp 库，前者建传输层、后者是会话层
    async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(session: ClientSession, tool_name: str, args: dict, max_retries: int = 5, delay: float = 5.0):
    """在 MCP session 上调一个工具，失败后等 delay 秒重试，最多重试 max_retries 次。

    Args:
        session:     mcp_session() 拿到的 MCP 会话对象。
        tool_name:   要调的 MCP 工具名，如 "search_repositories"。
        args:        工具参数 dict。
        max_retries: 最多尝试几次，默认 5。
        delay:       每次失败后等几秒再重试，默认 5。
    Returns:
        工具调用的原始返回；连续失败到最后一次仍报错就向上抛。
    """
    for attempt in range(max_retries):
        try:
            return await session.call_tool(tool_name, args)

        # 最后一次还失败就抛出去，不吞错；中间失败就睡一会儿再来
        except Exception:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay)


def parse_tool_result(res):
    """把工具调用的返回统一变成 Python 的 dict 或 list，方便后面取值。

    Args:
        res: call_tool() 的原始返回对象。
    Returns:
        dict 或 list；解析失败时返回原始字符串。
    """

    # MCP 返回带结构化字段时直接用，省去自己解析
    if getattr(res, "structuredContent", None):
        return res.structuredContent

    # 否则把所有 text 类型的内容块拼起来，再当 JSON 解析
    texts = [c.text for c in res.content if getattr(c, "type", None) == "text"]
    raw = "\n".join(texts)

    try:
        return json.loads(raw)
    except Exception:
        return raw