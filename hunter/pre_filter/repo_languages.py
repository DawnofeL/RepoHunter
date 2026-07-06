"""语言字段的纠错归一与耦合补全。

用户手填的项目语言难免写错，本模块的 `normalize_languages` 把歪七扭八的输入纠回
GitHub 认的规范名，并处理 Python 和 Jupyter Notebook 必须一起搜的特殊情况。内部
`_resolve` 负责单个语言词的三级归一（别名、精确、模糊）。顶部几个常量是规范名表、
别名表和耦合表，要加语言或加耦合对改这里就行。
"""

import difflib
import warnings

# 精选常见语言的规范名，GitHub 的 language: 限定只认这些写法
CANON = [
    "Python", "Jupyter Notebook", "Go", "JavaScript", "TypeScript",
    "Rust", "C", "C++", "C#", "Java", "Kotlin", "Swift", "Ruby",
    "PHP", "Shell",
]

# 别名和常见简写映射到规范名，key 一律小写
ALIASES = {
    "py": "Python",
    "python3": "Python",
    "ipynb": "Jupyter Notebook",
    "notebook": "Jupyter Notebook",
    "jupyter": "Jupyter Notebook",
    "jupyter-notebook": "Jupyter Notebook",
    "golang": "Go",
    "js": "JavaScript",
    "node": "JavaScript",
    "nodejs": "JavaScript",
    "ts": "TypeScript",
    "cpp": "C++",
    "c++": "C++",
    "cplusplus": "C++",
    "csharp": "C#",
    "c#": "C#",
    "rs": "Rust",
}

# 天然不互斥、必须一起搜的语言成对绑定，选一个就把整组都补上
COUPLINGS = [["Python", "Jupyter Notebook"]]

# 规范名的小写查找表，精确匹配忽略大小写用
_CANON_LOWER = {name.lower(): name for name in CANON}

# 模糊匹配的候选池，规范名小写加所有别名 key
_FUZZY_KEYS = list(_CANON_LOWER) + list(ALIASES)


def _resolve(token: str) -> str | None:
    """把一个语言词归一成规范名，依次走别名、精确、模糊三级，都没中返回 None。

    Args:
        token: 用户填的单个语言词，可能带大小写或拼写错误。
    Returns:
        归一后的规范名，三级都认不出返回 None。
    """
    t = token.strip().lower()
    if not t:
        return None
    if t in ALIASES:
        return ALIASES[t]
    if t in _CANON_LOWER:
        return _CANON_LOWER[t]

    # 两字母以内的短词模糊太容易乱配（ros 会被算得很像别名 rs），只认别名和精确名
    if len(t) <= 2:
        return None
    match = difflib.get_close_matches(t, _FUZZY_KEYS, n=1, cutoff=0.8)
    if not match:
        return None

    # 模糊命中的可能是别名 key 也可能是规范名小写，分别映射回规范名
    hit = match[0]
    return ALIASES.get(hit) or _CANON_LOWER[hit]


def normalize_languages(raw: list[str] | str | None) -> list[str]:
    """把用户填的语言列表纠错归一成规范名，再补上耦合语言、去重保序。

    每个字段填一个语言，手输难免写错，所以逐个走别名、精确、模糊三级纠回。
    Python 和 Jupyter Notebook 天然不互斥，选一个就把另一个一起搜。

    Args:
        raw: 用户填的语言，最多几个字段收成一个列表，也容忍单字符串或 None。
    Returns:
        规范名列表，空输入给空列表。
    """

    # 容忍单字符串和 None，统一成列表
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]

    result = []
    for token in raw:

        # 空字段跳过；认不出的原样保留并警告；归一成功但改了写法的也警告一声让用户知道
        if not token or not token.strip():
            continue
        canon = _resolve(token)
        if canon is None:
            warnings.warn(f"未能识别语言 {token!r}，原样使用")
            result.append(token.strip())
            continue
        if canon != token.strip():
            warnings.warn(f"语言 {token!r} 归一成 {canon}")
        result.append(canon)

    # 命中某个耦合组就把整组并进来，选 Python 补 Jupyter Notebook，反之亦然
    for group in COUPLINGS:
        if any(lang in group for lang in result):
            result.extend(group)

    # 去重保序，耦合可能带进重复，按首次出现的顺序留一份
    seen = set()
    out = []
    for lang in result:
        if lang not in seen:
            seen.add(lang)
            out.append(lang)
    return out