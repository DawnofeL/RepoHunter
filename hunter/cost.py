"""token 用量与缓存命中的计量工具。

本模块提供两套互不干扰的计量。`track` 把每次模型调用的用量按阶段累加进全局字典
`COST`，跑完后能看每个阶段总共花了多少、命中率多高。`TokenMeter` 是个累计器类，
盯单独一条调用链（一次 QU、一个仓库的 Content Filter），主调用和重试都算进去，供进度表
实时显示这条链的消耗。两者在每次模型调用后各自累加，各管各的。
"""

# 全局用量按阶段分组累加，每次模型调用后调 track 往里记一笔
COST = {}


def track(stage: str, resp) -> None:
    """把一次模型调用的用量累加进全局 COST，按阶段分组。

    Args:
        stage: 阶段名，如 "query_understanding"、"content_filter"。
        resp:  deepseek.chat.completions.create 的返回，从它的 usage 取各项 token。
    """

    # usage 是 OpenAI SDK 返回的用量对象，带 prompt/completion 和缓存命中字段
    u = resp.usage

    # 这个阶段头一次出现就先建一个全零的计数结构，之后同阶段的调用往里累加
    c = COST.setdefault(stage, {"calls": 0, "prompt": 0, "hit": 0, "miss": 0, "completion": 0})

    c["calls"]      += 1
    c["prompt"]     += u.prompt_tokens

    # 缓存命中、未命中字段不是每个模型都返回，取不到就按 0 算，避免 AttributeError
    c["hit"]        += getattr(u, "prompt_cache_hit_tokens", 0)
    c["miss"]       += getattr(u, "prompt_cache_miss_tokens", 0)
    c["completion"] += u.completion_tokens


class TokenMeter:
    """单条调用链自己的 token 累计器，每次模型调用后调 add 累加一笔。

    跟全局 COST 各管各的：COST 汇总整个阶段，这个只盯当前这一条调用链，
    用来在进度表和Content Filter日志里实时显示这条链至今花了多少 token。
    """

    def __init__(self) -> None:
        """各项计数清零，新建一个空的累计器。"""
        self.calls = 0
        self.prompt = 0
        self.hit = 0
        self.miss = 0
        self.completion = 0

    def add(self, resp) -> None:
        """把一次模型调用的用量累加进本累计器。

        Args:
            resp: deepseek.chat.completions.create 的返回，从它的 usage 取 token。
        """

        # 用量对象同 track，缓存命中字段有的模型没有，取不到当 0
        u = resp.usage
        self.calls += 1
        self.prompt += u.prompt_tokens
        self.hit += getattr(u, "prompt_cache_hit_tokens", 0)
        self.miss += getattr(u, "prompt_cache_miss_tokens", 0)
        self.completion += u.completion_tokens

    @property
    def total(self) -> int:
        """输入加输出的总 token 数。"""
        return self.prompt + self.completion

    @property
    def hit_rate(self) -> float:
        """缓存命中率，命中 token 占全部输入 token 的比例，没有输入时给 0。"""
        return self.hit / self.prompt if self.prompt else 0.0