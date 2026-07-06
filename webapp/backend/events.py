import json


def sse_event(ev: dict) -> dict:
    """把 hunter 层吐的中性事件 dict 打包成 sse_starlette 能直接 yield 的 SSE data 帧。

    hunter 的 pipeline / chat 只吐中性事件 dict（{"type": ..., ...}），不认识 SSE，转换在
    这一层做：整个 dict json.dumps 进 data 字段，前端 JSON.parse 后看 type 分发，只需一个
    解析入口。事件 type 一览：

    qu_done            {queries, keypoints}                     需求翻译完成
    keypoints_compiled {compiled}                               每条 keypoint 编译出的判定标准
    search_done        {repos, count}                           检索去重后的候选池
    repo_event         {full_name, stage, status, round, tools, tokens, hit_rate}  单仓库 Content Filter 进展，流式逐条来
                       stage 固定 content，status 为 running/judging/done/degraded/skipped
    content_log        {line}（或带 full_name）                  Content Filter 逐行 trace
    content_done       {ranked, total}                          排序后的完整结果
    cost               {table}                                  各阶段 token 与缓存汇总
    done               {}                                       整条流水线结束
    error              {message}                                出错，流提前结束
    delta              {text}                                   右栏对话的流式增量，逐 chunk 来
    """
    return {"data": json.dumps(ev, ensure_ascii=False)}
