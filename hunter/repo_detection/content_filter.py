"""Content Filter 的编排。

本模块的 `Repo_Detection` 把检索来的候选仓库逐个过 gate 判要不要深挖，要的才做 Content
Filter，最后排序。顶部三个 `_fill_*` 闭包把 skill 模板填成真实的 system、gate user 和
content user（用字符串替换不用 str.format，避开 README 里的花括号）。进度不在这里画，
只通过 on_event 回调吐纯数据，谁要可视化谁自己接（notebook 画 emoji 表、webapp 转 SSE）。
仓库之间全并发。
"""

import asyncio
import json

import httpx

from hunter import config
from hunter.repo_detection.prefetch import fetch_readme, clean_readme, fetch_tree, fetch_meta
from hunter.repo_detection.prompt_text import texts as _ptexts
from hunter.repo_detection.explorer import explore_one
from hunter.repo_detection.debate import _kp_with_standard
from hunter.repo_detection.scoring import rank
from hunter.pre_filter.keypoint_understanding import Keypoint_Understanding
from hunter.history import load_memories


def _fill_system(template: str, repo: dict, readme: str, tree: str, size: int,
                 output_language: str = "简体中文") -> str:
    """把 system 模板的六个占位填成真值，返回给 Content Filter 用的那份 system。

    六个占位对应项目的六样资料：{full_name} 仓库全名、{description} 仓库简介、
    {topics} 标签、{readme} 清洗后的 README、{tree} 两层目录结构树、{size} 仓库体积（MB）。
    空值占位（无内容时的「（无）」）按 output_language 走，英文搜索时用 "(none)"。

    用字符串替换而不用 str.format，因为 readme 和 tree 正文里有花括号会被 format 当占位符。

    Args:
        template: system_header.md 内容，带 {full_name}、{readme} 等占位。
        repo:     仓库 dict，取 full_name、description、topics。
        readme:   清洗后的 README 文本。
        tree:     两层目录结构文本。
        size:     仓库体积，单位 KB，换算成 MB 填进去给模型判体量。
    Returns:
        填好六样资料的 system 字符串。
    """

    # topics 是字符串列表，拼成逗号分隔的一行，空的话填占位免得出现空白行
    none = _ptexts(output_language)["none"]
    topics = ", ".join(repo.get("topics") or []) or none

    # size 是 KB，换算成 MB 给模型看，保留一位小数
    size_mb = round(size / 1024, 1)
    return (template
            .replace("{full_name}", repo["full_name"])
            .replace("{description}", repo.get("description") or none)
            .replace("{topics}", topics)
            .replace("{readme}", readme or none)
            .replace("{tree}", tree or none)
            .replace("{size}", str(size_mb)))


def _fill_explore(template: str, output_language: str) -> str:
    """把 Content Filter 提示词 user 里的输出语言占位填成真值。content 不看 keypoint，只描述项目架构。"""
    return template.replace("{output_language}", output_language)


def _fill_gate(template: str, keypoints: list, standards: dict[str, str] | None = None,
               output_language: str = "简体中文") -> str:
    """把 gate 提示词 user 里的 keypoint 清单占位填成真值。有 standards 就把判定标准拼进每条。"""
    kp_texts = [_kp_with_standard(kp, standards, output_language) for kp in keypoints]
    return template.replace("{keypoints}", json.dumps(kp_texts, ensure_ascii=False))


async def Repo_Detection(header_md: str, gate_md: str, explore_md: str,
                         advocate_md: str, skeptic_md: str, adjudicate_md: str, repos: list,
                         qu: dict | None = None, query: str | list[str] | None = None,
                         output_language: str = "简体中文",
                         top_n: int | None = None, on_event=None, log: str = "Visual",
                         emit_log=None, standards: dict[str, str] | None = None,
                         use_memory: bool = True, triage_md: str | None = None) -> dict:
    """对每个候选仓库先过 gate 判要不要深挖，要的才 Content Filter，判定走辩论裁决，最后排序返回。仓库之间全并发。

    Args:
        header_md:       system_header.md 内容，system 模板（角色加六样占位）。
        gate_md:         skip_gate.md 内容，gate user 模板（判要不要深挖）。
        explore_md:      content_filter.md 内容，Content Filter user 模板（content 只摆事实）。
        advocate_md:     advocate.md 内容，正方模板（带工具为 keypoint 找支持证据）。
        skeptic_md:      skeptic.md 内容，反方模板（带工具找反证）。
        adjudicate_md:   adjudicate.md 内容，裁决模板（拿双方过审论据判 hit/miss）。
        repos:           Search_Repositories 的输出，每项含 full_name、description、topics。
        qu:              Query_Understanding 输出，取其 keypoints；不传就用 query。
        query:           自然语言需求，跟前端「一条条填 keypoint」同一件事，只是省去手动拆分：
                         传一整句字符串就当成唯一一条 keypoint，传 list 就当成多条 keypoint 原样用。
                         qu 和 query 都传时 qu 优先；都不传就是空需求清单。
        output_language: LLM 散文字段（evidence、purpose、architecture 等）用的语言，默认简体中文。
        top_n:           排序后只留前几个，None 全留。
        on_event:        可选进度回调 on_event(full_name, stage, status, data)，UI 中立、不画任何东西。
                         stage 固定 "content"，status 是 running/judging/done/degraded/skipped，
                         data 带 round、tools、tokens、hit_rate，skip 时 data.reason 是那句跳过理由。
        log:             透传给 explore_one 的日志模式，"Visual"（默认）或 "ReAct"，见 explore_one 说明。
        emit_log:        透传给 explore_one 的 Visual 日志回调 emit_log(full_name, text)。None 时
                         explore_one 用自己的默认（直接 print），webapp 传自己的回调按仓库分流。
        standards:       {keypoint 原文: 判定标准} 映射，调用方（如 webapp）预先编译好传进来避免
                         重复编译、顺便回显给用户；None 就内部自己编译。
        use_memory:      前端记忆开关。True（默认）就先查一遍记忆库，命中有拆解的仓库跳过 explorer
                         直接复用；False 完全不查记忆，所有仓库该拆就拆，跟没有记忆模块一样。
                         开关只挡「读记忆」，不挡写回（写回在 webapp 层，跟这里无关）。
        triage_md:       triage.md 内容，分诊模板。None 就内部自己 load_skill 加载。

    Returns:
        含 ranked、total 的 dict。ranked 是 Content Filter 后按 keypoint 命中排序的结果，total 是仓库总数。
    """
    if qu is not None:
        keypoints = qu.get("keypoints", [])
    elif isinstance(query, str):
        keypoints = [query]
    elif query:
        keypoints = list(query)
    else:
        keypoints = []

    # 把每条 keypoint 编译成一句判定标准，拼进 gate 和辩论三方的 prompt，让所有仓库、所有判定
    # 用同一把尺子判，消掉跨仓库标准漂移。编译失败降级为空标准（等于没这个模块），不阻塞主流程。
    # 调用方（webapp）预编译好传进来就直接用，省一次编译、也方便它先回显给用户
    if standards is None:
        standards = {}
        if keypoints:
            kp_skill = config.load_skill("keypoint_understanding")
            compiled = await Keypoint_Understanding(kp_skill, keypoints, output_language)
            standards = {c["keypoint"]: c["standard"] for c in compiled}

    # 分诊 skill 没传就自己加载，调用方（webapp/pipeline）一般预加载好传进来
    if triage_md is None:
        triage_md = config.load_skill("triage")

    # gate skill 的输出语言占位符在这里统一填好，所有仓库共用同一份填好的模板
    gate_md = gate_md.replace("{output_language}", output_language)

    model = config.MODELS["content_filter"]
    n = len(repos)

    # 记忆开关开着就一次性查这批仓库的记忆，命中且存过拆解的，下面复用它跳过 explorer。
    # 关着 memo 就是空的，所有仓库照常拆。查记忆是纯读，写回在 webapp 层，不受这个开关影响
    memo: dict = {}
    if use_memory:
        # 按当前搜索语言查复用：只命中有这门语言那格拆解的，别的语言那格不算、会照常重新深挖
        hit = load_memories([r["full_name"] for r in repos], output_language)
        memo = {fn: m["dissection"] for fn, m in hit.items() if m.get("dissection")}
        if memo:
            print(f"[repo_memory] 命中 {len(memo)}/{n} 个仓库的记忆，这些跳过 explorer 复用拆解")

    # 进度不在这里画，content 每轮的回调先转成统一的 on_event
    def on_content(full_name: str, rnd: int, used: int, status: str, tokens: int, hit_rate: float,
                   reason: str = "", debate_done: int = 0, debate_total: int = 0) -> None:
        """把 explore_one 每轮和收尾的回调转成统一的 on_event content 事件。reason 只在 skip 时非空,
        debate_done/debate_total 只在 judging 时非零(辩论 worker 完成数/总数)。"""
        if on_event:
            on_event(full_name, "content", status,
                     {"round": rnd, "tools": used, "tokens": tokens, "hit_rate": hit_rate, "reason": reason,
                      "debate_done": debate_done, "debate_total": debate_total})

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {config.GITHUB_PAT}", "Accept": "application/vnd.github+json"},
        timeout=30.0,
    ) as client:
        async def process_one(repo: dict) -> dict:
            """跑单个仓库的完整流程：预抓资料、拼 system 和 user、Content Filter，返回 result。"""
            full_name = repo["full_name"]
            owner, name = full_name.split("/", 1)

            # 预抓 readme、两层目录树、stars/size 并发拉；size 拼进 system 给模型判体量
            raw, tree, (stars, size) = await asyncio.gather(
                fetch_readme(client, owner, name),
                fetch_tree(client, owner, name),
                fetch_meta(client, owner, name),
            )
            readme = clean_readme(raw) if raw else ""
            system = _fill_system(header_md, repo, readme, tree, size, output_language)
            gate_user = _fill_gate(gate_md, keypoints, standards, output_language)
            explore_user = _fill_explore(explore_md, output_language)
            # emit_log 为 None 就不传，让 explore_one 用它自己的默认（直接 print）
            extra = {"emit_log": emit_log} if emit_log is not None else {}
            # memo 里有这仓库就把存的拆解传进去，explore_one 拿它跳过 explorer；没有就传 None 照常拆
            return await explore_one(full_name, system, gate_user, explore_user,
                                     advocate_md, skeptic_md, adjudicate_md,
                                     keypoints, stars, size, model, output_language, on_content, log,
                                     standards=standards, cached_dissection=memo.get(full_name),
                                     triage_md=triage_md, **extra)

        results = await asyncio.gather(*[process_one(r) for r in repos])

    # 全部仓库按 keypoint 命中数排序，没有淘汰环节
    ranked = rank(results, top_n)
    return {"ranked": ranked, "total": n}
