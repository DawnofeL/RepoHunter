"""memory 占位模块。

memory 不在本次迁移范围（repo detection + pre filter 的网页端）。但 repo_detection 的编排器
import 了 load_memories、pipeline import 了 save_memories、webapp 的 server 启动时还会调
init_* 建表、各路由会调 list/get/save/delete 一堆函数。为了让这些 import 和调用都不炸，这里把
真实 memory 模块的对外函数全部放成空壳：读一律返回空、写一律不做。搜索路径不依赖记忆，所以
休眠不影响主流程；/memory 和 /chat 那些路由返回空、不 500。将来真迁 memory，用完整模块替换
本文件即可，server.py 一个字不用改。

—— 仓库记忆（S2 复用拆解，搜索时用）——
load_memories / save_memories：编排器和 pipeline 用，读空写空
list_memories / get_memory / delete_memory / clear_all：/memory 路由用

—— 对话会话与提取记忆（chat 用，本次一并休眠）——
save_session / rename_session / set_pinned / list_sessions / get_session / delete_session
get_notes / list_by_session / list_general

—— 建表（server 启动时调，空操作即可）——
init_repo_memory / init_chat_sessions / init_chat_memories / init_notes
"""


# —— 仓库记忆 ——

def load_memories(full_names: list[str]) -> dict:
    """占位：真实版按仓库全名查记忆库，这里一律返回空 dict，等于没有任何记忆命中。"""
    return {}


def save_memories(ranked: list[dict], keypoints: list[str]) -> None:
    """占位：真实版把这批仓库拆解写回记忆库，这里空操作。"""
    return None


def list_memories() -> list:
    """占位：真实版列所有仓库记忆摘要，这里返回空列表，/memory 列表页显示为空。"""
    return []


def get_memory(full_name: str) -> dict | None:
    """占位：真实版取一条完整仓库记忆，这里一律返回 None（视为不存在）。"""
    return None


def delete_memory(full_name: str) -> None:
    """占位：真实版删一条仓库记忆，这里空操作。"""
    return None


def clear_all() -> None:
    """占位：真实版清空所有仓库记忆，这里空操作。"""
    return None


# —— 对话会话与提取记忆 ——

def save_session(session_id: str, context: list, messages: list) -> None:
    """占位：真实版存一次对话，这里空操作。"""
    return None


def rename_session(sid: str, title: str) -> None:
    """占位：真实版给会话改名，这里空操作。"""
    return None


def set_pinned(sid: str, pinned: bool) -> None:
    """占位：真实版置顶/取消置顶会话，这里空操作。"""
    return None


def list_sessions() -> list:
    """占位：真实版列所有会话摘要，这里返回空列表。"""
    return []


def get_session(sid: str) -> dict | None:
    """占位：真实版取一次完整对话，这里一律返回 None。"""
    return None


def delete_session(sid: str) -> None:
    """占位：真实版删一次会话，这里空操作。"""
    return None


def get_notes(sid: str) -> str:
    """占位：真实版取会话笔记，这里返回空串。"""
    return ""


def list_by_session(sid: str) -> list:
    """占位：真实版列本会话提取出的记忆，这里返回空列表。"""
    return []


def list_general() -> list:
    """占位：真实版列全局通用记忆，这里返回空列表。"""
    return []


# —— 建表（server 启动时调）——

def init_repo_memory() -> None:
    """占位：真实版建仓库记忆表，这里空操作。"""
    return None


def init_chat_sessions() -> None:
    """占位：真实版建对话历史表，这里空操作。"""
    return None


def init_chat_memories() -> None:
    """占位：真实版建提取记忆表，这里空操作。"""
    return None


def init_notes() -> None:
    """占位：真实版给对话表补笔记列，这里空操作。"""
    return None
