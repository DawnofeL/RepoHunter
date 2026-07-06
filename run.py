"""一键启动 webapp 的脚本。

起 uvicorn 跑 webapp 后端，同时后台开浏览器到本地地址。`open_browser` 按平台唤起
浏览器（WSL 走 Windows 互操作，其余用标准库）；主流程在 __main__ 里先开后台线程
唤浏览器、再阻塞起服务。端口被占就改下面的 PORT，或终端跑 pkill -f "uvicorn webapp"。
"""

import platform
import subprocess
import threading
import time
import webbrowser

import uvicorn

# 服务端口，被占就改这里
PORT = 8755
URL = f"http://127.0.0.1:{PORT}"


def open_browser() -> None:
    """等服务起来后，按平台唤起浏览器打开本地页面。

    WSL 环境没有图形浏览器，标准库 webbrowser 不顶用，改走 Windows 互操作唤起
    Windows 默认浏览器；非 WSL 直接用标准库打开。
    """

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