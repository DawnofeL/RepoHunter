"""
Content Filter 主流程：gate 判方向、explorer 工具循环读源码出拆解、锚点审计、辩论裁决。

explore_one 是入口，先过 gate、再浅克隆、跑工具循环产出客观拆解并过锚点审计自愈，最后把每条
keypoint 扇给正反辩论加裁决。_call_explore/_call_final 发探查和收尾两种请求，_parse_final
解析收尾 JSON 带兜底，_call_gate 判要不要深挖，_clone_repo 浅克隆到本地。
"""

import asyncio
import json
import shutil
import tempfile

from hunter.config import call_deepseek, set_repo_sem, load_skill, MODELS, GITHUB_PAT, CLONE_DIR
from hunter.cost import track, TokenMeter
from hunter.repo_detection.agent_tools import (
    tool_schemas as _tool_schemas, make_dispatch, archive_segment, is_archived_read, MAX_LINES,
)
from hunter.memory.compact.tokens import estimate_tokens
from hunter.repo_detection.scoring import reconcile, score_one
from hunter.repo_detection.tool_loop import (
    _assistant_dict, _run_tool, _guard_dispatch, _try_json,
)
from hunter.repo_detection.prompt_text import texts as _ptexts
from hunter.repo_detection.log_visual import (
    _default_emit, _clip, _visual, _log_raw, _status, _emit_token_line, _flush_block, _fmt_visual,
)
from hunter.repo_detection.audit import (
    _audit_anchors, _repair_msg, _drop_bad_designs, _audit_cases,
)
from hunter.repo_detection.debate import (
    _fill_triage, _fill_stance, _fill_adjudicate, _triage, _debate_json,
)
from hunter.repo_detection.evidence import build_evidence


# 单个仓库最多调几次工具，用完后强制让模型直接给出判断
MAX_TOOLS = 20


# 滚动清理触发线：历史里 system 之外的部分（工具返回那些）估出来超过这个 token 数就清一轮。
# 探查场景越早清、每轮喂给模型的越干净，拆解质量越稳，不用等快撑爆才动手
CLEAN_TRIGGER_TOKENS = 20_000

# 清理后要腾出的目标：清完剩余空间（还是 system 之外那部分）压到这个数以下才算够，
# 达不到就少留一个读文件结果再清，一路减到只剩最近一个还超就截断它
CLEAN_TARGET_TOKENS = 12_000

# 清理时最多先留几个最近的读文件结果，从这个数往下试着减，直到剩余压进目标
CLEAN_KEEP_START = 5

# 探查预算耗尽后，若锚点审计发现坏锚点，额外解锁这么多次工具专供模型重查锚点。
# 探查阶段常把 MAX_TOOLS 烧光，审计打回的重修又要带工具核实，没有这笔独立额度重修就
# 只能直接删点。这笔额度只在修复轮解锁，不计入 MAX_TOOLS。
REPAIR_BUDGET = 3


# 同一工具加同一参数连续调用达到这个次数就判死循环，硬中断进强制交卷。
# 判据看调用指纹（工具名+关键参数），不同参数会清零，不误伤正常的连续读不同文件。
DUP_CALL_LIMIT = 3


# 零新信息返回的标记表，全是我们自己工具吐的固定措辞;判定只看结果前 40 字,防止源码正文撞词误判。
# 工具返回文案跟着搜索语言走了，中英两套标记都得列上，否则英文搜索时停滞检测认不出、失效。
# 英文标记必须挑每句「开头 40 字内一定出现」的词，否则落在 40 字窗口外照样匹配不到
FUTILE_MARKS = ("已读过", "无命中", "没有匹配的文件", "已在任务开头给你了",
                "文件不存在", "路径越界", "不是可列的目录", "工具预算已用完",
                "This file segment", "No matches", "No matching files", "The README was given",
                "The root directory tree was given", "File does not exist",
                "Path out of bounds", "Not a listable directory", "budget is used up")


def _futile(out: str) -> bool:
    """判一条工具返回是不是零新信息，只看开头 40 字里有没有命中标记表。"""
    head = str(out)[:40]
    return any(mark in head for mark in FUTILE_MARKS)


def _call_fp(tc) -> str:
    """把一个工具调用规约成指纹：工具名 + 原始参数串。

    参数串原样用，模型用完全相同的参数重复调同一个工具才会撞出同一指纹；读不同文件、
    搜不同 pattern 指纹都不同，不会误伤正常探查。

    Args:
        tc: 模型给的一个 tool_call 对象。
    Returns:
        工具名和参数拼成的指纹字符串。
    """
    return f"{tc.function.name}|{tc.function.arguments or ''}"


def _is_archived_reread(tc, read_cache: dict) -> bool:
    """判一个工具调用是不是「重读一段被清理归档过的读文件」，是就不算它进死循环指纹。

    只认 read_file，参数解析坏了当不是。归档重读的指纹跟当初那次读一样，但它是系统清理逼出来的
    合理重读，重读上限那关自会拦，不该在这里被误判成死循环。
    """
    if tc.function.name != "read_file":
        return False
    try:
        args = json.loads(tc.function.arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return False
    return is_archived_read(read_cache, args)


async def _call_explore(messages: list, meter: TokenMeter, model: str | None = None,
                        output_language: str = "简体中文") -> object:
    """
    带四个工具发一次请求，让模型自己决定要不要调工具，不强制 JSON。

    带工具的回复和纯文本回复结构不同、不能混用，所以探查轮和收尾轮分开走。工具定义按
    output_language 取中英那套。

    Args:
        messages: 当前对话历史。
        meter:    本仓库的 token 累计器。
        model:    模型名，不传时用 content_filter 默认。
        output_language: 工具定义（description）的语言。
    Returns:
        LLM 的原始返回对象。
    """

    # 每次发请求，除了把对话 message 递过去，还附一张「你可以用这四样工具，规格如下」的工具定义
    resp = await call_deepseek(
        model=model or MODELS["content_filter"],
        messages=messages,

        # 四个工具的规格清单，不塞进 messages，作为单独参数附给模型看
        tools=_tool_schemas(output_language),

        # auto 表示调不调、调哪个由模型自己定，代码只把清单摆出去
        tool_choice="auto",
    )

    # 全局累计照旧，再把这次调用记进当前仓库自己的累计器，供日志和进度表显示
    track("content_filter", resp)
    meter.add(resp)
    return resp


async def _call_final(messages: list, meter: TokenMeter, model: str | None = None) -> object:
    """
    不带工具发一次请求并强制 JSON，逼模型用现有信息直接给最终判断。

    Args:
        messages: 当前对话历史。
        meter:    本仓库的 token 累计器。
        model:    模型名，不传时用 content_filter 默认。
    Returns:
        LLM 的原始返回对象。
    """

    # 这次不附 tools 清单，模型没工具可调，只能用现有信息直接作答
    resp = await call_deepseek(
        model=model or MODELS["content_filter"],
        messages=messages,

        # 强制输出合法 JSON 对象，逼模型按收尾格式吐结果而不是大白话
        response_format={"type": "json_object"},
    )

    # 全局累计照旧，再把这次调用记进当前仓库自己的累计器，供日志和进度表显示
    track("content_filter", resp)
    meter.add(resp)
    return resp


async def _parse_final(messages: list, raw: str, meter: TokenMeter, model: str | None = None,
                       output_language: str = "简体中文") -> dict | None:
    """
    解析收尾 JSON，坏了先让模型只修格式重试一次，仍坏再抢救一次提取，都失败返回 None。

    重试和抢救都接在原历史后面，前缀没变所以能命中缓存，几乎不额外花钱。抢救针对模型
    彻底不听格式指令、整段吐大白话的情况：一整个仓库探查下来十几万 token，栽在最后一步
    格式上全损太亏，多花一次调用把它上一段回复里的内容硬提成 JSON，能救一个算一个。
    重试消息按 output_language 走，英文探查时不插中文。

    Args:
        messages: 当前对话历史，重试时在它后面追加。
        raw:      模型这次给出的待解析文本。
        meter:    本仓库的 token 累计器。
        model:    模型名，不传时用 content_filter 默认。
        output_language: 重试提示消息的语言。
    Returns:
        解析出的 dict，重试和抢救后仍解析不出返回 None。
    """
    parsed = _try_json(raw)
    if parsed is not None:
        return parsed

    t = _ptexts(output_language)
    retry = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": t["json_fix"]},
    ]
    resp = await _call_final(retry, meter, model)
    retry_raw = resp.choices[0].message.content or ""
    parsed = _try_json(retry_raw)
    if parsed is not None:
        return parsed

    # 两次修格式都没用，最后抢救一次：把它刚才那段吐出来的东西当素材，让它照输出契约提取成 JSON
    rescue = retry + [
        {"role": "assistant", "content": retry_raw},
        {"role": "user", "content": t["json_rescue"]},
    ]
    resp = await _call_final(rescue, meter, model)
    return _try_json(resp.choices[0].message.content or "")


async def _clone_repo(full_name: str) -> str | None:
    """
    把仓库浅克隆到一个临时目录，返回目录路径，克隆失败返回 None。

    GITHUB_PAT 拼进 URL，私有库也能拉、还避开匿名限速。--depth 1 只取当前快照不要历史，
    省时省盘。克隆落在项目内的 CLONE_DIR 下，跑完不自动删，留着给用户审查，由 clear_clone_dir 统一清。

    Args:
        full_name: owner/repo。
    Returns:
        克隆根目录的绝对路径；克隆失败返回 None（残缺目录已清掉）。
    """
    tmp = tempfile.mkdtemp(prefix="repohunter_", dir=CLONE_DIR)
    url = (f"https://{GITHUB_PAT}@github.com/{full_name}.git" if GITHUB_PAT
           else f"https://github.com/{full_name}.git")
    # stdin 显式断开，防 git 在凭证异常时等终端输入挂死
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, tmp,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"[{full_name}] 🔴 克隆失败：{err.decode('utf-8', 'replace').strip()[:200]}")
        return None
    return tmp


async def _call_gate(system: str, gate_user: str, meter: TokenMeter, model: str | None = None) -> dict:
    """发一次不带工具的 gate 请求，判这个仓库要不要深挖，强制 JSON。

    只看 system 里的项目信息判整体方向，返回 {"skip": bool, "reason": str}。解析不出或
    格式不对默认 skip=False，宁可深挖也别错跳，错跳不可逆。

    Args:
        system:    预拼的 system，含项目六样信息。
        gate_user: 判要不要深挖的 user 指令，模板见 repo_detection/skills/skip_gate.md。
        meter:     本仓库的 token 累计器。
        model:     模型名，不传用 content_filter 默认。
    Returns:
        {"skip": bool, "reason": str}，解析不出时给 {"skip": False}。
    """
    resp = await call_deepseek(
        model=model or MODELS["content_filter"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": gate_user},
        ],
        response_format={"type": "json_object"},
    )
    track("skip_gate", resp)
    meter.add(resp)
    data = _try_json(resp.choices[0].message.content or "")
    return data if isinstance(data, dict) else {"skip": False}


async def _repair_round(messages: list, dispatch: dict, meter: TokenMeter, model: str | None,
                        full_name: str, log: str, emit_log,
                        output_language: str = "简体中文") -> dict | None:
    """审计打回后的带工具重修：解锁 REPAIR_BUDGET 次工具让模型重查锚点，再出一次最终 JSON。

    探查阶段常把 MAX_TOOLS 烧光，主循环里的重修那时已经没工具可用、只能直接删点。这里给一笔
    独立小额度，让模型真的用 glob/grep/read 去核实打回的那几个锚点、把 where 改对，救回被删的
    设计点。额度用完（或模型提前不调工具）就收尾出 JSON。messages 已在调用前追加好重修指令。
    注入的额度用完和逼停消息按 output_language 走。

    Args:
        messages: 当前对话历史，末尾已是重修指令。函数内继续往末尾追加。
        dispatch: 工具调度表，跟主循环用的是同一份（带 guard）。
        meter:    本仓库的 token 累计器。
        model:    模型名。
        full_name: owner/name，日志用。
        log:      日志模式。
        emit_log: 日志回调。
        output_language: 注入消息的语言。
    Returns:
        重修后解析出的最终 JSON dict，解析不出返回 None。
    """
    t = _ptexts(output_language)
    used = 0
    while used < REPAIR_BUDGET:
        resp = await _call_explore(messages, meter, model, output_language)
        msg = resp.choices[0].message

        # 模型不调工具了，这轮就是重修后的最终判断
        if not msg.tool_calls:
            if log != "ReAct":
                _visual(emit_log, full_name, "修复轮结论", msg.content)
            return await _parse_final(messages, msg.content or "", meter, model, output_language)

        messages.append(_assistant_dict(msg))
        if log != "ReAct":
            for tc in msg.tool_calls:
                _visual(emit_log, full_name, f"→(修复) {tc.function.name}({tc.function.arguments})")

        # 修复轮用自己的额度，一轮里超额的填占位，不真跑
        results_msgs: list = [None] * len(msg.tool_calls)
        to_run = []
        for i, tc in enumerate(msg.tool_calls):
            if used < REPAIR_BUDGET:
                used += 1
                to_run.append((i, tc))
            else:
                results_msgs[i] = {"role": "tool", "tool_call_id": tc.id,
                                   "content": t["repair_budget_out"]}
        outs = await asyncio.gather(*[_run_tool(dispatch, tc, output_language) for _, tc in to_run])
        for (i, tc), out in zip(to_run, outs):
            results_msgs[i] = {"role": "tool", "tool_call_id": tc.id, "content": out}
            if log != "ReAct":
                _visual(emit_log, full_name, f"{tc.function.name} 返回", out)
        messages += results_msgs

    # 额度用满还在调工具，逼它不带工具直接出 JSON
    messages.append({"role": "user", "content": t["force_stop"]})
    resp = await _call_final(messages, meter, model)
    return await _parse_final(messages, resp.choices[0].message.content or "", meter, model, output_language)


def _read_result_indices(messages: list) -> list[dict]:
    """扫历史，找出所有「读文件工具的返回」消息，回一份按出现顺序排的清单。

    先把每个 assistant 消息里的工具调用按 id 建索引（id → 调用了什么、参数是什么），再逐条
    找 role=tool 的返回消息，凭它的 tool_call_id 回查是不是 read_file 调用，是就记下它在
    messages 里的下标和这次读的 (path, offset, limit)。只认还没被清过的（content 不是占位符）。

    Args:
        messages: 当前对话历史。
    Returns:
        每项 {"idx": 在 messages 里的下标, "key": (path, offset, limit)} 的清单，按出现先后排。
    """
    # id → 这次调用的 (工具名, 参数 dict)
    call_by_id: dict = {}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                call_by_id[tc["id"]] = (tc["function"]["name"], args)

    found = []
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        name, args = call_by_id.get(m.get("tool_call_id"), ("", {}))
        if name != "read_file":
            continue
        # 已经清成占位符的不再算进来（中英占位符都认）
        if _is_cleared(m.get("content", "")):
            continue
        path = args.get("path", "")
        offset = args.get("offset") or 0
        limit = args.get("limit") or MAX_LINES
        found.append({"idx": i, "key": (path, offset, limit)})
    return found


def _clear_read_result(messages: list, item: dict, read_cache: dict,
                       output_language: str = "简体中文") -> None:
    """把一条读文件返回消息的正文换成占位符，并在缓存里把这段标成归档。

    占位符带文件名和行段，模型日后要回看能精准重新定位；归档后这段就从「读过就挡」变成
    「可重读（到上限）」，见 agent_tools 的重读规矩。占位符按 output_language 走。

    Args:
        messages:   当前对话历史，原地改。
        item:       _read_result_indices 给的一项，含 idx 和 key。
        read_cache: 本仓库已读缓存，把这段标归档。
        output_language: 占位符的语言。
    """
    path, offset, limit = item["key"]
    messages[item["idx"]]["content"] = _ptexts(output_language)["cleared"].format(
        path=path or "(未知文件)", start=offset, end=offset + limit)
    archive_segment(read_cache, item["key"])


def _system_tokens(system: str) -> int:
    """估一下 system prompt 本身多少 token，用来从整段历史里扣掉、只算工具返回那部分。"""
    return estimate_tokens([{"content": system}])


def _is_cleared(content: str) -> bool:
    """判一条消息内容是不是已经被清成占位符（中英两种占位符都认）。"""
    s = str(content)
    return s.startswith("(此处原为") or s.startswith("(This was")


def _rolling_clean(messages: list, system: str, read_cache: dict,
                   full_name: str, rnd: int, log: str, emit_log,
                   output_language: str = "简体中文") -> dict | None:
    """滚动清理：历史里 system 之外的部分超过触发线就清老的读文件返回，直到剩余压进目标。

    先量 system 之外多少 token，没到 CLEAN_TRIGGER_TOKENS 直接返回 None（这轮不清）。到线了走
    阶梯：先留最近 CLEAN_KEEP_START 个读文件返回、清更早的，量剩余够不够 CLEAN_TARGET_TOKENS；
    不够就少留一个再清再量，一路减到留 1 个；留 1 个还超就把这最后一个正文截断到刚好压进目标。
    清理只保命、绝不叫停探查，纯代码不调模型。

    Args:
        messages:   当前对话历史，原地改。
        system:     本仓库的 system prompt，量长度时要从总量里扣掉。
        read_cache: 本仓库已读缓存，清一段就归档一段。
        full_name:  owner/name，日志和监控用。
        rnd:        当前轮次，监控用。
        log:        日志模式。
        emit_log:   日志回调。
    Returns:
        这轮真清了就返回一份监控 dict（清了几个、当前状态），没到线不清返回 None。
    """
    sys_toks = _system_tokens(system)

    def hist_tokens() -> int:
        # 整段历史的 token 减去 system 那份，就是工具返回那部分
        return max(estimate_tokens(messages) - sys_toks, 0)

    if hist_tokens() < CLEAN_TRIGGER_TOKENS:
        return None

    items = _read_result_indices(messages)
    cleared = 0

    # 阶梯：留最近 keep 个、清更早的，从 CLEAN_KEEP_START 往下减，先把剩余压进目标的就停
    for keep in range(CLEAN_KEEP_START, 0, -1):
        to_clear = items[:max(len(items) - keep, 0)]
        for it in to_clear:
            if _is_cleared(messages[it["idx"]].get("content", "")):
                continue
            _clear_read_result(messages, it, read_cache, output_language)
            cleared += 1
        if hist_tokens() <= CLEAN_TARGET_TOKENS:
            break

    # 减到只留最近 1 个仍超目标：这最后一个自己就太大，把它正文截断到刚好压进目标
    if hist_tokens() > CLEAN_TARGET_TOKENS and items:
        last = messages[items[-1]["idx"]]
        over = hist_tokens() - CLEAN_TARGET_TOKENS
        # 按「4 字符约 1 token」把超出的 token 折回字符数，从尾部砍掉那么多
        cut_chars = over * 4
        body = str(last.get("content", ""))
        trunc = ("\n…(content too long, truncated; re-readable if needed)"
                 if output_language == "English" else "\n…（内容过长已截断，如需可重读后续）")
        if len(body) > cut_chars:
            last["content"] = body[:len(body) - cut_chars] + trunc

    # 归档状态快照：当前所有读文件段各是 live 还是 archived、重读几次，逐条列出来。
    # 这条日志走 emit_log 的按仓库通道，前端把它落进这个仓库自己那行探查日志里，多仓库并行时
    # 各看各的，一眼看出这条循环压了多少、清了谁、谁被重读过
    archived = sum(1 for r in read_cache.values() if r["state"] == "archived")
    seg_lines = []
    for (path, off, lim), rec in read_cache.items():
        mark = "已归档" if rec["state"] == "archived" else "在上下文"
        reread = f"，重读 {rec['rereads']} 次" if rec["rereads"] else ""
        seg_lines.append(f"  · {path} 行 {off}-{off + lim}：{mark}{reread}")
    detail = "\n".join(seg_lines)
    _status(emit_log, full_name,
            f"🧹 上下文清理[Round {rnd}]：本轮清了 {cleared} 个旧读文件返回，"
            f"当前 {len(read_cache)} 段（{archived} 已归档 / {len(read_cache) - archived} 在上下文），"
            f"剩余约 {hist_tokens()} tok\n{detail}", log)

    return {"round": rnd, "cleared": cleared,
            "total_segments": len(read_cache),
            "archived": archived, "live": len(read_cache) - archived,
            "hist_tokens": hist_tokens()}


async def explore_one(full_name: str, system: str, gate_user: str, user: str,
                      advocate_md: str, skeptic_md: str, adjudicate_md: str,
                      keypoints: list[str], stars: int, size: int, model: str,
                      output_language: str = "简体中文", progress=None,
                      log: str = "Visual", emit_log=_default_emit,
                      standards: dict[str, str] | None = None,
                      cached_dissection: dict | None = None,
                      triage_md: str | None = None) -> dict:
    """
    对一个候选仓库先过 gate 判要不要深挖，要就跑工具循环探查摆事实，再走辩论裁决判 keypoint。

    system、gate_user、user 和三份辩论模板由 Repo_Detection 预拼好传入。system 含项目六样信息，README 和
    根目录已在 system 给出，用一层 guard 拦掉模型对它们的重复读取，省工具次数。stars、size 也在
    Repo_Detection 预抓好传入（size 已拼进 system 给模型判体量），这里原样带进结果 dict。
    content 只摆事实、不判 keypoint；判定走辩论：每条 keypoint 先分诊判拆解够不够，不够就按
    分诊清单从克隆里抠源码，正反两方拿同一份材料并行判、锚点核验后交裁决出终判，辩论方不带工具。

    Args:
        full_name:     owner/name。
        system:        预拼的 system，含分析师角色和项目六样原文，模板见 repo_detection/skills/system_header.md。
        gate_user:     预拼的 gate user，判要不要深挖，模板见 repo_detection/skills/skip_gate.md。
        user:          预拼的 Content Filter user，模板见 repo_detection/skills/content_filter.md。
        advocate_md:   正方模板原文，模板见 repo_detection/skills/advocate.md，运行时用 content 的事实填好再调。
        skeptic_md:    反方模板原文，模板见 repo_detection/skills/skeptic.md。
        adjudicate_md: 裁决模板原文，模板见 repo_detection/skills/adjudicate.md，用双方过审论据填好再调。
        keypoints:     keypoint 原文清单，给辩论各方和 reconcile 对齐用。对齐永远只认原文。
        standards:     {keypoint 原文: 判定标准} 映射，Keypoint_Understanding 编译出来的。只拼进
                       正反方和裁决的 prompt 让三方按统一标准判，绝不进 reconcile 对齐（会污染）。
                       None 或某条查不到就退化成只用原文，等价于没这个模块。
        cached_dissection: 记忆库里存的这仓库拆解，命中时由 Repo_Detection 传进来。有值就跳过
                       explorer 那二十次工具循环，直接拿它当拆解往下走辩论；gate 和克隆照跑
                       （锚点审计要对本地文件核，按需取证也要读克隆）。None 就正常跑 explorer。
        triage_md:     分诊 skill 模板。None 就自己 load_skill 加载，Repo_Detection 会预加载好传进来。
        stars:         仓库 star 数，预抓好传入，进结果展示、排序平手比较和辩论判热度。
        size:          仓库体积 KB，预抓好传入，已拼进 system，也给辩论判体量。
        model:         模型名。
        output_language: 辩论各方散文字段用的语言，默认简体中文。
        progress:      可选回调 progress(full_name, rounds, tools_used, status, tokens, hit_rate, reason,
                       debate_done, debate_total)，末两个只在 judging 状态给 worker 完成数/总数，其余为 0，
                       status 走 running → judging → done（跳过是 skipped、失败是 degraded）；
                       reason 只在 gate 判 skip 时给一句跳过理由，其它状态是空串。
        log:           "Visual"（默认）整理过的日志格式，Round 独立成行、去掉仓库前缀、工具
                       返回按行截断；"ReAct" 原样打印每轮新增发给 LLM 的消息和 LLM 的原始回复，
                       不做任何裁剪或格式修饰，用于评估 agent 实际看到的原生 prompt。
        emit_log:      Visual 模式的日志回调 emit_log(full_name, text)，把一整块日志文本上报出去，
                       前缀不在这里加、由回调那层决定要不要按仓库分组。默认直接 print 不带前缀
                       （notebook 单仓库用）；webapp 传自己的回调把日志按 full_name 结构化分流。
                       ReAct 模式不走这个回调，仍直接 print 原生内容。

    Returns:
        含 full_name、dissection、keypoint_hits/total、stars、size、detail、tools_used、
        tokens、hit_rate、read_files、audit、skipped、reason、degraded 的 dict。
        detail 每条 keypoint 带裁决的 status/evidence，外加 advocate/skeptic 两方论据。
        read_files 是这次 Content Filter 实际读过的文件相对路径去重清单，给结论溯源用。
    """

    # triage_md 没传就自己加载（notebook 直接调 explore_one 时的兜底；Repo_Detection 会传进来）
    if triage_md is None:
        triage_md = load_skill("triage")

    # 给本仓库建一个 worker 并发闸塞进 contextvar，这仓库派的所有 worker 自动继承，
    # 单仓库同时在飞的 LLM 请求受它限；跨仓库总量另受 call_deepseek 里的全局闸限
    set_repo_sem()

    # 本仓库专用的已读缓存，键是 (path, offset, limit)，值记这段的状态（live/archived）和重读次数。
    # read_file 靠它去重和挡重读；滚动清理归档一段就在这里把它标 archived，放它被重读回来
    read_cache: dict = {}

    # 注入模型的固定文案（逼停、提醒、修复、占位）按当前搜索语言取，英文探查时不插中文
    pt = _ptexts(output_language)

    # meter 计本仓库 token，从 gate 那次调用就开始算
    meter = TokenMeter()

    # gate：只看 system 里的项目信息判要不要深挖，不带工具、不克隆。判 skip 就到此为止
    gate = await _call_gate(system, gate_user, meter, model)
    if gate.get("skip"):
        reason = gate.get("reason", "")
        _status(emit_log, full_name, f"⏭ gate 判跳过：{_clip(reason, 120)}", log)
        detail = reconcile({}, keypoints)
        scores = score_one(detail)
        if progress:
            progress(full_name, 0, 0, "skipped", meter.total, meter.hit_rate, reason)
        return {"full_name": full_name, "dissection": {}, **scores,
                "stars": stars, "size": size, "detail": detail,
                "tools_used": 0, "tokens": meter.total, "hit_rate": round(meter.hit_rate, 3),
                "read_files": [], "audit": {"repaired": False, "dropped": [], "clean": True},
                "skipped": True, "reason": reason, "degraded": False}

    _status(emit_log, full_name, "▶ 开始探查", log)

    # gate 判要深挖，把仓库浅克隆到临时目录，四个工具都对这份本地副本操作；克隆失败就降级返回
    local_root = await _clone_repo(full_name)
    if local_root is None:
        detail = reconcile({}, keypoints)
        scores = score_one(detail)
        if progress:
            progress(full_name, 0, 0, "degraded", meter.total, meter.hit_rate)
        return {"full_name": full_name, "dissection": {}, **scores,
                "stars": stars, "size": size, "detail": detail,
                "tools_used": 0, "tokens": meter.total, "hit_rate": round(meter.hit_rate, 3),
                "read_files": [], "audit": {"repaired": False, "dropped": [], "clean": True},
                "skipped": False, "reason": "", "degraded": True}

    # make_dispatch 把克隆目录和已读缓存绑进四个工具，guard 拦掉重复读 README 和根目录
    # 克隆跑完不删，留在 CLONE_DIR 给用户接着深入看，由 clear_clone_dir 统一清
    dispatch = _guard_dispatch(make_dispatch(local_root, read_cache, output_language), read_cache, output_language)

    # 对话起点：system 含项目六样资料，user 是Content Filter指令，后续每轮往这个列表末尾追加
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # used 累计已动用的工具次数，rnd 数到第几轮，llm_out 存最终判断（meter 在 gate 前已建）
    used = 0
    rnd = 0
    llm_out: dict | None = None

    # 锚点审计用：repaired 标记是否已打回重修过一次，dropped_names 记兜底删掉的设计点
    repaired = False
    dropped_names: list = []

    # 两个程序注入的提醒各只发一次：半程盘点、停滞收尾
    nudged_mid = False
    nudged_stall = False

    # 重复调用熔断：记上一次见到的调用指纹和它连续出现几次，同指纹连撞 DUP_CALL_LIMIT 次
    # 就判死循环、强制交卷。dup_stop 一置就跟预算耗尽同路，逼模型直接出 JSON
    last_fp = ""
    dup_count = 0
    dup_stop = False

    # 命中记忆：这仓库上次拆过、拆解存在记忆库里，直接拿现成的当 llm_out，跳过下面整个 explorer
    # 循环那二十次工具调用。存的拆解上次已过锚点审计，这里不再重审。gate 和克隆已经照跑，
    # 后面辩论照走（辩论对着这次的新需求现判，锚点审计对本地克隆核，都不受影响）
    skip_explorer = cached_dissection is not None
    if skip_explorer:
        _status(emit_log, full_name, "🧠 命中记忆，复用已存拆解，跳过 explorer", log)
        llm_out = {"dissection": cached_dissection}

    # 工具调用 ReAct 主循环：每轮发一次请求，模型还要工具就执行再进下一轮；产出最终 JSON
    # 后先过锚点审计闸，对不上就打回重修一次，再不行删点兜底，全对才跳出。命中记忆时整个跳过
    while not skip_explorer:
        rnd += 1

        # 工具次数用满、或撞上重复调用熔断，追加逼停消息，用只输出 JSON 的模式逼模型直接给判断
        if used >= MAX_TOOLS or dup_stop:
            if log == "ReAct":
                _log_raw(full_name, f"===== Round {rnd} 逼停 =====", pt["force_stop"])
            else:
                _visual(emit_log, full_name, f"Round {rnd}")
                why = "重复调用熔断" if dup_stop else f"预算耗尽 ({MAX_TOOLS}次)"
                _visual(emit_log, full_name, f"⚠ {why}，追加逼停消息")
            messages.append({"role": "user", "content": pt["force_stop"]})
            resp = await _call_final(messages, meter, model)
            _emit_token_line(emit_log, full_name, rnd, meter, log)
            llm_out = await _parse_final(messages, resp.choices[0].message.content or "", meter, model, output_language)
        else:
            if log == "ReAct":
                _log_raw(full_name, f"\n===== Round {rnd} 发给 LLM 的新增消息 =====", messages[-1])
            else:
                _visual(emit_log, full_name, f"Round {rnd}")
            resp = await _call_explore(messages, meter, model, output_language)
            _emit_token_line(emit_log, full_name, rnd, meter, log)

            # resp.choices[0].message 是 OpenAI SDK 返回里这一轮模型的回复，含文本和可能的 tool_calls
            msg = resp.choices[0].message

            if progress:
                progress(full_name, rnd, used, "running", meter.total, meter.hit_rate)

            # 模型还在调工具，执行完进下一轮，这一轮不产出最终 JSON
            if msg.tool_calls:
                # 先把这条 assistant 消息存进历史，再打日志，顺序不能反
                assistant_dict = _assistant_dict(msg)
                messages.append(assistant_dict)
                if log == "ReAct":
                    _log_raw(full_name, f"Round {rnd} LLM 原始回复", assistant_dict)
                else:
                    if msg.content:
                        _visual(emit_log, full_name, "💭", msg.content)
                    for tc in msg.tool_calls:
                        _visual(emit_log, full_name, f"→ {tc.function.name}({tc.function.arguments})")

                # 重复调用熔断：逐个过本轮调用的指纹，跟上次同就累加、不同就清零重记，
                # 连撞 DUP_CALL_LIMIT 次判死循环。本轮调用照常执行完，下一轮轮首才逼停。
                # 但被清理归档过的段的重读是系统逼出来的合理重读、指纹跟原读一样，不能算它死循环
                for tc in msg.tool_calls:
                    if _is_archived_reread(tc, read_cache):
                        continue
                    fp = _call_fp(tc)
                    if fp == last_fp:
                        dup_count += 1
                    else:
                        last_fp = fp
                        dup_count = 1
                    if dup_count >= DUP_CALL_LIMIT:
                        dup_stop = True
                        _visual(emit_log, full_name,
                                f"🔁 同一调用连撞 {dup_count} 次，判死循环，下一轮强制收尾")

                # 模型一轮可能发好几个工具调用，按顺序分配额度：没超的真执行，超的填占位
                results_msgs: list = [None] * len(msg.tool_calls)
                to_run = []
                for i, tc in enumerate(msg.tool_calls):
                    if used < MAX_TOOLS:
                        used += 1
                        to_run.append((i, tc))
                    else:
                        results_msgs[i] = {"role": "tool", "tool_call_id": tc.id,
                                           "content": pt["tool_budget_out"]}

                # 真要执行的并发跑，结果按原下标填回去，保证每个 tool_call 都有一条对应结果消息
                outs = await asyncio.gather(*[_run_tool(dispatch, tc, output_language) for _, tc in to_run])
                for (i, tc), out in zip(to_run, outs):
                    results_msgs[i] = {"role": "tool", "tool_call_id": tc.id, "content": out}
                    if log == "ReAct":
                        _log_raw(full_name, f"Round {rnd} 工具结果消息", results_msgs[i])
                    else:
                        _visual(emit_log, full_name, f"{tc.function.name} 返回", out)

                messages += results_msgs

                # 一轮完整结束才许注入（assistant 的工具调用和 tool 结果之间插东西历史就不合法了）
                # 停滞：这轮真执行的结果全是零新信息，提醒它别再空转，直接收尾
                if not nudged_stall and to_run and all(_futile(o) for o in outs):
                    nudged_stall = True
                    if log == "ReAct":
                        _log_raw(full_name, "注入停滞提醒", pt["stall"])
                    else:
                        _visual(emit_log, full_name, "💤 本轮全是零新信息，注入停滞提醒")
                    messages.append({"role": "user", "content": pt["stall"]})
                # 半程：预算用掉一半，提醒它对照契约盘点，从发散转向查漏
                if not nudged_mid and used >= MAX_TOOLS // 2:
                    nudged_mid = True
                    if log == "ReAct":
                        _log_raw(full_name, "注入盘点提醒", pt["midpoint"])
                    else:
                        _visual(emit_log, full_name, f"🧭 预算过半 ({used}/{MAX_TOOLS})，注入盘点提醒")
                    messages.append({"role": "user", "content": pt["midpoint"]})

                # 这轮的工具返回都追加进历史了，进下一轮前滚动清理一次：到线才清、清完压回目标，
                # 清理只保命不叫停探查。清理内部把归档状态快照走 emit_log 落进这个仓库自己那行日志
                _rolling_clean(messages, system, read_cache, full_name, rnd, log, emit_log, output_language)
                continue

            # 模型不调工具了，这轮回复就是最终判断
            if log == "ReAct":
                _log_raw(full_name, f"Round {rnd} LLM 原始回复（无工具调用，即最终判断）", msg.content)
            else:
                _visual(emit_log, full_name, "无工具调用，结论", msg.content)
            llm_out = await _parse_final(messages, msg.content or "", meter, model, output_language)

        # 审计闸：拿到最终 JSON，核对 key_designs 的 where 锚点。坏了进修复轮重查、再审计，
        # 修复过仍坏就删点兜底。整个审计+修复收在这个内层循环里，不回主循环顶（回顶会撞上预算
        # 耗尽的逼停，把修复轮辛苦重查的结果覆盖掉）
        done_explorer = False
        while True:
            bad = _audit_anchors(local_root, llm_out, output_language)
            if not bad:
                done_explorer = True
                break

            # 已经修过一次 → 不再问模型，代码删点兜底
            if repaired:
                dropped_names = sorted({b["name"] for b in bad})
                _status(emit_log, full_name, f"🔧 锚点仍对不上 {len(bad)} 处，删点兜底：{dropped_names}", log)
                llm_out = _drop_bad_designs(llm_out, bad)
                done_explorer = True
                break

            # 第一次对不上 → 进修复轮带独立额度重查锚点。探查预算耗尽也照修，REPAIR_BUDGET
            # 是专供修复的独立额度，不受探查那 MAX_TOOLS 是否烧光影响
            repaired = True
            _status(emit_log, full_name, f"🔧 锚点对不上 {len(bad)} 处，进修复轮重查（额度 {REPAIR_BUDGET}）", log)
            messages.append({"role": "user", "content": _repair_msg(bad, output_language)})
            llm_out = await _repair_round(messages, dispatch, meter, model, full_name, log, emit_log, output_language)

        if done_explorer:
            break

    # 最终 JSON 彻底解析不出就标降级，对齐函数收到空内容会把判定全补 unknown，结构照样完整
    degraded = llm_out is None
    if degraded:
        _status(emit_log, full_name, "🔴 JSON 解析失败，降级 unknown", log)

    # content 只摆事实，keypoint 判定走辩论：先分诊判拆解够不够，不够按需抠源码，再正反裁决出终判
    # content 降级(llm_out 为 None)没有事实可辩、keypoints 为空没有可判的，都跳过辩论全补 miss
    adv_cases: list = []
    ske_cases: list = []
    if llm_out is not None and keypoints:
        # 拆解在探查阶段早就算出来了，先打出来给人看一眼，再进辩论，让日志顺序跟真实执行顺序对上
        dissection = llm_out.get("dissection", {})
        if dissection:
            _status(emit_log, full_name,
                    f"📋 拆解:\n{json.dumps(dissection, ensure_ascii=False, indent=2)}", log)

        # 拆解拼进 system 尾部，分诊、正、反三方共享这个前缀，一份拆解全仓库只 miss 一次缓存。
        # gate/explorer 焐热的是前半段原 system，拆解那段每仓库首次 debate 调用 miss、之后全命中
        system_debate = (system + "\n\n# 这个仓库的架构拆解\n\n"
                         "下面是探查源码后得出的客观架构拆解，判断时参考：\n\n"
                         + json.dumps(dissection, ensure_ascii=False, indent=2))

        _status(emit_log, full_name, f"⚖ 按 keypoint 扇出判定：{len(keypoints)} 条 × 正反两方", log)

        # 每条 keypoint 一条独立链路：分诊 → 按需取证 → 正反并行 → 锚点核验 → 立刻单条裁决。
        # 一次裁决只看一条 keypoint，上下文干净、不会串条；keypoint 由代码绑定，对齐结构上有保证
        total_workers = 2 * len(keypoints)
        done_workers = 0

        async def _counted(coro):
            nonlocal done_workers
            result = await coro
            done_workers += 1
            if progress:
                progress(full_name, rnd, used, "judging", meter.total, meter.hit_rate, "",
                         done_workers, total_workers)
            return result

        async def _judge_one(kp: str) -> tuple:
            kp_tag = kp if len(kp) <= 16 else kp[:16] + "…"

            # 分诊：判这份拆解够不够判这条，不够就开取证清单。日志攒进 triage_logs 稍后统一打
            triage_logs: list = []
            tri = await _triage(system_debate, _fill_triage(triage_md, kp, standards, output_language), meter, model,
                                full_name, f"分诊[{kp_tag}]", log, triage_logs)
            need = tri.get("need", [])

            # 按需取证：纯代码按清单抠源码，核验限量后拼成文本；够判时清单空、这里回空串
            evidence = build_evidence(local_root, need)
            triage_logs.append(_fmt_visual(f"分诊[{kp_tag}] 取证清单 {len(need)} 项，抠得源码 {len(evidence)} 字"))

            # 正反两方拿同一份材料（拆解在 system、按需源码在 user）并行判，都跑完才进裁决
            adv_user = _fill_stance(advocate_md, kp, stars, size, evidence, output_language, standards)
            ske_user = _fill_stance(skeptic_md, kp, stars, size, evidence, output_language, standards)
            adv_logs: list = []
            ske_logs: list = []
            adv_raw, ske_raw = await asyncio.gather(
                _counted(_debate_json([{"role": "system", "content": system_debate},
                                       {"role": "user", "content": adv_user}],
                                      meter, model, full_name, f"正方[{kp_tag}]", log, adv_logs, output_language)),
                _counted(_debate_json([{"role": "system", "content": system_debate},
                                       {"role": "user", "content": ske_user}],
                                      meter, model, full_name, f"反方[{kp_tag}]", log, ske_logs, output_language)),
            )
            adv_case = {"keypoint": kp, **{k: (adv_raw or {}).get(k, "") for k in ("evidence", "where", "searched")}}
            ske_case = {"keypoint": kp, **{k: (ske_raw or {}).get(k, "") for k in ("evidence", "where", "searched")}}
            # 单条锚点过代码核验，核不上的降级成说法
            adv_case = _audit_cases(local_root, [adv_case])[0]
            ske_case = _audit_cases(local_root, [ske_case])[0]
            # 两方齐了立刻裁决这一条，不等别的 keypoint
            adj_user = _fill_adjudicate(adjudicate_md, kp, stars, size, adv_case, ske_case, output_language)
            judge_logs: list = []
            verdict = await _debate_json([{"role": "user", "content": adj_user}], meter, model,
                                         full_name, f"裁决[{kp_tag}]", log, judge_logs, output_language)
            # 这条的分诊、正方、反方、裁决四段日志按真实顺序一起打，多条之间不交叉
            _flush_block(emit_log, full_name, f"===== 🔍 分诊 · {kp} =====", triage_logs, log)
            _flush_block(emit_log, full_name, f"===== 🟢 正方 · {kp} =====", adv_logs, log)
            _flush_block(emit_log, full_name, f"===== 🔴 反方 · {kp} =====", ske_logs, log)
            _flush_block(emit_log, full_name, f"===== ⚖️ 裁决 · {kp} =====", judge_logs, log)
            return kp, adv_case, ske_case, verdict

        if progress:
            progress(full_name, rnd, used, "judging", meter.total, meter.hit_rate, "", 0, total_workers)

        # 先跑第一条焐热 system_debate 里那段拆解的前缀缓存，再并发其余，让它们大概率命中。
        # 全并发的话所有 keypoint 抢着写同一段新前缀，缓存还没建好就都 miss，拆解白算 n 遍
        first = await _judge_one(keypoints[0])
        rest = await asyncio.gather(*[_judge_one(kp) for kp in keypoints[1:]])
        results = [first] + rest

        # gather 保序，results 跟 keypoints 一一对应；每条自己的裁决按下标对齐回清单
        adv_cases = [r[1] for r in results]
        ske_cases = [r[2] for r in results]
        judged = {"keypoints": [{"status": (r[3] or {}).get("status"),
                                 "evidence": (r[3] or {}).get("evidence", "")}
                                for r in results]}
        detail = reconcile(judged, keypoints)
    else:
        detail = reconcile({}, keypoints)

    # 双方论据挂回各自 keypoint，前端展开时正反两说和裁决理由都能看
    adv_by = {c.get("keypoint", ""): c for c in adv_cases}
    ske_by = {c.get("keypoint", ""): c for c in ske_cases}
    for item in detail["keypoints"]:
        for side, by in (("advocate", adv_by), ("skeptic", ske_by)):
            c = by.get(item["keypoint"])
            if c:
                item[side] = {"evidence": c.get("evidence", ""), "where": c.get("where", ""),
                              "searched": c.get("searched", ""), "unverified": bool(c.get("unverified"))}
    scores = score_one(detail)

    result = {
        "full_name":   full_name,
        "dissection":  (llm_out or {}).get("dissection", {}),
        **scores,
        "stars":       stars,
        "size":        size,
        "detail":      detail,
        "tools_used":  used,
        "tokens":      meter.total,
        "hit_rate":    round(meter.hit_rate, 3),
        "read_files":  sorted({p for (p, _, _) in read_cache}),
        "audit":       {"repaired": repaired, "dropped": dropped_names, "clean": not dropped_names},
        "skipped":     False,
        "reason":      "",
        "degraded":    degraded,
    }

    _status(emit_log, full_name,
            f"✓ 完成 | rounds={rnd} tools={used} "
            f"keypoint {scores['keypoint_hits']}/{scores['keypoint_total']} "
            f"tokens={meter.total} 缓存命中{meter.hit_rate * 100:.0f}%", log)

    if progress:
        progress(full_name, rnd, used, "degraded" if degraded else "done", meter.total, meter.hit_rate)
    return result
