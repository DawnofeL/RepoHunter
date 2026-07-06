"""锚点审计与自愈：核对 dissection 和辩论 case 里的 where 锚点在克隆里是否真实存在。

_audit_anchors 逐个查 where 的路径和符号，_repair_msg 把对不上的拼成重修指令打回给模型，
_drop_bad_designs 兜底删掉仍对不上的设计点，_audit_cases 对辩论一方的论据锚点做同样核验。
"""

import os

from hunter.repo_detection.agent_tools import _safe_join


def _audit_anchors(local_root: str, llm_out: dict | None) -> list[dict]:
    """
    核对 key_designs 里每个 where 锚点在克隆目录里是否真实存在，返回对不上的清单。

    where 是「路径:符号」格式、多个逗号隔开。路径用 os.path.exists 查，带符号且路径是
    文件的再读该文件确认符号确实出现在里面。llm_out 解析失败（None）或没有 key_designs
    时返回空清单。

    Args:
        local_root: 克隆根目录绝对路径。
        llm_out:    模型最终输出的 dict，可能为 None。
    Returns:
        坏锚点清单，每项含 name（设计点名）、anchor（出问题的那段 where）、reason。
    """
    bad = []
    designs = (llm_out or {}).get("dissection", {}).get("key_designs", []) or []
    for d in designs:
        name = d.get("name", "")

        # where 可能写了多个锚点，逗号切开逐个查
        for seg in (d.get("where", "") or "").split(","):
            seg = seg.strip()
            if not seg:
                continue

            # 每段是「路径:符号」，符号可省。先把路径拼到克隆目录下查存在性
            path, _, symbol = seg.partition(":")
            path = path.strip()
            symbol = symbol.strip()
            full = _safe_join(local_root, path)
            if full is None or not os.path.exists(full):
                bad.append({"name": name, "anchor": seg, "reason": "路径不存在"})
                continue

            # 路径在、又带了符号且指向文件，就读文件确认符号确实在里面
            if symbol and os.path.isfile(full):
                try:
                    with open(full, encoding="utf-8", errors="replace") as f:
                        if symbol not in f.read():
                            bad.append({"name": name, "anchor": seg,
                                        "reason": f"文件在，但没搜到符号 {symbol}"})
                except Exception:
                    pass
    return bad


def _repair_msg(bad: list[dict]) -> str:
    """
    把坏锚点清单拼成一条重修指令，追加进对话让模型只改这几条、重出完整 JSON。

    Args:
        bad: _audit_anchors 返回的坏锚点清单。
    Returns:
        给模型的重修提示文本。
    """
    lines = "\n".join(f'- "{b["name"]}" 的 where "{b["anchor"]}"：{b["reason"]}' for b in bad)
    return (
        "系统提示:你刚才输出的 key_designs 里，下面这些 where 引用在仓库里核对不上：\n"
        f"{lines}\n"
        "请只针对这几条重新用工具核对源码：用 glob_files / grep_code / read_file 找到这个"
        "设计点真正对应的文件和符号，把 where 改成查得到的正确锚点。如果某条设计点本身在"
        "源码里找不到依据、站不住，就把它从 key_designs 里删掉，不要硬留。其余 key_designs "
        "和别的字段保持原样。改完重新输出完整 JSON。"
    )


def _drop_bad_designs(llm_out: dict, bad: list[dict]) -> dict:
    """
    把仍然对不上的 key_design 条目按设计点名从 dissection 里删掉，兜底不给用户看错锚点。

    Args:
        llm_out: 模型最终输出的 dict。
        bad:     重修后仍对不上的坏锚点清单。
    Returns:
        删掉问题条目后的 llm_out（原地改 dissection.key_designs，删光就成空数组）。
    """
    bad_names = {b["name"] for b in bad}
    diss = llm_out.get("dissection", {})
    designs = diss.get("key_designs", []) or []
    diss["key_designs"] = [d for d in designs if d.get("name", "") not in bad_names]
    return llm_out


def _audit_cases(local_root: str, cases: list) -> list:
    """
    对一方论据的 where 逐条核验，核不上的置空并标 unverified，裁决时只当说法不当事实。

    复用 _audit_anchors：把单条 case 拼成它认识的 key_designs 形状喂进去，有坏锚点就作废。

    Args:
        local_root: 克隆根目录。
        cases:      一方输出的 cases 数组。
    Returns:
        清洗后的 cases 数组，非 dict 条目丢弃。
    """
    cleaned = []
    for c in cases or []:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        where = (c.get("where") or "").strip()
        if where:
            fake = {"dissection": {"key_designs": [{"name": c.get("keypoint", ""), "where": where}]}}
            if _audit_anchors(local_root, fake):
                c["where"] = ""
                c["unverified"] = True
        cleaned.append(c)
    return cleaned
