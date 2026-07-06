"""对齐与算分：把模型给的 keypoint 判定对齐回清单、数命中、排序。

模型输出的 keypoint 判定代码不直接信，`_align` 按位置对齐回我们发出的清单、漏判
补 miss。`reconcile` 对齐 keypoints，`score_one` 数命中数，`rank` 按命中数排序、平手
看 stars、降级沉底。
"""

# keypoint 只有命中和没命中两态，模型给别的（含空）一律按没命中算
VALID_STATUS = {"hit", "miss"}


def _align(sent_items: list[str], llm_items: list, key: str) -> list[dict]:
    """把 LLM 给的判定按位置对齐回我们发出的清单，第 i 条判定对应第 i 条需求。

    不靠模型照抄 keypoint 原文来匹配：模型只要改写措辞、翻译、把编译进 prompt 的判定标准
    一起抄进 keypoint 字段，文本就对不上、整条判定丢失、全补 miss。改成按位置绑定，发出的
    清单有序、模型按同序逐条判，代码按下标取 status/evidence，keypoint 字段一律用我们发出
    的原文，模型写的 keypoint 字段不看。这跟辩论 worker 按派发顺序绑定 case 是同一套思路。

    模型漏判（输出条数少）时缺的补 miss，多判的忽略，保证每条发出的需求都有结果。

    Args:
        sent_items: 发出去的 keypoint 原文清单，有序。
        llm_items:  LLM 那一组判定输出，按同序取用。
        key:        结果里 keypoint 文本挂在哪个键上。
    Returns:
        对齐后的判定清单，长度和 sent_items 一致，keypoint 字段用原文。
    """
    aligned = []
    for i, text in enumerate(sent_items):
        found = llm_items[i] if i < len(llm_items) and isinstance(llm_items[i], dict) else None
        status = found.get("status") if found else None
        if status not in VALID_STATUS:
            status = "miss"
        evidence = found.get("evidence", "") if found else ""
        aligned.append({key: text, "status": status, "evidence": evidence})
    return aligned


def reconcile(llm_out: dict, keypoints: list[str]) -> dict:
    """把 LLM 输出的 keypoint 判定对齐回发出的清单，漏判的补成 miss。"""
    return {
        "keypoints": _align(keypoints, llm_out.get("keypoints", []), "keypoint"),
    }


def _count(items: list[dict]) -> tuple[int, int]:
    """数一组判定里 hit、miss 各多少。"""
    hit = sum(1 for it in items if it["status"] == "hit")
    miss = sum(1 for it in items if it["status"] == "miss")
    return hit, miss


def score_one(detail: dict) -> dict:
    """对齐后的 detail 数出 keypoint 命中数和总数，不算分也不设过线。

    只数 hit，miss 一律算没命中，展示成「命中数/总数」。
    """
    k_hit, _ = _count(detail["keypoints"])

    return {
        "keypoint_hits":  k_hit,
        "keypoint_total": len(detail["keypoints"]),
    }


def rank(results: list[dict], top_n: int | None = None) -> list[dict]:
    """按 keypoint 命中数降序排，平手看 stars；gate 跳过的和降级的都沉到最底，可选截断 top_n。"""
    def sort_key(r: dict) -> tuple:
        """排序键：跳过的垫最底，其次降级的，再按命中数、平手看 stars。"""
        return (not r.get("skipped", False), not r.get("degraded", False),
                r["keypoint_hits"], r.get("stars", 0))

    ordered = sorted(results, key=sort_key, reverse=True)
    return ordered[:top_n] if top_n else ordered
