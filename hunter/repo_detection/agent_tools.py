"""Content Filter用的四个只读工具及其底层。

对外是四个工具，都对克隆到本地的仓库目录操作：`list_tree` 列单层目录、`read_file`
读文件带行号和已读缓存、`grep_code` 用 ripgrep 做正则搜索、`glob_files` 按文件名
pattern 跨树找文件。`TOOL_SCHEMAS` 是发给模型的工具定义常量，
`make_dispatch` 把四个工具绑定到克隆目录、返回引擎按名字调用的调度表。
预抓资料（README、两层目录树、meta）另在 prefetch 模块，走 GitHub API、克隆之前跑。
"""

import asyncio
import os


# 每次最多读多少行，不传 limit 时用这个默认值，超大文件可以分段读
MAX_LINES = 2000

# 工具返回给模型的内容最多多少字，超出就截断并提示，防止单次工具返回把整个上下文塞满
MAX_OUTPUT_CHARS = 40000

# 列目录时最多显示多少个条目，超出的截断并标注总数
MAX_TREE_ENTRIES = 300

# grep 搜索最多返回多少行命中，够定位用就行
MAX_GREP_HITS = 20

# glob 找文件最多返回多少个路径
MAX_GLOB_HITS = 100

# 一段被清理归档后，最多允许模型重读几次。到上限再读也挡，防清理和重读来回拉锯的死循环
MAX_REREAD = 2


def _safe_join(root: str, rel: str) -> str | None:
    """
    把模型给的相对路径拼到克隆根目录下，挡住用 ../ 逃出仓库的路径。

    Args:
        root: 克隆根目录的绝对路径。
        rel:  模型给的仓库内相对路径，可能为空。
    Returns:
        拼好且仍在 root 内的绝对路径；越界返回 None。
    """
    full = os.path.normpath(os.path.join(root, rel or ""))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


def _fmt_line(item: dict, indent: int = 0) -> str:
    """
    格式化一条目录条目，dir 名后加斜杠、file 带字节大小，indent 控制缩进层级。"""
    pad = "    " * indent
    width = max(28 - len(pad), 1)
    if item.get("type") == "dir":
        return f"{pad}{item['name'] + '/':<{width}}[dir]"
    return f"{pad}{item['name']:<{width}}[file, {item.get('size', 0)}B]"


async def list_tree(root: str, path: str = "") -> str:
    """
    列克隆目录里某一层的条目，带类型和文件大小，跳过 .git。

    Args:
        root: 克隆根目录绝对路径。
        path: 目录相对路径，空字符串列根目录。
    Returns:
        每行一条条目，超 MAX_TREE_ENTRIES 截断附总数；越界或非目录返回提示语。
    """
    full = _safe_join(root, path)
    if full is None:
        return "路径越界，只能在仓库内查看。"
    if not os.path.isdir(full):
        return f"{path or '(根目录)'} 不是可列的目录，可能不存在或指向文件，文件请用 read_file 读。"

    # 收集本层条目，目录排前、同类按名字排，.git 不给模型看
    items = []
    for e in os.scandir(full):
        if e.name == ".git":
            continue
        is_dir = e.is_dir()
        items.append({"name": e.name, "type": "dir" if is_dir else "file",
                      "size": 0 if is_dir else e.stat().st_size})
    items.sort(key=lambda it: (it["type"] != "dir", it["name"]))

    lines = [_fmt_line(it) for it in items[:MAX_TREE_ENTRIES]]
    text = "\n".join(lines)
    if len(items) > MAX_TREE_ENTRIES:
        text += f"\n...（共 {len(items)} 条，只列前 {MAX_TREE_ENTRIES} 条）"
    return text or "（空目录）"


async def read_file(root: str, read_cache: dict, path: str,
                    offset: int = 0, limit: int = MAX_LINES) -> str:
    """
    读克隆目录里一个文件，cat -n 风格带行号，支持 offset/limit 分段和带状态的已读去重。

    每段在 read_cache 里记一条状态：live（正文还在上下文里）或 archived（被滚动清理归档、
    正文换成占位符了），归档的还记重读了几次。三种情况：没读过正常读、记 live；读过且 live
    再读挡回「已读过」，防空转白烧钱；读过但 archived 放行重读（内容不在上下文里、重读有意义），
    但到 MAX_REREAD 上限再读也挡，防清理和重读来回拉锯的死循环。

    Args:
        root:       克隆根目录绝对路径。
        read_cache: 本仓库已读缓存，键是 (path, offset, limit)，值是 {"state", "rereads"}。
        path:       文件相对路径。
        offset:     起始行（从 0 起）。
        limit:      读多少行。
    Returns:
        带行号的内容，行号从 offset+1 起；越界、不存在、读失败、已读过、已达重读上限各返回对应提示。
    """
    # 同一段读过：live 的挡回不再读；archived 的放行重读，但到上限也挡
    key = (path, offset, limit)
    rec = read_cache.get(key)
    if rec is not None:
        if rec["state"] == "live":
            return f"该文件该段（{path} 行 {offset}-{offset + limit}）已读过，内容未变，略。"
        # archived：内容已被清理出上下文，允许重读回来，但最多 MAX_REREAD 次
        if rec["rereads"] >= MAX_REREAD:
            return (f"该文件该段（{path} 行 {offset}-{offset + limit}）已归档且重读达上限，"
                    "不再重复读取，请用现有信息收尾。")

    full = _safe_join(root, path)
    if full is None:
        return "路径越界，只能在仓库内读取。"
    if not os.path.isfile(full):
        return f"文件不存在: {path}"

    # 二进制或非 utf-8 字符用替换符兜底，不让一个文件把工具搞崩
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"读取失败: {e}"

    # 头回读记成 live；重读归档段则回到 live 并把重读计数加一
    if rec is None:
        read_cache[key] = {"state": "live", "rereads": 0}
    else:
        rec["state"] = "live"
        rec["rereads"] += 1
    lines = content.split("\n")
    chunk = lines[offset:offset + limit]

    # 行号从 offset+1 起算，和编辑器一致，方便模型在证据里引用具体行
    numbered = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(chunk))
    if len(numbered) > MAX_OUTPUT_CHARS:
        numbered = numbered[:MAX_OUTPUT_CHARS] + "\n...（内容过长已截断，用 offset/limit 读后续）"
    return numbered


async def grep_code(root: str, pattern: str, path: str = "", glob: str = "",
                    output_mode: str = "files_with_matches", ignore_case: bool = False,
                    context: int = 0, head_limit: int = MAX_GREP_HITS) -> str:
    """在克隆目录里跑 ripgrep 做正则搜索，把参数拼成 rg 命令行子进程执行。

    Args:
        root:        克隆根目录绝对路径。
        pattern:     正则表达式，rg 语法。
        path:        限定搜索的子目录，空则搜整个仓库。
        glob:        按文件名过滤，如 *.py。
        output_mode: files_with_matches 只回文件名 / content 回带行号匹配行 / count 回计数。
        ignore_case: 大小写不敏感。
        context:     content 模式下匹配行前后各显示几行。
        head_limit:  最多返回多少行。
    Returns:
        rg 的命中结果，超 head_limit 截断附总数；无命中、越界、出错各返回对应提示。
    """
    cmd = ["rg", "--color=never"]

    # 三种输出模式分别对应 rg 的 -c / -n / -l，content 模式才拼上下文
    if output_mode == "count":
        cmd.append("-c")
    elif output_mode == "content":
        cmd.append("-n")
        if context:
            cmd += ["-C", str(context)]
    else:
        cmd.append("-l")

    if ignore_case:
        cmd.append("-i")
    if glob:
        cmd += ["--glob", glob]

    # 排除 .git 放在用户 glob 之后，rg 后匹配优先，挡住 .git 内部被显式 glob 带进来
    cmd += ["-g", "!.git"]

    # -- 标记选项结束，后面是正则本体和可选的限定目录，避免 pattern 以 - 开头被当成旗标
    cmd.append("--")
    cmd.append(pattern)
    if path:
        if _safe_join(root, path) is None:
            return "路径越界，只能在仓库内搜索。"
        cmd.append(path)

    # stdin 必须显式断开：rg 没给搜索路径时，见 stdin 是可读管道就改读 stdin 而不搜目录，
    # Jupyter 内核的 stdin 正是一根不关的管道，不断开会永久挂死
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=root, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()

    # rg 退出码 1 表示没匹配，属正常不是报错；2 以上才是真出错
    if proc.returncode == 1:
        return "无命中。"
    if proc.returncode not in (0, 1):
        return f"搜索出错：{err.decode('utf-8', 'replace').strip()}"

    lines = out.decode("utf-8", "replace").splitlines()
    capped = lines[:head_limit]
    text = "\n".join(capped)
    if len(lines) > head_limit:
        text += f"\n...（共 {len(lines)} 行命中，只显示前 {head_limit} 行）"
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n...（输出过长已截断）"
    return text or "无命中。"


async def glob_files(root: str, pattern: str, path: str = "") -> str:
    """
    在克隆目录里按文件名 pattern 找文件，用 rg --files 配 --glob 列出匹配路径。

    Args:
        root:    克隆根目录绝对路径。
        pattern: 文件名匹配的 glob，如 **/*.py。
        path:    限定搜索的子目录，空则搜整个仓库。
    Returns:
        每行一个匹配文件路径，超 MAX_GLOB_HITS 截断附总数；无匹配、越界、出错各返回提示。
    """
    # 用户 glob 在前、排除 .git 在后，rg 后匹配优先，保证 .git 内部不被显式 glob 重新带进来
    cmd = ["rg", "--files", "--color=never", "-g", pattern, "-g", "!.git"]
    if path:
        if _safe_join(root, path) is None:
            return "路径越界，只能在仓库内查找。"
        cmd.append(path)

    # stdin 显式断开，防 rg 在管道 stdin 下改读 stdin，同 grep_code
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=root, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()

    # rg --files 没匹配时退出码可能是 1，按无结果处理，2 以上才是出错
    if proc.returncode not in (0, 1):
        return f"查找出错：{err.decode('utf-8', 'replace').strip()}"

    files = out.decode("utf-8", "replace").splitlines()
    if not files:
        return "没有匹配的文件。"
    capped = files[:MAX_GLOB_HITS]
    text = "\n".join(capped)
    if len(files) > MAX_GLOB_HITS:
        text += f"\n...（共 {len(files)} 个，只显示前 {MAX_GLOB_HITS} 个）"
    return text


# 工具 schema 固定顺序写死成常量，每次调用原样发出，保证每次发出的内容字节一致才能命中缓存
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_tree",
            "description": "列出仓库某个目录下的直接子项，给出名字、是文件还是目录、文件大小（字节）。path 不填或填空字符串列根目录。它只列一层，要看更深的目录就对子目录路径再调一次。文件大小是给你参考的，大文件读之前先掂量值不值得花一次调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录相对路径，不填或空字符串列根目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读仓库里一个文件的内容，带行号返回，行号方便你在证据里引用具体某一行。大文件会自动截断，可用 offset 和 limit 读指定行段；大文件先用小 limit 读头部看清结构，再按需读具体行段，不要整吞。同一文件同一段读过一次就不用再读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件相对路径"},
                    "offset": {"type": "integer", "description": "起始行，从 0 起，默认 0"},
                    "limit": {"type": "integer", "description": "读多少行，默认 2000"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_code",
            "description": "在当前仓库内用 ripgrep 做正则搜索，定位某功能、类、函数出现在哪几个文件，不用全读。默认只回文件名，最省 token。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "要搜的正则表达式，ripgrep 语法，字面花括号等元字符需转义"},
                    "path": {"type": "string", "description": "限定搜索的子目录相对路径，不填搜整个仓库"},
                    "glob": {"type": "string", "description": "按文件名过滤，如 *.py、**/*.ts，只在匹配的文件里搜"},
                    "output_mode": {"type": "string", "description": "files_with_matches 只回文件名（默认，最省 token），content 回带行号的匹配行，count 回每文件命中数"},
                    "ignore_case": {"type": "boolean", "description": "大小写不敏感，默认 false"},
                    "context": {"type": "integer", "description": "匹配行前后各显示几行，仅 output_mode 为 content 时生效"},
                    "head_limit": {"type": "integer", "description": "最多返回多少行命中，默认 20"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "按文件名 pattern 跨整个仓库找文件，返回匹配的文件路径。用来快速定位 manifest、入口、配置等文件，比 list_tree 一层层翻快。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "文件名匹配的 glob，如 **/*.py、src/**/*.ts"},
                    "path": {"type": "string", "description": "限定搜索的子目录，不填搜整个仓库"},
                },
                "required": ["pattern"],
            },
        },
    },
]


def archive_segment(read_cache: dict, key: tuple) -> None:
    """把一段读文件记录标成归档：正文已被滚动清理出上下文，之后允许模型重读回来（限次）。

    滚动清理每清掉一段读文件返回就调一次。归档只改状态、不动重读计数，让这段从「读过就挡」
    变成「可重读（到上限为止）」。key 是 (path, offset, limit)，缓存里没有就不管（没读过的段不会被清）。

    Args:
        read_cache: 本仓库已读缓存。
        key:        要归档的那段的键 (path, offset, limit)。
    """
    rec = read_cache.get(key)
    if rec is not None:
        rec["state"] = "archived"


def is_archived_read(read_cache: dict, args: dict) -> bool:
    """按一次 read_file 调用的参数，查这段是不是归档状态且还没读到重读上限。

    给 guard 和死循环熔断用：被系统清理归档、模型又来重读的段，是合理重读、不该被挡也不该
    算死循环。args 是模型填的读文件参数，缺省对齐 read_file 的默认值拼出那段的键。

    Args:
        read_cache: 本仓库已读缓存。
        args:       一次 read_file 调用的参数 dict。
    Returns:
        这段已归档且重读没到上限返回 True，否则 False。
    """
    key = (args.get("path", ""), args.get("offset") or 0, args.get("limit") or MAX_LINES)
    rec = read_cache.get(key)
    return rec is not None and rec["state"] == "archived" and rec["rereads"] < MAX_REREAD


def make_dispatch(local_root: str, read_cache: dict) -> dict:
    """把四个工具绑定到克隆目录，返回「工具名 → async 闭包」的调度表。

    四个闭包把 local_root 和 read_cache 固定进去，对外只收一个 args dict（LLM 填的
    参数），引擎按工具名查表调用时不需要知道当前是哪个仓库、哪个目录。

    Args:
        local_root: 克隆根目录绝对路径。
        read_cache: 这个仓库私有的已读缓存 dict，传给 read_file 去重和记状态用。
    Returns:
        dict，键是工具名（list_tree / read_file / grep_code / glob_files），值是 async 闭包。
    """
    async def _list_tree(args: dict) -> str:
        return await list_tree(local_root, args.get("path", "") or "")

    async def _read_file(args: dict) -> str:
        offset = args.get("offset") or 0
        limit = args.get("limit") or MAX_LINES
        return await read_file(local_root, read_cache, args["path"], offset, limit)

    async def _grep_code(args: dict) -> str:
        return await grep_code(
            local_root, args["pattern"],
            args.get("path", "") or "", args.get("glob", "") or "",
            args.get("output_mode", "files_with_matches") or "files_with_matches",
            bool(args.get("ignore_case")), args.get("context") or 0,
            args.get("head_limit") or MAX_GREP_HITS)

    async def _glob_files(args: dict) -> str:
        return await glob_files(local_root, args["pattern"], args.get("path", "") or "")

    return {"list_tree": _list_tree, "read_file": _read_file,
            "grep_code": _grep_code, "glob_files": _glob_files}
