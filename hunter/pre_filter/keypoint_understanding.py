"""Keypoint 理解阶段：把每条模糊的自然语言需求编译成一句可判定的标准。

本模块只有一个入口 `Keypoint_Understanding`，对每条 keypoint 各发一次独立请求、
全部并发，产出一句 standard 附在原文旁边。keypoint 原文由代码原样保留、绝不
经模型改写，模型只需要吐 standard 这一句话，不用输出 JSON，也就没有格式解析
这一步。单条调用失败时该条 standard 给空串，不拖累其他条，也不挡主流程。
"""

import asyncio

from hunter.config import call_deepseek, MODELS
from hunter.cost import track, TokenMeter


async def _compile_one(skill_md: str, keypoint: str, meter: TokenMeter) -> str:
    """对一条 keypoint 发一次请求，拿回一句重写后的 standard；调用出错就返回空串。

    Args:
        skill_md: keypoint_understanding.md 内容，作为 system prompt。
        keypoint: 用户写的这一条需求原文。
        meter:    本轮编译共用的 token 累计器。
    Returns:
        模型吐的那句 standard；调用异常时给空串。
    """
    try:
        resp = await call_deepseek(
            model=MODELS["keypoint_understanding"],
            messages=[
                {"role": "system", "content": skill_md},
                {"role": "user", "content": keypoint},
            ],
        )
        track("keypoint_understanding", resp)
        meter.add(resp)
        return resp.choices[0].message.content.strip()
    except Exception:
        return ""


async def Keypoint_Understanding(skill_md: str, keypoints: list[str],
                                 output_language: str = "简体中文") -> list[dict]:
    """把每条 keypoint 各自编译成一句 standard，keypoint 原文和 standard 一一配对返回。

    每条 keypoint 独立发一次请求、全部并发，互不干扰、也没有条数对齐的顾虑。

    Args:
        skill_md:  keypoint_understanding.md 的文件内容，作为 system prompt。
        keypoints: 用户写的需求清单，每条一句。
        output_language: 判定标准用什么语言写，回显给用户看、也拼进下游辩论提示词。
    Returns:
        每项 {"keypoint": 原文, "standard": 编译出的标准} 的列表，顺序与输入一致。
        某条编译失败时它的 standard 是空串，其余条不受影响。
    """

    # 编译是并发的一批独立调用，用一个累计器把这批调用整体算成一条链
    meter = TokenMeter()
    skill_md = skill_md.replace("{output_language}", output_language)

    standards = await asyncio.gather(
        *[_compile_one(skill_md, kp, meter) for kp in keypoints]
    )

    print(f"编译 {len(keypoints)} 条 keypoint，token 消耗 {meter.total}（命中{meter.hit_rate * 100:.0f}%）")
    return [{"keypoint": kp, "standard": std} for kp, std in zip(keypoints, standards)]
