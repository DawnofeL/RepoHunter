"""
对话侧工具后端：复用搜索侧四个只读工具，外面包一层多仓库路由。

搜索侧的工具一次绑死一个克隆目录，对话可能同时注入几个仓库，所以给每个工具的参数表
加一个 repo（仓库全名），按它挑克隆目录；只有一个仓库时可以不填，默认就它。克隆懒加载：
第一次用到某仓库才走 ensure_clone，本地有就秒回，没有才现场浅克隆。ChatToolbox 一轮
对话建一个，读文件缓存按仓库分开存，同一轮里重复读同一段会被挡回。
"""

import copy
import json

from hunter.clone import ensure_clone, clone_path
from hunter.repo_detection.agent_tools import tool_schemas, make_dispatch, archive_segment, MAX_LINES
from hunter.repo_detection.prompt_text import texts as _ptexts
from hunter.memory.compact.tokens import estimate_tokens

# 每轮对话最多的工具调用次数，交互场景等不起搜索侧深挖那种二十次
MAX_TOOL_CALLS = 6

# 「已查过的代码」块注入 system 的 token 预算，超了就把较早的结果降级成一行指针
TOOL_HISTORY_BUDGET = 8000

# 会话级工具箱注册表：同一会话跨轮共用一个工具箱（已读缓存和工具日志都在里面），
# 超过上限踢掉最老的会话防内存无限涨
_SESSION_BOXES: dict = {}
_MAX_SESSIONS = 32


def get_toolbox(session_id: str, repos: list[str], output_language: str = "简体中文") -> "ChatToolbox":
    """按会话取工具箱：同一会话复用同一个（跨轮保住已读缓存和工具日志），没会话 id 就给一次性的。

    复用时仓库清单取并集（新注入的追加进来），语言跟着这轮界面语言走。
    """
    if not session_id:
        return ChatToolbox(repos, output_language)
    box = _SESSION_BOXES.get(session_id)
    if box is None:
        box = ChatToolbox(list(repos), output_language)
        _SESSION_BOXES[session_id] = box
        while len(_SESSION_BOXES) > _MAX_SESSIONS:
            _SESSION_BOXES.pop(next(iter(_SESSION_BOXES)))
    else:
        box.lang = output_language
        for r in repos:
            if r not in box.repos:
                box.repos.append(r)
    return box

# 工具活动的展示文案，推给前端让用户看到助手正在干什么，按界面语言取
_ACT_ZH = {
    "clone": "克隆 {repo}", "read": "读 {path}", "grep": "正则搜 {pattern}",
    "tree": "列目录 {path}", "tree_root": "列根目录", "glob": "找文件 {pattern}",
    "call": "调用 {name}", "repo_prefix": "{repo}：",
    "bad_repo": "参数 repo 必须是这些仓库之一：{repos}",
    "clone_fail": "仓库 {repo} 克隆失败，本地读不到它的源码，基于拆解回答并说明这一点。",
    "hist_header": "# 本会话已查过的代码（结果还在的直接引用别重查；标了已清理的需要就重新调用工具取回）",
    "hist_label": "已查过的代码",
    "hist_gone": "（结果已清理，需要就重新调用）",
}
_ACT_EN = {
    "clone": "cloning {repo}", "read": "reading {path}", "grep": "regex search {pattern}",
    "tree": "listing {path}", "tree_root": "listing root", "glob": "finding {pattern}",
    "call": "calling {name}", "repo_prefix": "{repo}: ",
    "bad_repo": "The repo argument must be one of: {repos}",
    "clone_fail": "Failed to clone {repo}; its source is unavailable locally. Answer from the dissection and say so.",
    "hist_header": "# Code already looked up in this session (quote results that are still here instead of re-querying; entries marked cleaned can be fetched again with a tool call)",
    "hist_label": "Code looked up",
    "hist_gone": "(result cleaned; call the tool again if needed)",
}


def _acts(lang: str) -> dict:
    """按界面语言取活动文案包，English 用英文，其余中文。"""
    return _ACT_EN if lang == "English" else _ACT_ZH


class ChatToolbox:
    """一轮对话的工具箱：管工具定义、多仓库路由、懒克隆和按仓库的已读缓存。"""

    def __init__(self, repos: list[str], output_language: str = "简体中文") -> None:
        self.repos = repos
        self.lang = output_language
        # 每个仓库各一份调度表和已读缓存，第一次用到才建（要先克隆）
        self._dispatch: dict = {}
        self._read_cache: dict = {}
        # 工具日志：这个会话历次调用的 {desc, name, repo, args, result, state}，按时间序。
        # state 是 live（结果还注入在 system 里）或 archived（超预算被降级成一行指针）
        self.log: list = []
        # 最近一次拼「已查过的代码」块时降级了几条，给 dev 监控看
        self.last_archived = 0

    def schemas(self) -> list:
        """搜索侧的四个工具定义加上 repo 参数后返回，参数说明里列出可选的仓库全名。"""
        schemas = copy.deepcopy(tool_schemas(self.lang))
        names = "、".join(self.repos) if self.lang != "English" else ", ".join(self.repos)
        desc = (f"Repo full name, one of: {names}. Optional when only one repo is injected."
                if self.lang == "English"
                else f"仓库全名，可选值：{names}。只注入了一个仓库时可不填。")
        for s in schemas:
            s["function"]["parameters"]["properties"]["repo"] = {"type": "string", "description": desc}
        return schemas

    def _pick_repo(self, args: dict) -> str | None:
        """从参数里取出目标仓库：填了就校验，没填且只有一个仓库就默认它，定不了返回 None。"""
        repo = (args.pop("repo", "") or "").strip()
        if repo in self.repos:
            return repo
        if not repo and len(self.repos) == 1:
            return self.repos[0]
        return None

    def needs_clone(self, raw_args: str) -> str | None:
        """看这次调用指向的仓库本地有没有克隆，没有就返回仓库名（好让上层先吐克隆提示），有返回 None。"""
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return None
        repo = self._pick_repo(dict(args))
        if repo and repo not in self._dispatch and not (clone_path(repo) / ".git").is_dir():
            return repo
        return None

    def describe_clone(self, repo: str) -> str:
        """拼「正在克隆某仓库」的活动文案，克隆要几秒，先推给前端别让用户干等。"""
        return _acts(self.lang)["clone"].format(repo=repo)

    def describe(self, name: str, raw_args: str) -> str:
        """把一次工具调用拼成一句给用户看的活动文案，如「读 xxx.py」「正则搜 pattern」。"""
        a = _acts(self.lang)
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            args = {}
        repo = args.get("repo", "")
        if name == "read_file":
            text = a["read"].format(path=args.get("path", ""))
        elif name == "grep_code":
            text = a["grep"].format(pattern=args.get("pattern", ""))
        elif name == "list_tree":
            path = args.get("path", "")
            text = a["tree"].format(path=path) if path else a["tree_root"]
        elif name == "glob_files":
            text = a["glob"].format(pattern=args.get("pattern", ""))
        else:
            text = a["call"].format(name=name)
        # 多仓库时前面带上是哪个仓库的操作，单仓库不用啰嗦
        if len(self.repos) > 1 and repo:
            text = a["repo_prefix"].format(repo=repo) + text
        return text

    async def exec(self, name: str, raw_args: str) -> str:
        """执行一次工具调用，返回给模型看的结果文本，一切异常都兜成文字说明不往上抛。"""
        t = _ptexts(self.lang)
        a = _acts(self.lang)
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return t["tool_bad_json"].format(args=raw_args)

        repo = self._pick_repo(args)
        if repo is None:
            return a["bad_repo"].format(repos=" / ".join(self.repos))

        # 这个仓库第一次被用到：确保本地有克隆，然后把四个工具绑到它的目录上
        if repo not in self._dispatch:
            root = await ensure_clone(repo)
            if root is None:
                return a["clone_fail"].format(repo=repo)
            self._read_cache[repo] = {}
            self._dispatch[repo] = make_dispatch(root, self._read_cache[repo], self.lang)

        fn = self._dispatch[repo].get(name)
        if fn is None:
            return t["tool_unknown"].format(name=name)

        # 读文件前先看这段是不是 live 状态（正文还注入在上下文里），是的话工具会挡回「已读过」，
        # 这种挡回不记日志，免得往「已查过的代码」块里塞无意义的条目
        blocked = False
        if name == "read_file":
            key = (args.get("path", ""), args.get("offset") or 0, args.get("limit") or MAX_LINES)
            rec = self._read_cache[repo].get(key)
            blocked = rec is not None and rec["state"] == "live"

        try:
            result = str(await fn(args))
        except Exception as e:
            result = t["tool_run_err"].format(err=e)

        # 记进工具日志：结果先按 live 注入下轮 system，超预算时 context_block 再降级
        if not blocked:
            self.log.append({"desc": self.describe(name, raw_args), "name": name, "repo": repo,
                             "args": args, "result": result, "state": "live"})
        return result

    def context_block(self, budget: int = TOOL_HISTORY_BUDGET) -> str:
        """把工具日志拼成「本会话已查过的代码」块注入 system，超预算的较早结果降级成一行指针。

        从最新往回留结果原文，token 累计到预算为止；更早的把状态改成 archived、只留一行
        「查过什么（结果已清理）」。read_file 的段降级时同步把已读缓存标归档，模型才被放行
        限次重读（跟搜索侧滚动清理同一套规矩）。没有日志返回空串。
        """
        if not self.log:
            return ""
        a = _acts(self.lang)

        # 从最新往回累计 token，超预算的降级；已归档的不回头
        kept = 0
        archived_now = 0
        for entry in reversed(self.log):
            if entry["state"] != "live":
                continue
            kept += estimate_tokens([{"content": entry["result"]}])
            if kept <= budget:
                continue
            entry["state"] = "archived"
            archived_now += 1
            if entry["name"] == "read_file":
                key = (entry["args"].get("path", ""), entry["args"].get("offset") or 0,
                       entry["args"].get("limit") or MAX_LINES)
                archive_segment(self._read_cache.get(entry["repo"], {}), key)
        self.last_archived = archived_now

        lines = [a["hist_header"], ""]
        for entry in self.log:
            if entry["state"] == "live":
                lines.append(f"## {entry['desc']}\n{entry['result']}")
            else:
                lines.append(f"## {entry['desc']}{a['hist_gone']}")
        return "\n\n".join(lines)

    def history_label(self) -> str:
        """「已查过的代码」段在提示词监控里的显示名，按界面语言取。"""
        return _acts(self.lang)["hist_label"]