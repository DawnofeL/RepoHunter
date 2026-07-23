# RepoHunter · GitHub 仓库发现与深度排序

学习一个新领域，通常需要几个对口的开源项目作参考。用 Claude Code、Codex 这类通用 agent 来找，存在几处明显短板，RepoHunter 针对每一处给出对应设计。

- **检索串行**。通用 agent 一次只能逐个翻看项目，无法并行探查大批候选。RepoHunter 对所有候选仓库并发探查，每条需求也并发辩论裁决，单次检索十来个候选即有两百来次模型调用并行执行。
- **探查深度不标准化**。深挖到什么程度取决于当时的对话，想看清就得反复追问。RepoHunter 在检索前把每条模糊需求编译成一句可判定的统一标准，所有仓库以同一尺度衡量。每个仓库都经过相同流程，用工具循环读取源码、产出客观拆解、再逐条判定需求，探查深度保持一致。
- **容易幻觉**。通用 agent 常把不满足要求的项目判定为满足。RepoHunter 要求每条判定的证据锚定到具体源码位置，再由代码在克隆仓库中核验文件与符号确实存在，无法核实的证据一律剔除。
- **召回大量无关项目**。结果里混入许多不相干的仓库，需要人工逐个排除。RepoHunter 由正反两方对抗取证、独立裁决判定每条需求是否命中，最终按命中数排序，相关项目排在前列。
- **缺乏记忆**。每次检索都从零开始。RepoHunter 将仓库拆解落库，再次检索到同一项目时直接复用。对话过程中也会自动提取长期记忆并按需召回，跨对话保留用户偏好。

从一句需求出发，自动到 GitHub 上搜出候选仓库、逐个克隆读真源码做客观架构拆解，再把你的每条需求交给正反两个 agent 对抗式取证、独立裁决判命中，最后按命中数排序。配一个完整的本地网页：左栏搜索、右栏就着搜到的仓库跟 AI 对话，带跨对话长期记忆和上下文压缩。界面中英文可切。

- **搜索侧**：需求翻成搜索词 → 候选池 → gate 粗筛 → 工具循环读源码出拆解 → 逐条需求对抗式辩论裁决 → 排序。
- **对话侧**：注入搜过的仓库跟 AI 聊，长期记忆自动提取、按需召回，历史太长自动压缩。



https://github.com/user-attachments/assets/4d1fa603-6913-4621-a887-9cbf39f2f2be



> ## 完整流程图
>
> **用浏览器打开 [`flowchart.html`](flowchart.html)**，一张图看清从需求到排序的全链路。

设计细节见 [`docs/Repo_Detection.md`](docs/Repo_Detection.md)。

踩坑历史与对应优化见 [`docs/踩坑.md`](docs/踩坑.md)。

https://github.com/user-attachments/assets/7374628d-dfed-4976-a3c3-88ce9684a375





---



## 快速启动

三个入口，按你的情况挑一个。**启动后都要在网页凭证页填 DeepSeek API key 和 GitHub PAT 才能真正搜索**，只开网页不填 key 打不了搜索。



### 方式一：Docker（推荐，最省心）

前提：装了 Docker。git、ripgrep、依赖全在镜像里，不用自己装。

```bash
git clone https://github.com/DawnofeL/New_Repo_Hunter.git
cd New_Repo_Hunter
docker compose up --build
```

构建完打开 `http://localhost:8755`。数据存本机 `./data`，容器删了也不丢。



### 方式二：裸机手动（不用 Docker）

前提：`python3`（3.10+）、`git`、`ripgrep`。git 用来克隆被分析的仓库，ripgrep 供代码搜索工具用，缺了搜索会瘫。

```bash
# 1. 装系统命令（缺哪个装哪个）
sudo apt-get install -y git ripgrep      # macOS: brew install git ripgrep

# 2. 克隆
git clone https://github.com/DawnofeL/New_Repo_Hunter.git
cd New_Repo_Hunter

# 3. 独立环境（二选一）
conda create -n repohunter python=3.11 -y && conda activate repohunter
# 或： python3 -m venv .venv && source .venv/bin/activate

# 4. 装依赖并启动
pip install -r requirements.txt
python run.py
```

会自动打开 `http://localhost:8755`，没自动开就手动打开。



### 方式三：交给 Coding Agent 一句话

对你的 coding agent 说一句「照 INSTALL.md 把这个项目装好启动」，它会自己检测环境、在上面 Docker / 裸机里选合适的一条装好并启动。指令见 [`INSTALL.md`](INSTALL.md)。



---



## 使用

1. 打开 `http://localhost:8755`。
2. 凭证页填 **DeepSeek API key** 和 **GitHub PAT**（填一次自动记住，存在本机 `data/creds.json`，不进 git）。
3. 左栏写需求、发起搜索；右栏点结果里的「+」把仓库注入对话，跟 AI 聊它。
4. 界面右上角可切中英文。



---



## 数据与隐私

搜索记录、对话记忆、你填的凭证、克隆下来的仓库，全部落在项目的 `data/` 目录，`data/` 不进 git，也不上传任何地方。clone 下来是干净空白的，别人的记录不会带过来。
