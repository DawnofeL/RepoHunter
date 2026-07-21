"""
克隆生命周期：按仓库名固定命名、跨搜索保留、按最后使用时间过期清理。

克隆落在 data/tmp 下，目录名由仓库全名转换而来（斜杠换成双下划线）。搜索侧和对话侧都
从 ensure_clone 拿本地代码：本地有就刷新时间戳直接复用，没有才现场浅克隆。每个克隆目录
里放一个 .last_used 标记文件记最后使用时间，程序启动时 sweep_clones 拿它跟当前时间比对，
超过一天没用过的整个删掉。日期比对纯代码完成，不经模型。
"""

import asyncio
import shutil
from datetime import datetime
from pathlib import Path

from hunter import config
from hunter.config import CLONE_DIR

# 标记文件名与时间格式，可读格式方便直接翻看，跟 history 库的时间戳风格一致
MARK_FILE = ".last_used"
TIME_FMT = "%Y-%m-%d %H:%M:%S"

# 超过这个天数没用过的克隆，启动清扫时删掉
MAX_AGE_DAYS = 1.0


def clone_path(full_name: str) -> Path:
    """按仓库全名算固定克隆路径，owner/repo 转成 owner__repo。"""
    return CLONE_DIR / full_name.replace("/", "__")


def touch_clone(path: Path) -> None:
    """刷新克隆目录里的最后使用时间标记，目录不存在就什么都不做。"""
    if path.is_dir():
        try:
            (path / MARK_FILE).write_text(datetime.now().strftime(TIME_FMT), encoding="utf-8")
        except OSError:
            pass


def _last_used(path: Path) -> datetime:
    """读一个克隆目录的最后使用时间，标记文件缺了或坏了就退回目录修改时间。"""
    try:
        return datetime.strptime((path / MARK_FILE).read_text(encoding="utf-8").strip(), TIME_FMT)
    except (OSError, ValueError):
        return datetime.fromtimestamp(path.stat().st_mtime)


async def ensure_clone(full_name: str) -> str | None:
    """确保本地有这个仓库的克隆，返回目录路径，克隆失败返回 None。

    目录已存在且完整（.git 还在）就刷新时间戳直接复用；残缺目录先删掉重克隆。
    克隆走 --depth 1 只取当前快照省时省盘。PAT 运行时从 config 取（configure 注入后
    才生效），拼进 URL 私有库也能拉、还避开匿名限速。

    Args:
        full_name: owner/repo。
    Returns:
        克隆根目录的绝对路径字符串；克隆失败返回 None（残缺目录已清掉）。
    """
    path = clone_path(full_name)
    if (path / ".git").is_dir():
        touch_clone(path)
        return str(path)

    # 上次克隆到一半挂了会留残缺目录，删干净重来
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)

    pat = config.GITHUB_PAT
    url = (f"https://{pat}@github.com/{full_name}.git" if pat
           else f"https://github.com/{full_name}.git")
    # stdin 显式断开，防 git 在凭证异常时等终端输入挂死
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        print(f"[{full_name}] 🔴 克隆失败：{err.decode('utf-8', 'replace').strip()[:200]}")
        return None
    touch_clone(path)
    return str(path)


def sweep_clones(max_age_days: float = MAX_AGE_DAYS) -> int:
    """清掉超过 max_age_days 没用过的克隆，返回删了几个。程序启动时调一次。

    逐个目录读最后使用时间跟当前时间比对，超龄整删；散落的非目录文件顺手清掉。
    老版随机名目录没有标记文件，走目录修改时间兜底，过期照样被清。
    """
    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    removed = 0
    for entry in CLONE_DIR.iterdir():
        if not entry.is_dir():
            entry.unlink(missing_ok=True)
            continue
        age_days = (now - _last_used(entry)).total_seconds() / 86400
        if age_days > max_age_days:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed