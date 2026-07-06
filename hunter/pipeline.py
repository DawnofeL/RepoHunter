"""搜索流水线的编排：需求清单 → QU 抽查询 → 检索去重 → 每仓库并发 Content Filter → 排序。

run_pipeline 是入口，边跑边 yield 中性事件 dict（{"type": ...}），跟传输格式无关，webapp
那层再把中性事件包成 SSE。真正的六角色重活在 hunter.repo_detection 里，这层只负责串联各阶段、
把进度事件汇成一条流，顺带把结果写回记忆和历史。notebook、CLI 也能直接复用这条流水线。
"""

import asyncio
import contextlib
import io
import sys
import traceback

from hunter.config import load_skill, clear_clone_dir
from hunter.cost import COST
from hunter.clients import mcp_session
from hunter.pre_filter.query_understanding import Query_Understanding
from hunter.pre_filter.keypoint_understanding import Keypoint_Understanding
from hunter.pre_filter.search_repo import Search_Repositories
from hunter.repo_detection import Repo_Detection
from hunter.memory import save_memories
from hunter import history_store as history


def _ev(event_type: str, **payload) -> dict:
    """拼一个中性事件 dict，形如 {"type": event_type, ...}。webapp 那层再包成 SSE。"""
    return {"type": event_type, **payload}


class _Tee(io.TextIOBase):
    """替身 stdout：写的时候既照常打到真终端，又把整行塞进 queue 转成 content_log 事件。

    print 可能一次写一段带好几个换行，按 \\n 切行，攒不满一整行的留在 buf 里等下次写补齐。
    """

    def __init__(self, original, queue: asyncio.Queue, sink: list | None = None) -> None:
        self.original = original
        self.queue = queue
        # sink 不为 None 时把每行 trace 也收一份，留给存历史用；None 就只推前端不收
        self.sink = sink
        self.buf = ""

    def write(self, s: str) -> int:
        self.original.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            # IPython display 在非 notebook 环境会打出 <IPython...HTML object> 的 repr，是噪声，滤掉
            if line.strip() and not line.lstrip().startswith("<IPython"):
                self.queue.put_nowait(_ev("content_log", line=line))
                if self.sink is not None:
                    self.sink.append(line)
        return len(s)

    def flush(self) -> None:
        self.original.flush()


async def _drain(task: asyncio.Task, queue: asyncio.Queue):
    # 一边等任务跑，一边把回调塞进 queue 的事件实时 yield 出去；任务结束且队列抽空才退出
    while not task.done() or not queue.empty():
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=0.1)
            yield ev
        except asyncio.TimeoutError:
            continue


async def run_pipeline(params: dict):
    """跑完整流水线，边跑边 yield 中性事件。密钥已在 server 层 configure 注入，这里只管业务。

    需求清单先经 QU 抽出搜索查询，检索去重成候选池，再每个仓库并发 Content Filter，最后排序。

    Args:
        params: 含 keypoints、languages、output_language、top_k、use_memory。

    Yields:
        中性事件 dict，按 qu→search→content→cost→done 顺序，出错则 yield error 提前结束。
    """
    try:
        # 每次 run 先清掉上一轮的全局 token 累计，否则跨 run 越加越多
        COST.clear()

        # 开新搜索前先清掉上一轮留在 data/tmp 的克隆，本轮的会一直留到下次搜索或关程序
        clear_clone_dir()

        keypoints     = params.get("keypoints", [])
        languages     = params.get("languages", [])
        output_language = params.get("output_language", "简体中文")
        top_k         = params.get("top_k", 30)
        use_memory    = params.get("use_memory", True)

        # 1. 从需求清单抽出搜索查询，keypoints 原样带回
        qu_skill = load_skill("query_understanding")
        queries = await Query_Understanding(qu_skill, keypoints)
        yield _ev("qu_done", queries=queries.get("queries", []), keypoints=queries.get("keypoints", []))

        # 1.5 把每条 keypoint 编译成一句判定标准，回显给用户看系统怎么理解需求，也传给下游避免重复编译
        kp_list = queries.get("keypoints", [])
        standards: dict = {}
        if kp_list:
            compiled = await Keypoint_Understanding(load_skill("keypoint_understanding"), kp_list)
            standards = {c["keypoint"]: c["standard"] for c in compiled}
            yield _ev("keypoints_compiled", compiled=compiled)

        # 2. 检索去重成候选池
        async with mcp_session() as session:
            repos = await Search_Repositories(session, queries, languages=languages, top_k=top_k)
        yield _ev("search_done", repos=repos, count=len(repos))

        # 3. Repo_Detection 统一编排：每个仓库直接 Content Filter，仓库间全并发。
        #    on_event 把每个仓库的进度转成 repo_event，配合 _drain 实时推给前端，notebook 那套 emoji 不掺进来
        header_md     = load_skill("system_header")
        gate_md       = load_skill("skip_gate")
        explore_md    = load_skill("content_filter")
        advocate_md   = load_skill("advocate")
        skeptic_md    = load_skill("skeptic")
        adjudicate_md = load_skill("adjudicate")

        q: asyncio.Queue = asyncio.Queue()

        def emit(full_name, stage, status, data):
            """Repo_Detection 的进度回调，把一个事件转成 repo_event 塞进 queue 等 _drain 推出去。"""
            q.put_nowait(_ev("repo_event", full_name=full_name, stage=stage, status=status, **data))

        # Content Filter 的日志走结构化回调，每条带 full_name，前端按仓库分流展开。
        # 按仓库分组攒起来，跑完挂到各自的 ranked 结果里，每个仓库的探查日志跟它的结果一起存、一起展开。
        trace_by_repo: dict = {}

        def emit_log(full_name, text):
            """explore_one 的 Visual 日志回调，转成带 full_name 的 content_log 事件推给前端，并按仓库攒进 trace。"""
            q.put_nowait(_ev("content_log", full_name=full_name, line=text))
            trace_by_repo.setdefault(full_name, []).append(text)

        # QU、检索阶段的零星 print 靠 _Tee 兜着实时推流（没有 full_name、不属于任何仓库），
        # sink 传 None 只推不收，不进历史存储
        tee = _Tee(sys.stdout, q, None)
        with contextlib.redirect_stdout(tee):
            det_task = asyncio.ensure_future(Repo_Detection(
                header_md, gate_md, explore_md, advocate_md, skeptic_md, adjudicate_md,
                repos, queries, output_language, on_event=emit, emit_log=emit_log,
                standards=standards, use_memory=use_memory))
            async for ev in _drain(det_task, q):
                yield ev
            detection = det_task.result()

        ranked = detection["ranked"]
        total  = detection["total"]

        # 每个仓库的探查日志挂到它自己的结果里，跟拆解、裁决一起走，前端在各自卡片底部展开、历史里也各存各的
        for r in ranked:
            r["trace"] = trace_by_repo.get(r.get("full_name", ""), [])

        yield _ev("content_done", ranked=ranked, total=len(ranked))

        # 把这批仓库写回记忆，本次需求清单跟着存。不受记忆开关影响（开关只挡读、不挡写，
        # 关一次漏存一次记忆库会有空洞）。save_memories 内部已裹 try/except，这里 to_thread
        # 再兜一层，写记忆失败绝不能连累已经跑出来的结果
        try:
            await asyncio.to_thread(save_memories, ranked, kp_list)
        except Exception:
            traceback.print_exc()

        # 4. 成本汇总 + 收尾
        yield _ev("cost", table=dict(COST))

        # 搜索元信息：QU 查询词、编译出的判定标准、检索候选池。每仓库的探查日志已挂在 ranked 各自结果里，这里不重复
        process = {
            "queries":   queries.get("queries", []),
            "keypoints": queries.get("keypoints", []),
            "standards": standards,
            "repos":     repos,
        }

        # 把这次完整结果落库进历史，sqlite 是阻塞 IO 用 to_thread 包，别卡事件循环；
        # 存失败只告警，绝不能让已经跑出来的结果因为存历史失败而报错
        try:
            await asyncio.to_thread(
                history.save_query, params, ranked, len(ranked), dict(COST), process,
            )
        except Exception:
            traceback.print_exc()

        yield _ev("done")

    except Exception as e:
        traceback.print_exc()
        yield _ev("error", message=f"{type(e).__name__}: {e}")
