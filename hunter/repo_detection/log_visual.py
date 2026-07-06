"""日志格式化与输出：Visual 和 ReAct 两种模式，供 explorer 主循环和辩论 worker 共用。

Visual 模式整理排版、按行截断，ReAct 模式原样打印发给 LLM 的消息和回复。一堆 _fmt_*/_visual/
_status/_flush_block 只管把一条日志格式化成文本，加不加仓库前缀、往哪打由调用方决定。
"""

import json

from hunter.cost import TokenMeter


def _fmt_obj(obj: object) -> str:
    """
    把一个对象格式化成便于阅读的字符串，dict/list 走 json.dumps 缩进展开，字符串原样返回。

    工具调用参数、assistant 消息、辩论 cases 这类结构化内容用 json.dumps 才能看清层级；
    read_file/grep_code 这类工具返回本身就是纯字符串，套 JSON 反而会把换行转义成
    字面量 \\n，直接原样返回让换行自然生效。这里本身不截断，_log_raw（ReAct）原样用、
    _visual（Visual）再按行截断，截断与否留给调用它的函数决定。

    Args:
        obj: 要打印的内容，dict、list 或字符串。
    Returns:
        格式化后的字符串，长度不变。
    """
    if isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return str(obj)


def _log_raw(full_name: str, title: str, obj: object) -> None:
    """ReAct 模式下用：原样打印一条消息或对象，不裁剪不拼接，标题只是一行提示不算内容。"""
    print(f"[{full_name}] {title}")
    print(_fmt_obj(obj))


def _clip(s: object, n: int) -> str:
    """
    把内容压成一行并截断到 n 字，让并发日志每条都在一行内显示。

    Args:
        s: 要显示的内容，任意类型，先转成字符串。
        n: 最大字数，超出就截断加省略号。
    Returns:
        换行替换成空格、超长截断后的字符串。
    """
    text = str(s).replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def _clip_lines(s: str, max_lines: int = 15) -> str:
    """
    按行截断，保留原始换行不压平，超过 max_lines 行就砍掉尾部并标注还剩多少行。

    list_tree、read_file、grep_code 这类工具返回本身是靠换行对齐的多行表格，用 _clip 会
    把换行替换成空格、把表格压成一坨乱麻，所以这里按行截断、每行的对齐原样保留。

    Args:
        s:         要截断的多行字符串。
        max_lines: 最多保留多少行。
    Returns:
        最多 max_lines 行，超出时末尾加一行「…还有 N 行」。
    """
    lines = str(s).split("\n")
    if len(lines) <= max_lines:
        return s
    kept = lines[:max_lines]
    return "\n".join(kept) + f"\n…还有 {len(lines) - max_lines} 行"


def _default_emit(full_name: str, text: str) -> None:
    """默认日志回调：直接 print，不带仓库前缀。notebook 单仓库用这个，前缀无意义。"""
    print(text)


def _fmt_visual(title: str, obj: object = None) -> str:
    """Visual 模式把一条日志格式化成文本（标题独立成行、内容按行截断），只格式化不输出。obj 为 None 只出标题。"""
    if obj is None:
        return title
    return f"{title}:\n{_clip_lines(_fmt_obj(obj))}"


def _fmt_react(title: str, obj: object) -> str:
    """ReAct 模式把一条日志格式化成文本（标题一行 + 原样完整内容，不截断），只格式化不输出。"""
    return f"{title}\n{_fmt_obj(obj)}"


def _visual(emit_log, full_name: str, title: str, obj: object = None) -> None:
    """
    Visual 模式发一条日志：标题独立成行、内容按行截断，通过 emit_log 上报，不带仓库前缀。

    前缀不在这里加，谁消费 emit_log 谁决定要不要按仓库分组（notebook 单仓库直接打、
    webapp 按 full_name 分流）。obj 为 None 时只发标题那一行（用于 Round 分隔、注入提醒等纯标记）。

    Args:
        emit_log:  日志回调 emit_log(full_name, text)，把一整块文本上报出去。
        full_name: owner/name，供回调那层按仓库归类。
        title:     这条日志的标题，如 "Round 3" 或 "list_tree 返回"。
        obj:       日志内容，dict/list 会缩进展开、字符串按行截断；None 表示只发标题。
    """
    emit_log(full_name, _fmt_visual(title, obj))


def _token_line(rnd: int, meter: TokenMeter) -> str:
    """拼一行当前仓库至今累计的 token 消耗（带命中率），不带仓库前缀，前缀由调用方决定。"""
    return (
        f"[Round {rnd}] 💰 累计 prompt {meter.prompt}"
        f"(命中{meter.hit}/未命中{meter.miss}, 命中{meter.hit_rate * 100:.0f}%)"
        f" + 输出 {meter.completion} = {meter.total} tok"
    )


def _emit_token_line(emit_log, full_name: str, rnd: int, meter: TokenMeter, log: str) -> None:
    """打一行 token 消耗：ReAct 直接 print 带仓库前缀，Visual 走 emit_log 回调不带前缀。"""
    line = _token_line(rnd, meter)
    if log == "ReAct":
        print(f"[{full_name}] {line}")
    else:
        emit_log(full_name, line)


def _status(emit_log, full_name: str, text: str, log: str) -> None:
    """打一条流程状态行（gate 跳过、开始探查、完成等）：ReAct 直接 print 带前缀，Visual 走 emit_log。"""
    if log == "ReAct":
        print(f"[{full_name}] {text}")
    else:
        emit_log(full_name, text)


def _flush_block(emit_log, full_name: str, header: str, lines: list, log: str) -> None:
    """
    打一个辩论块：先一行分隔标题（如「=== 🟢 正方 ===」），再把这一方攒好的日志逐条打出来。

    正反方并行跑时各攒各的日志，跑完用这个函数分块打，一方一整块不被另一方插断。ReAct 直接
    print 带仓库前缀，Visual 走 emit_log，两种模式共用同一套分块结构。

    Args:
        emit_log:  Visual 日志回调。
        full_name: owner/name。
        header:    分隔标题，如 "=== 🟢 正方 ==="。
        lines:     这一块攒好的日志文本行。
        log:       "ReAct" 或 "Visual"。
    """
    _status(emit_log, full_name, header, log)
    for line in lines:
        _status(emit_log, full_name, line, log)
