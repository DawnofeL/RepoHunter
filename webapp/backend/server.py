import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from hunter import config
from hunter import clone
from hunter import memory
from hunter import history
from hunter import dev
from hunter import dev_store
from hunter import creds_store as creds
from hunter.chat import stream_chat
from hunter.pipeline import run_pipeline
from webapp.backend.events import sse_event


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时清扫过期克隆：超过一天没用过的删掉，一天内的留着给搜索和对话复用。
    # 关闭时不清，克隆本来就要跨次保留，靠每次启动的清扫控制磁盘
    removed = clone.sweep_clones()
    if removed:
        print(f"启动清扫：删掉 {removed} 个过期克隆")
    yield


app = FastAPI(title="RepoHunter", lifespan=lifespan)

# 建搜索账本表、仓库账本表（同一个 history.db），对话历史表、提取记忆表，不存在才建，
# server 一起来就保证表都在。init_notes 给 chat_sessions 补笔记列，要在 init_chat_sessions 建好表之后
history.init_runs()
history.init_repo_memory()
memory.init_chat_sessions()
memory.init_chat_memories()
memory.init_notes()
# 开发者监控的审计落库表，独立的 dev_audit.db
dev_store.init_db()

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class RunRequest(BaseModel):
    # 前端运行时填的密钥与各阶段模型，每次 run 注入；留空表示沿用 .env / config 默认
    deepseek_api_key: str = ""
    github_pat: str = ""
    # 阶段名到模型名的 dict，前端组装好六键发上来，空值的阶段 configure 会跳过保持默认
    models: dict = {}
    # 业务参数：keypoints 是用户一条条写的需求，languages 走检索硬过滤
    keypoints: list[str] = []
    languages: list[str] = []
    output_language: str = "简体中文"
    top_k: int = 20
    # 记忆开关，默认开：命中记忆就复用拆解跳过 explorer；关了就全部照常拆
    use_memory: bool = True


class CredsRequest(BaseModel):
    # 前端记住的凭证和模型，进入时存一份，下次自动带出来
    deepseek_api_key: str = ""
    github_pat: str = ""
    model: str = ""
    # 各阶段模型配置，跟凭证一起记住，下次带出来预填设置界面
    models: dict = {}


class ChatRequest(BaseModel):
    # 右栏对话：多轮历史 + 注入的 repo 全名，密钥单独带一份好独立注入
    deepseek_api_key: str = ""
    # 各阶段模型 dict，对话侧只用到 chat/recall/extract，多发无妨
    models: dict = {}
    messages: list[dict] = []
    context: list[str] = []
    # 会话 id，提取记忆按它读写游标；空串就不触发提取（只答不提）
    session_id: str = ""
    # 界面语言，决定助手回答语言和 system 各段包装文案（跟搜索侧 RunRequest 同一套取值）
    output_language: str = "简体中文"


class ChatSaveRequest(BaseModel):
    # 存一次对话：前端每聊完一轮把完整快照发上来，session_id 定这轮存进哪一行。
    # 不带 title，新建的默认名字和改名都在后端管，前端没资格覆盖
    session_id: str = ""
    context: list[str] = []
    messages: list[dict] = []


class ChatSessionPatch(BaseModel):
    # 改会话元数据：标题、置顶，两个都可选，传了哪个就改哪个
    title: str | None = None
    pinned: bool | None = None


class DevToggle(BaseModel):
    # 开发者监控总开关，全局的，开了对话每轮才记中间过程
    enabled: bool = False


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/creds")
async def creds_get() -> dict:
    # 页面加载时前端来读，拿去自动填两个框
    return await asyncio.to_thread(creds.load_creds)


@app.post("/creds")
async def creds_save(req: CredsRequest) -> dict:
    # 进入时存这次填的，改了就覆盖旧的
    await asyncio.to_thread(creds.save_creds, req.deepseek_api_key, req.github_pat, req.model, req.models)
    return {"status": "ok"}


@app.post("/run")
async def run(req: RunRequest) -> EventSourceResponse:
    # 先把前端填的密钥模型注入全局 config，再跑流水线。单用户假设，并发多 run 会互相覆盖配置
    config.configure(
        deepseek_api_key=req.deepseek_api_key,
        github_pat=req.github_pat,
        models=req.models,
    )
    params = {
        "keypoints":       req.keypoints,
        "languages":       req.languages,
        "output_language": req.output_language,
        "top_k":           req.top_k,
        "use_memory":      req.use_memory,
    }

    # hunter.pipeline 吐中性事件 dict，这层把每个包成 SSE 帧再流出去
    async def _sse():
        async for ev in run_pipeline(params):
            yield sse_event(ev)

    return EventSourceResponse(_sse())


@app.post("/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    # 对话密钥单独注入，各阶段模型走 models dict，hunter.chat 里取 chat/recall/extract
    config.configure(
        deepseek_api_key=req.deepseek_api_key,
        models=req.models,
    )

    # hunter.chat 吐中性事件 dict，这层把每个包成 SSE 帧再流出去
    async def _sse():
        async for ev in stream_chat(req.messages, req.context, req.session_id, req.output_language):
            yield sse_event(ev)

    return EventSourceResponse(_sse())


@app.get("/history")
async def history_list() -> dict:
    # 只返摘要列，列表页不取大块的 results/cost。sqlite 是阻塞 IO 用 to_thread 包
    items = await asyncio.to_thread(history.list_runs)
    return {"items": items}


@app.get("/history/{qid}")
async def history_get(qid: int):
    # 取完整一次搜索，点开详情用（拆解按名字从仓库账本 JOIN 回来），不存在回 404
    item = await asyncio.to_thread(history.get_run, qid)
    if item is None:
        return JSONResponse({"error": "历史不存在"}, status_code=404)
    return item


@app.delete("/history/{qid}/repo")
async def history_delete_repo(qid: int, full_name: str):
    # 单删一次搜索里的一个仓库，full_name 带斜杠走 query 参数
    ok = await asyncio.to_thread(history.delete_repo_from_run, qid, full_name)
    if not ok:
        return JSONResponse({"error": "历史不存在"}, status_code=404)
    return {"status": "ok"}


@app.delete("/history/{qid}/process")
async def history_delete_process(qid: int) -> dict:
    # 只删搜索过程，结果照样留着
    await asyncio.to_thread(history.delete_process, qid)
    return {"status": "ok"}


@app.delete("/history/{qid}")
async def history_delete(qid: int) -> dict:
    # 删掉一整次搜索（仓库账本不动）
    await asyncio.to_thread(history.delete_run, qid)
    return {"status": "ok"}


@app.delete("/history")
async def history_clear() -> dict:
    # 清空所有搜索流水账（仓库账本不动，各清各的）
    await asyncio.to_thread(history.clear_runs)
    return {"status": "ok"}


@app.get("/memory")
async def memory_list() -> dict:
    # 列出仓库账本所有仓库的摘要，列表页用，不取大块的 dissection
    items = await asyncio.to_thread(history.list_memories)
    return {"items": items}


@app.get("/memory/repo")
async def memory_get(full_name: str):
    # 取一条完整仓库记忆（含 seen_runs 时间线），点开详情用。full_name 带斜杠走 query 参数，不存在回 404
    item = await asyncio.to_thread(history.get_memory, full_name)
    if item is None:
        return JSONResponse({"error": "记忆不存在"}, status_code=404)
    return item


@app.delete("/memory/repo")
async def memory_delete(full_name: str) -> dict:
    # 删掉一条仓库记忆，full_name 带斜杠走 query 参数
    await asyncio.to_thread(history.delete_memory, full_name)
    return {"status": "ok"}


@app.delete("/memory")
async def memory_clear() -> dict:
    # 清空仓库账本（搜索流水账不动）
    await asyncio.to_thread(history.clear_repos)
    return {"status": "ok"}


@app.post("/chat/session")
async def chat_session_save(req: ChatSaveRequest) -> dict:
    # 前端每聊完一轮存一次完整对话，upsert 到 session_id 那一行。标题不归这个接口管
    await asyncio.to_thread(
        memory.save_session, req.session_id, req.context, req.messages
    )
    return {"status": "ok"}


@app.patch("/chat/session/{sid}")
async def chat_session_patch(sid: str, req: ChatSessionPatch) -> dict:
    # 改会话元数据：重命名、置顶/取消置顶，两个都可选，各自独立生效
    if req.title is not None:
        await asyncio.to_thread(memory.rename_session, sid, req.title)
    if req.pinned is not None:
        await asyncio.to_thread(memory.set_pinned, sid, req.pinned)
    return {"status": "ok"}


@app.get("/chat/session")
async def chat_session_list() -> dict:
    # 列出所有会话的摘要，翻旧对话用，不取大块的 messages
    items = await asyncio.to_thread(memory.list_sessions)
    return {"items": items}


@app.get("/chat/session/{sid}")
async def chat_session_get(sid: str):
    # 取一次完整对话，点开还原接着聊，不存在回 404
    item = await asyncio.to_thread(memory.get_session, sid)
    if item is None:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    return item


@app.delete("/chat/session/{sid}")
async def chat_session_delete(sid: str) -> dict:
    # 删掉一次会话
    await asyncio.to_thread(memory.delete_session, sid)
    return {"status": "ok"}


@app.get("/chat/dev")
async def chat_dev_status() -> dict:
    # 开发者监控现在开着没
    return {"enabled": dev.is_enabled()}


@app.post("/chat/dev")
async def chat_dev_toggle(req: DevToggle) -> dict:
    # 开/关开发者监控，全局生效
    if req.enabled:
        dev.enable()
    else:
        dev.disable()
    return {"enabled": dev.is_enabled()}


# 审计路由必须排在 /chat/dev/{sid} 前面注册，否则 audit 会被当成 sid 吃掉
@app.get("/chat/dev/audit")
async def chat_dev_audit_list() -> dict:
    # 列出落库审计过的会话摘要，审计浏览入口用
    items = await asyncio.to_thread(dev_store.list_audit_sessions)
    return {"items": items}


@app.get("/chat/dev/audit/{sid}")
async def chat_dev_audit_get(sid: str) -> dict:
    # 取某次会话落库的完整记录，不受内存 40 条上限限制，重启后仍在
    records = await asyncio.to_thread(dev_store.get_audit_records, sid)
    return {"records": records}


@app.delete("/chat/dev/audit/{sid}")
async def chat_dev_audit_delete(sid: str) -> dict:
    # 清掉某次会话的审计记录
    await asyncio.to_thread(dev_store.clear_audit, sid)
    return {"status": "ok"}


@app.delete("/chat/dev/audit")
async def chat_dev_audit_clear() -> dict:
    # 清掉全部审计记录
    await asyncio.to_thread(dev_store.clear_audit, None)
    return {"status": "ok"}


@app.get("/chat/dev/{sid}")
async def chat_dev_snapshot(sid: str) -> dict:
    # 面板打开时拉一份现状：已记录的中间过程 + 当前笔记 + 本会话提取出的记忆 + 全局通用记忆
    records = dev.snapshot(sid)
    note_text = await asyncio.to_thread(memory.get_notes, sid)
    session_mem = await asyncio.to_thread(memory.list_by_session, sid)
    general_mem = await asyncio.to_thread(memory.list_general)
    return {
        "enabled": dev.is_enabled(),
        "records": records,
        # 包成 {notes: 正文}，前端 renderDevState 读的是 snap.note.notes
        "note": {"notes": note_text},
        "session_memories": session_mem,
        "general_memories": general_mem,
    }


@app.get("/chat/dev/{sid}/stream")
async def chat_dev_stream(sid: str) -> EventSourceResponse:
    # 实时推这个会话的监控记录。面板订这条长连接，record 一有新记录就流过来。
    # 只推实时，历史靠上面 snapshot 那个 GET 先拉一份，避免重放和实时重复
    async def _sse():
        q = dev.subscribe(sid)
        try:
            while True:
                rec = await q.get()
                yield sse_event(rec)
        finally:
            dev.unsubscribe(sid, q)

    return EventSourceResponse(_sse())


@app.get("/")
async def index():
    # 前端 index.html 在 chunk 2 加入，还没有就先回个状态提示
    f = FRONTEND / "index.html"
    if f.exists():
        return FileResponse(f)
    return JSONResponse({"status": "RepoHunter backend up", "note": "前端将在 chunk 2 加入"})


# 前端静态资源（css/js）目录，chunk 2 放文件，先挂上，目录已建好不会报错
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
