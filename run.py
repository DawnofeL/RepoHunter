"""一键启动 webapp 的脚本。

起 uvicorn 跑 webapp 后端，同时后台开浏览器到本地地址。`open_browser` 按平台唤起
浏览器（WSL 走 Windows 互操作，其余用标准库）；主流程在 __main__ 里先开后台线程
唤浏览器、再阻塞起服务。端口被占就改下面的 PORT，或终端跑 pkill -f "uvicorn webapp"。
在 Docker 容器里跑时没有浏览器，检测到就不唤起、只打印访问地址。
"""

import os
import platform
import subprocess
import threading
import time
import webbrowser

import uvicorn

# 服务端口，被占就改这里
PORT = 8755
URL = f"http://127.0.0.1:{PORT}"


def _in_container() -> bool:
    """粗判是不是跑在容器里：Docker 会留 /.dockerenv，或 cgroup 里带 docker/containerd 字样。"""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as f:
            return any(x in f.read() for x in ("docker", "containerd", "kubepods"))
    except Exception:
        return False


def open_browser() -> None:
    """等服务起来后，按平台唤起浏览器打开本地页面。

    容器里没有浏览器，直接跳过只提示地址；WSL 走 Windows 互操作唤起 Windows 默认浏览器；
    非 WSL 直接用标准库打开。
    """

    # 容器里唤不起浏览器，别白试，直接提示用户自己打开
    if _in_container():
        print(f"服务已启动，请在浏览器打开 {URL}")
        return

    # 先等 uvicorn 起来，免得浏览器先到、页面还没就绪
    time.sleep(1.5)

    # WSL 的判据是内核 release 串里带 microsoft
    is_wsl = "microsoft" in platform.uname().release.lower()
    if is_wsl:

        # 几种唤起方式挨个试，哪个命令在就用哪个，全都不在就放弃
        for cmd in (["wslview", URL], ["explorer.exe", URL], ["cmd.exe", "/c", "start", "", URL]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except FileNotFoundError:
                continue
        return

    # 非 WSL（原生 Linux/Mac/Windows）直接用标准库开
    try:
        webbrowser.open(URL)
    except Exception:
        pass


if __name__ == "__main__":

    # 后台线程开浏览器，主线程照常起服务，互不耽误
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("webapp.backend.server:app", host="0.0.0.0", port=PORT)