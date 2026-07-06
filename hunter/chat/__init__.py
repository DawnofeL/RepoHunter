"""chat 占位模块。

右栏对话功能依赖 memory，本次只做 repo detection + pre filter 的网页端，chat 一并休眠。
server 的 /chat 路由 import 了 stream_chat，这里放一个空异步生成器顶着，被调到就吐一条错误
事件、不真聊。搜索路径不碰 /chat，休眠不影响主流程。将来真迁 chat，用完整 chat 包替换本
文件即可，server.py 一个字不用改。
"""


async def stream_chat(messages: list, context: list, session_id: str):
    """占位：真实版流式调模型答话，这里只吐一条错误事件表示未启用。

    保持异步生成器形态（server 那边 async for 遍历它），吐一条 error 中性事件，前端拿到会
    显示未启用而不是干等。

    Args:
        messages:   多轮对话历史。
        context:    注入的 repo 全名列表。
        session_id: 会话 id。
    Yields:
        一条 error 中性事件 dict。
    """
    yield {"type": "error", "message": "对话功能未启用（memory / chat 模块未迁移）"}
