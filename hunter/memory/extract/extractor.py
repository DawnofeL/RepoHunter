"""提取记忆(extract_memories)：一轮聊完在后台把对话里的重点抠出来落库（S3 提取编排）。

攒够 6 轮或用户明说要记就调一次模型出动作，校验落库，成功才挪游标。单飞加补跑保证同时
只跑一个、不漏最后一段。提取是后台事，不阻塞回答，失败只打日志。spawn_extraction 是入口，
对话那边收完一轮调它。存取走 extract.chat_memories，仓库校验走 hunter.history 的仓库账本，
调模型走 call_deepseek。每轮 dev.record 一份过程给开发者监控抽屉看。
"""

import asyncio
import json
import re
import traceback

from hunter import dev
from hunter.config import call_deepseek, load_skill
from hunter.memory.extract.chat_memories import (
    apply_actions,
    list_manifest,
    get_extract_cursor,
    set_extract_cursor,
)
from hunter.history import get_memory

# 攒够多少条新消息才提取一次，6 轮一问一答算 12 条
EXTRACT_EVERY_MESSAGES = 12

# 用户消息里出现这些词就立即提取，不等攒满
REMEMBER_WORDS = ("记住", "记一下", "记下", "别忘", "remember")

# 提取的单飞状态：同时只跑一个，跑着时新触发压进 pending，跑完补一次。进程内驻留跨请求
_extract_running = False
_extract_pending: tuple | None = None
# 后台任务引用，防止 create_task 出来的任务被 GC 掉
_bg_tasks: set = set()


def _wants_remember(new_msgs: list[dict]) -> bool:
    """看这批新消息里用户有没有明说要记，出现「记住」这类词就立即提取不等攒满。"""
    for msg in new_msgs:
        if msg.get("role") == "user":
            text = msg.get("content") or ""
            if any(w in text for w in REMEMBER_WORDS):
                return True
    return False


def _fmt_manifest(manifest: list[dict]) -> str:
    """把已有记忆清单拼成文本填进提取提示词，让模型对照去重。空就给一句占位。"""
    if not manifest:
        return "（暂无已有记忆）"
    return "\n".join(f"- {r['name']}（{r['type']}）：{r.get('description', '')}" for r in manifest)


def _parse_actions(raw: str) -> list[dict]:
    """把提取模型的输出解析成动作列表，先整段解析、不行就抠最外层方括号，都不行返回空。"""
    raw = (raw or "").strip()
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                data = None
    if isinstance(data, list):
        return data
    # 模型偶尔包成 {"memories": [...]}，兜一下
    if isinstance(data, dict) and isinstance(data.get("memories"), list):
        return data["memories"]
    return []


async def _run_extraction(session_id: str, messages: list[dict], model: str) -> None:
    """跑一次提取：读游标取新消息，够阈值就调模型出动作，校验落库，成功才挪游标。

    只处理游标之后的新消息(增量)。repo 类的 full_name 对 history 仓库账本核验，编出来的丢掉；
    update/delete 的 name 校验和密钥扫描在 apply_actions 里做。整段成功才把游标推到最新，
    失败游标不动，下次重提不漏。每步都往 dev.record 记一笔给监控抽屉。
    """
    cursor = await asyncio.to_thread(get_extract_cursor, session_id)
    new_msgs = messages[cursor:]
    if len(new_msgs) < EXTRACT_EVERY_MESSAGES and not _wants_remember(new_msgs):
        # 监控：不够阈值、也没说「记住」，这轮不提取，记一笔原因
        dev.record(session_id, "extract", {
            "fired": False,
            "reason": f"游标 {cursor}，现有 {len(messages)} 条，新增 {len(new_msgs)} 条 < {EXTRACT_EVERY_MESSAGES}",
        })
        return

    manifest = await asyncio.to_thread(list_manifest)
    skill = load_skill("extract_memories").replace("{memories}", _fmt_manifest(manifest))
    convo = "\n\n".join(f"{msg.get('role')}: {msg.get('content')}" for msg in new_msgs)

    resp = await call_deepseek(
        model=model,
        messages=[
            {"role": "system", "content": skill},
            {"role": "user", "content": convo},
        ],
    )
    raw = resp.choices[0].message.content or ""
    rows = _parse_actions(raw)

    # repo 类校验 full_name 真实存在，编出来的库里没有就丢
    valid = []
    dropped = []
    for row in rows:
        if row.get("type") == "repo":
            fn = row.get("full_name")
            if not fn or not await asyncio.to_thread(get_memory, fn):
                dropped.append(row)
                continue
        valid.append(row)

    stat = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "redacted": 0}
    if valid:
        stat = await asyncio.to_thread(apply_actions, valid, session_id)
        print(f"[extract] session={session_id} {stat}")
    # 挪游标到这次处理过的最后一条，成功才挪（失败会在上面抛异常、不到这里）
    await asyncio.to_thread(set_extract_cursor, session_id, len(messages))

    # 监控：记这次提取的全过程——填好的提示词、模型原始返回、解析出的动作、编造被丢的、落库计数
    dev.record(session_id, "extract", {
        "fired": True,
        "prompt": [{"role": "system", "content": skill}, {"role": "user", "content": convo}],
        "raw": raw,
        "actions": rows,
        "dropped": dropped,
        "stat": stat,
        "new_cursor": len(messages),
    })


async def _safe_extract(session_id: str, messages: list[dict], model: str) -> None:
    """包一层 try/except，提取失败只打日志绝不影响对话，游标也不动、下次重提。"""
    try:
        await _run_extraction(session_id, messages, model)
    except Exception:
        traceback.print_exc()


async def _extract_single_flight(session_id: str, messages: list[dict], model: str) -> None:
    """单飞：同时只跑一个提取，跑着时新触发压进 pending 覆盖旧的，跑完拿最新的补一次。"""
    global _extract_running, _extract_pending
    if _extract_running:
        _extract_pending = (session_id, messages, model)
        return
    _extract_running = True
    try:
        await _safe_extract(session_id, messages, model)
        while _extract_pending is not None:
            job = _extract_pending
            _extract_pending = None
            await _safe_extract(*job)
    finally:
        _extract_running = False


def spawn_extraction(session_id: str, messages: list[dict], model: str) -> None:
    """一轮聊完起个后台任务跑提取，不阻塞回答。任务引用存进 _bg_tasks 防被 GC。"""
    if not session_id or not messages:
        return
    task = asyncio.create_task(_extract_single_flight(session_id, list(messages), model))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
