"""全局配置与运行时注入。

本模块集中放密钥、各阶段模型名和共享的 DeepSeek 客户端。`deepseek` 是全局单例
客户端；`MODELS` 按阶段配模型名，函数里不写死、随时能换；`configure` 给 webapp
在每次 run 前注入前端填的密钥和模型；`load_skill` 按名字读 skills/ 下的 skill 文件。
密钥从项目根的 .env 读，读不到也不报错，等运行时再注入。
"""

import asyncio
import contextvars
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from openai import (AsyncOpenAI, APIConnectionError, APITimeoutError,
                    InternalServerError, RateLimitError)

# 从项目根的 .env 读密钥，.env 不进 git，密钥不写死在代码里
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 密钥用 getenv 容错读，webapp 场景下 .env 可能为空、前端运行时才填，这里取不到也不能炸
GITHUB_PAT        = os.getenv("GITHUB_PAT", "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MCP_URL           = os.getenv("MCP_URL", "https://api.githubcopilot.com/mcp/")

# DeepSeek 走 OpenAI 兼容接口。AsyncOpenAI 不许传空 key，没真 key 时先垫个占位，运行时再 configure 改属性
deepseek = AsyncOpenAI(api_key=DEEPSEEK_API_KEY or "PENDING", base_url=DEEPSEEK_BASE_URL)

# ── 并发配额（想调并发就改这两个数，改完重启进程生效）──────────────
# 两级闸门，总在飞请求 = 仓库数 × 单仓 worker，被这两个数封顶：
#   API_CONCURRENCY  全局在飞 LLM 请求上限（DeepSeek pro 上限 500，这里留安全垫）
#   WORKER_PER_REPO  单个仓库辩论 worker 的并发上限
API_CONCURRENCY = 400
WORKER_PER_REPO = 12
# ───────────────────────────────────────────────────────────

# 全局并发闸，所有 LLM 请求共用。进程启动时按上面的常量建一次，运行时不热改
# （在飞请求还挂在旧闸上，热改会和它们打架，要改并发就改常量重启）
API_SEM = asyncio.Semaphore(API_CONCURRENCY)

# 每仓库的 worker 闸放进 contextvar，explore_one 入口给本仓库 set 一个，它派的 worker 自动继承。
# 走 contextvar 是为了不用把 sem 一层层穿过所有函数签名
_repo_sem_var: contextvars.ContextVar = contextvars.ContextVar("repo_sem", default=None)

# 撞限流/超时/服务端错误才退避重试，其它错误（鉴权、参数非法）直接抛，别白重试
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


def set_repo_sem() -> "asyncio.Semaphore":
    """给当前仓库建一个 worker 并发闸并塞进 contextvar，返回它。explore_one 入口调一次。"""
    sem = asyncio.Semaphore(WORKER_PER_REPO)
    _repo_sem_var.set(sem)
    return sem


async def call_deepseek(**kwargs):
    """所有 LLM 调用的统一入口：过两级并发闸 + 撞限流指数退避重试。

    先过本仓库 worker 闸（有的话）、再过全局闸，两个都拿到才真发请求；等本地工具那些时间不占闸，
    因为闸是按「一次请求」抢的、请求返回就释放。撞 429/超时/5xx 退避重试最多 3 次，退避时不占闸。

    Args:
        kwargs: 原样透传给 deepseek.chat.completions.create（model、messages、tools 等）。
    Returns:
        create 的返回对象。
    """
    repo_sem = _repo_sem_var.get()
    delay = 1.0
    for attempt in range(4):
        try:
            if repo_sem is not None:
                async with repo_sem, API_SEM:
                    return await deepseek.chat.completions.create(**kwargs)
            async with API_SEM:
                return await deepseek.chat.completions.create(**kwargs)
        except _RETRYABLE:
            if attempt == 3:
                raise
            await asyncio.sleep(delay)
            delay *= 2

# 各阶段用的模型，随时在这里调
MODELS = {
    "query_understanding":    "deepseek-v4-pro",
    "keypoint_understanding": "deepseek-v4-pro",
    "content_filter":         "deepseek-v4-pro",
}


def configure(deepseek_api_key: str = "", models: dict | None = None,
              github_pat: str = "", deepseek_base_url: str = "") -> None:
    """运行时注入前端填的密钥和各阶段模型，webapp 每次启动 run 前调一次。

    deepseek 是单例，直接改它的 api_key/base_url 属性，已经 import 过它的模块拿的是
    同一个对象，自动生效。MODELS 是共享 dict，原地改也自动生效。GITHUB_PAT 是字符串
    得重新赋值，所以用处都要走 config.GITHUB_PAT 运行时取。每个参数空着就跳过、保持原值。

    Args:
        deepseek_api_key:  DeepSeek 密钥，非空才覆盖单例的 api_key。
        models:            阶段名到模型名的 dict，某阶段填了且在 MODELS 里才覆盖。
        github_pat:        GitHub PAT，非空才覆盖全局 GITHUB_PAT。
        deepseek_base_url: DeepSeek 接口地址，非空才覆盖单例的 base_url。
    """

    global GITHUB_PAT

    # 密钥、地址非空才改，避免空字符串把已有值冲掉
    if deepseek_api_key:
        deepseek.api_key = deepseek_api_key
    if deepseek_base_url:
        deepseek.base_url = deepseek_base_url

    # 按阶段名覆盖模型，留空的阶段跳过，乱填的阶段名不在 MODELS 里也跳过
    if models:
        for stage, name in models.items():
            if name and stage in MODELS:
                MODELS[stage] = name

    if github_pat:
        GITHUB_PAT = github_pat


# skill 分散在各模块自己的 skills/ 目录里，按包定位不依赖运行时 cwd。最后那个扁平目录是
# 重构过渡期的兜底，还没挪进模块的 skill 暂放这里，全挪完就空了
_HUNTER_DIR = Path(__file__).resolve().parent
SKILL_DIRS = [
    _HUNTER_DIR / "pre_filter" / "skills",
    _HUNTER_DIR / "repo_detection" / "skills",
    _HUNTER_DIR.parent / "skills",
]


def load_skill(name: str) -> str:
    """按名字在各模块的 skills/ 目录里找 skill markdown，找到第一个就返回。

    Args:
        name: skill 文件名，不带 .md 后缀，如 "query_understanding"。
    Returns:
        该 skill 文件的全文字符串。
    """
    for d in SKILL_DIRS:
        f = d / f"{name}.md"
        if f.is_file():
            return f.read_text(encoding="utf-8")
    raise FileNotFoundError(f"找不到 skill: {name}.md")


# 分析仓库时浅克隆的落脚目录，放项目内 data/tmp 方便审查，已在 .gitignore 里排除
# import 时就建好，_clone_repo 的 mkdtemp 要往里放，哪怕还没清过也得存在
CLONE_DIR = Path(__file__).resolve().parent.parent / "data" / "tmp"
CLONE_DIR.mkdir(parents=True, exist_ok=True)


def clear_clone_dir() -> None:
    """清空 CLONE_DIR 下的所有克隆，目录本身留着。

    C 端每次开搜前、启动时、优雅关闭时各清一次；notebook 想清就手动调。克隆跑完不再自动删，
    留着给用户接着深入看，直到下一次清理。
    """
    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    for entry in CLONE_DIR.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)