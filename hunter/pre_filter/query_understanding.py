"""意图理解阶段：从用户写的需求清单里抽出 GitHub 搜索查询。

本模块只有一个入口 `Query_Understanding`，它调 DeepSeek 从用户一条条写的需求里抽出
项目身份概念、翻成 GitHub 能搜的 queries。keypoints 由用户直接写、不经 QU，这里原样
带回。内部用闭包 `call` 统一发请求并计量，`try_repair` 在 JSON 坏掉时正则兜底。
"""

import json
import re
import warnings

from hunter.config import call_deepseek, MODELS
from hunter.cost import track, TokenMeter


async def Query_Understanding(skill_md: str, keypoints: list[str]) -> dict:
    """用 DeepSeek 从用户的需求清单抽出搜索查询，keypoints 原样带回。

    keypoints 是用户在前端一条条写的需求，本身就是后面逐条核对的清单，这里不改它、
    只读它来抽 queries。输出 JSON 坏掉时走三级兜底，尽量不让整条流水线崩。

    Args:
        skill_md:  query_understanding.md 的文件内容，作为 system prompt。
        keypoints: 用户写的需求清单，每条一句。
    Returns:
        含 queries、keypoints 两个键的 dict。queries 是抽出的搜索查询，keypoints 是
        原样带回的用户清单；queries 彻底解析不出时给空列表。
    """

    # QU 是单线程，用一个累计器把主调用和可能的重试都算上，末尾打印本阶段 token
    meter = TokenMeter()

    async def call(msgs: list[dict]) -> str:
        """发一次 DeepSeek 请求并返回回复文本，顺便把用量记进全局和本累计器。"""

        # response_format 强制 DeepSeek 输出合法 JSON，减少格式出错概率，模型从 MODELS 配置取
        resp = await call_deepseek(
            model=MODELS["query_understanding"],
            messages=msgs,
            response_format={"type": "json_object"},
        )
        track("query_understanding", resp)
        meter.add(resp)
        return resp.choices[0].message.content

    def try_repair(raw: str) -> dict | None:
        """从破损字符串里正则抠出最外层 JSON 对象做兜底修复，抠不出返回 None。"""

        # 贪婪匹配第一个 { 到最后一个 }，把模型在 JSON 前后多说的话剔掉
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None

    # 用户的需求清单拼成 user，每条一行，skill md 进 system 保缓存命中
    bullets = "\n".join(f"- {k}" for k in keypoints)
    user_content = f"用户的需求清单：\n{bullets}"
    messages = [
        {"role": "system", "content": skill_md},
        {"role": "user", "content": user_content},
    ]

    raw = await call(messages)

    try:
        data = json.loads(raw)

    except json.JSONDecodeError as e:

        # 把破损输出塞回 assistant，再追一条修格式指令，前缀不变所以能命中缓存
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"输出的 JSON 格式不合法，错误：{e}。内容不变，只修复格式，重新输出合法 JSON。"},
        ]
        raw = await call(retry_messages)

        try:
            data = json.loads(raw)

        except json.JSONDecodeError:

            # 重试仍失败，代码兜底抠 JSON，实在抠不出就警告并返回空壳
            data = try_repair(raw)
            if data is None:
                warnings.warn("JSON 修复失败，输出可能不完整")
                data = {"queries": []}

    # keypoints 用用户原样写的那份，不取模型可能顺手吐的
    data["keypoints"] = keypoints
    print(f"生成 {len(data.get('queries', []))} 路查询，{len(keypoints)} 条 keypoint")
    print(f"token 消耗 {meter.total}（命中{meter.hit_rate * 100:.0f}%）")
    return data