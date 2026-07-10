"""explorer 和 debate 共用的工具循环底件。

_try_json 解析模型输出的 JSON 带兜底，_run_tool 执行单个工具调用并兜错，_assistant_dict
把模型回复转成存历史的格式，_guard_dispatch 给调度表套一层拦截挡掉重复读 README 和根目录。
FORCE_STOP_MSG 是工具预算用满时追加的固定逼停消息。
"""

import json
import re

from hunter.repo_detection.agent_tools import is_archived_read


# 工具预算用满时追加的逼停消息，固定字符串不带变量，每次内容一样才能命中缓存
FORCE_STOP_MSG = "系统提示:工具预算已用完，不要再调用任何工具，立即用现在掌握的信息，按规定格式输出最终 JSON。"


def _assistant_dict(msg) -> dict:
    """
    把模型回复转成存进对话历史的 dict，有工具调用时把调用信息原样带上。

    格式必须和模型原始回复完全一致，否则下次请求会因为历史不合法而报错。

    Args:
        msg: deepseek 回复里的 message 对象。
    Returns:
        含 role、content 的 dict，有工具调用时还带一个 tool_calls 列表。
    """
    d = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return d


def _try_json(raw: str) -> dict | None:
    """
    把模型输出解析成 dict，先整段解析、不行再正则抠最外层花括号，都失败返回 None。

    Args:
        raw: 模型回复的原始文本，可能 JSON 前后多了几句话。
    Returns:
        解析出的 dict，两种方式都失败返回 None。
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 整段解析失败，正则找最外层的一对大括号再试一次
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


async def _run_tool(dispatch: dict, tc) -> str:
    """
    模型 return 了 tc（tool call），我们把它作为 _run_tool 的输入，去解析 tc 里具体的函数名字和参数，然后拿去调用函数，return对应工具返回的结果

    Args:
        dispatch: 工具名到 async 闭包的调度表，由 make_dispatch 生成。
        tc:       模型给的一个 tool_call 对象，带工具名和 JSON 参数串。
    Returns:
        工具返回的文本；参数非法、工具名不存在、执行报错都返回对应的错误说明。
    """
    
    # 正常路径就三步：取工具名、解析参数、查表拿到真函数调它，下面的 try 全是兜底
    name = tc.function.name

    # 参数是模型填的 JSON 串，先解析，坏了直接回错误不往下走
    try:
        args = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError:
        return f"参数不是合法 JSON：{tc.function.arguments}"

    # dispatch 是「工具名 → 真函数」的表，按模型给的名字查出对应工具，名字不存在就回错
    fn = dispatch.get(name)
    if fn is None:
        return f"未知工具 {name}，只能用 list_tree / read_file / grep_code / glob_files。"

    # 真正干活的一行：调出来的工具拿参数去执行，结果转字符串返回，执行报错也兜成文字
    try:
        return str(await fn(args))
    except Exception as e:
        return f"工具执行出错：{e}"


def _guard_dispatch(base: dict, read_cache: dict | None = None) -> dict:
    """给调度表套两层拦截：根目录树和 README 已在 system 给了，模型再要就直接回提示不真跑。

    explorer 和辩论的立场 agent 共用同一份 system，所以都套这层 guard 省工具次数。传了 read_cache
    时，被滚动清理归档过的 README 段是合理重读（内容已不在上下文里），放行不挡。

    Args:
        base:       make_dispatch 返回的原始调度表。
        read_cache: 本仓库已读缓存，用来放行归档过的 README 重读；None 就不放行（辩论方没有清理，用不上）。
    Returns:
        换上 guard 版 list_tree / read_file 的调度表，grep_code 和 glob_files 照旧。
    """
    async def _guarded_list_tree(args: dict) -> str:
        """拦截列根目录的请求，根目录树已在 system 给了，直接回提示不重复抓。"""
        if not (args.get("path", "") or "").strip("/"):
            return "根目录结构树已在任务开头给你了，直接看那棵树，要更深就 list_tree 具体子目录。"
        return await base["list_tree"](args)

    async def _guarded_read_file(args: dict) -> str:
        """拦截读 README 的请求，README 已在 system 给了，文件名大小写不定靠后缀判。

        但如果这段 README 之前读过、又被滚动清理归档了，那是合理重读，放行让它读回来。
        """
        leaf = args.get("path", "").split("/")[-1].lower()
        if leaf.startswith("readme") and not (read_cache and is_archived_read(read_cache, args)):
            return "README 已在任务开头给你了，看那份，不用再读。"
        return await base["read_file"](args)

    return {**base, "list_tree": _guarded_list_tree, "read_file": _guarded_read_file}
