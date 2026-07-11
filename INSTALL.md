# INSTALL · 给 Coding Agent 的安装指南

严格按顺序执行，遇到不确定的地方先检测再决定，不要臆测。全程不得删除或覆盖用户已有的任何环境，本项目一律新建独立环境。API key 安装阶段一概不碰、不索取，启动后由用户在网页里填。

---

## 0. 前置检测

先探测环境，据此选安装路径：

1. 有没有 Docker（`docker --version` 且 `docker compose version` 都可用）。有就优先走 A 路（Docker），最省事。
2. 没 Docker，走 B 路（裸机）。裸机需要三样系统命令：`python3`（3.10 及以上）、`git`、`ripgrep`（命令名 `rg`）。逐个用 `--version` 检测哪个缺。

选定路径后往下走对应那一节。

---

## A 路：Docker（推荐）

### A1. 克隆仓库

先问用户：「项目想放哪个文件夹？已经下载过的话，把本地路径告诉我。」等用户给出路径再执行，不要擅自推进。

```bash
git clone https://github.com/DawnofeL/New_Repo_Hunter.git <用户指定路径>/New_Repo_Hunter
cd <用户指定路径>/New_Repo_Hunter
```

用户已 Download ZIP 解压的，跳过 clone，直接 cd 进目录。

### A2. 起服务

```bash
docker compose up --build
```

镜像里已装好 git、ripgrep 和全部依赖，第一次构建要下基础镜像和装依赖，几分钟。构建完服务在 8755 端口起来。

### A3. 打开网页

容器里没有浏览器，让用户自己开：`http://localhost:8755`。

---

## B 路：裸机（不用 Docker）

### B1. 装系统命令（缺哪个装哪个）

`git` 和 `ripgrep` 是运行时功能依赖：git 用来克隆用户要分析的仓库，ripgrep 供代码搜索工具用，缺了搜索功能会瘫。按检测结果装缺的：

```bash
# Debian / Ubuntu / WSL
sudo apt-get update && sudo apt-get install -y git ripgrep

# macOS（Homebrew）
brew install git ripgrep
```

### B2. 克隆仓库

同 A1，先问路径再执行。

```bash
git clone https://github.com/DawnofeL/New_Repo_Hunter.git <用户指定路径>/New_Repo_Hunter
cd <用户指定路径>/New_Repo_Hunter
```

### B3. 新建独立 Python 环境

conda（优先）：

```bash
conda create -n repohunter python=3.11 -y
conda activate repohunter
```

没有 conda 时用 venv：

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
```

### B4. 装 Python 依赖

```bash
pip install -r requirements.txt
```

### B5. 启动

```bash
python run.py
```

非 Docker 环境会自动打开 `http://localhost:8755`；没自动开就让用户手动打开。

---

## 完成后向用户报告

- 走的哪条路（Docker / 裸机）、项目路径、访问地址 `http://localhost:8755`。
- 提醒：**要真正搜索，得先在网页凭证页填 DeepSeek API key 和 GitHub PAT**，填完才能用。只开网页不填 key 打不了搜索。
- 数据（搜索记录、对话、凭证、克隆的仓库）都存在项目的 `data/` 目录，不进 git、不外泄。
- 如需卸载，告知用户直接说「卸载」，agent 运行对应清理：Docker 路 `docker compose down --rmi local -v` 再删项目目录；裸机路删掉新建的环境（conda env remove / 删 .venv）再删项目目录。清完自检干净后向用户汇报，最后加一句「感谢您花时间体验本项目」。
