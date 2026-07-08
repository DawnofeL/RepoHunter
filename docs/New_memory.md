# 记忆模块文件结构

记忆分两摊，各自一个包、一个库：搜索侧的记忆迁移后跟搜索历史合并进了 `hunter/history`，对话侧的记忆（S3）还在 `hunter/memory` 休眠。设计蓝本是 RepoHunter/docs/记忆模块.md，本文档只管新项目的文件结构和接入方式，设计原理不重复。

## 一、hunter/history（搜索侧，已迁移）

原来的仓库记忆（S2）和搜索历史是两个东西、两个库，信息高度重复：同一份拆解在每次搜索里各抄一份。合并后两张表一个库（`data/history.db`），用 full_name 挂钩、拆解全库只存一份。

```
hunter/history/
├── __init__.py          门面：re-export 两个子模块的对外函数
├── history_db.py        共享 history.db 连接（DB_PATH + _connect）
├── repo_memory.py       仓库账本：init / load_memories / upsert_repos / list / get / get_dissections / delete / clear
└── runs.py              搜索账本：init / save_run / list / get（JOIN 回拆解）/ delete_repo_from_run / delete_process / delete_run / clear
```

两个子模块各自就一个文件，直接 .py 不套文件夹（单文件不值得套一层）；对话侧 S3 的 extract、compact 每个有多个文件才套文件夹。

两张表分工：

- **repo_memory（一仓库一行）**：full_name、description、tags、dissection、stars、size、language、read_files、seen_runs、skip_note、analysed_at、last_seen。dissection 贵、静态不变，全库只此一份。seen_runs 是这仓库历次被搜到的轻量摘要（run_id、时间、那次 keypoints、命中几条），点开仓库看审计时间线。
- **runs（一次搜索一行）**：id、ts、query、languages、keypoints、top_k、output_language、total、top_name、cost、process、results。results 是这次每个仓库的轻量记录（判定、trace、tokens），**不含 dissection**，只留 full_name 指向 repo_memory。

写入只走 `save_run` 一个口：先插 run 行拿 run_id，再把这批仓库 upsert 进 repo_memory（拆解只存一份、这次搜索追进各自 seen_runs）。读回放一次搜索时 `get_run` 按名字把拆解 JOIN 回来拼成完整 ranked，前端拿到的形状不变。仓库被删掉后旧 run 取不到拆解，给空 dict 当「拆解已删除」，不报错（容忍悬空引用）。清理各清各的：清 runs 不动 repo_memory。

## 二、hunter/memory（对话侧 S3，未迁移）

对话记忆跟搜索无关，是右栏对话的记忆，仍在 `hunter/memory` 放占位空壳，等 S3 迁移。目标结构：

```
hunter/memory/
├── __init__.py          门面：对话记忆真实导出（现在全是占位空壳）
├── memory_db.py         共享连接（S3 迁移时新建）
│
├── chat_history/        S3.1 对话记录
│   └── store.py         chat_sessions 表：save / get / list / delete / rename / set_pinned
│
├── extract/             S3.2 提取记忆
│   ├── secret_scan.py   落库前密钥扫描打码
│   ├── chat_memories.py chat_memories 表：apply_actions、list_general / list_by_repo / list_by_session / list_manifest、提取游标
│   ├── extractor.py     提取编排：攒轮触发、单飞补跑、防幻觉校验、调模型落库
│   └── skills/extract_memories.md
│
├── compact/             S3.3 会话笔记与压缩
│   ├── store.py         chat_sessions 补 notes / notes_cursor / notes_tokens 三列 + accessor
│   ├── agent.py         笔记更新编排：token 阈值触发、单飞补跑
│   ├── compact.py       压缩三件套：笔记顶替、摘要兜底、连败熔断硬截断
│   └── skills/session_notes.md、compact_summary.md
│
└── recall/              S3.4 记忆召回
    ├── select.py        清单选择器 + 跳过门 + memoryAge + 过时告诫
    └── skills/recall_memories.md
```

对话层（跟 S3 咬合，迁 S3 时一起替换占位）：

```
hunter/chat/
├── __init__.py          门面：导出 stream_chat
├── session.py           对话主流程：拼 system、流式调模型、收完触发提取 / 笔记 / 压缩
├── context.py           拼 system：人设 + 通用记忆 + 注入仓库 + 会话中召回
└── skills/llm_skill.md
```

## 组织规则

子模块有多个文件（store + agent + secret_scan + skills）才套文件夹，单文件就直接一个 .py（history 的 repo_memory.py、runs.py 就是这样）。`__init__.py` 门面是唯一对外出口。子模块之间的交叉引用直接走对方模块（如 runs.py 引 repo_memory.py 的 get_dissections），不走门面，避免循环 import。

## 接入点

搜索侧（已接好）：

- hunter/repo_detection/content_filter.py 用 `history.load_memories` 查仓库账本，命中的把拆解传给 explore_one 跳过 explorer，use_memory 开关只挡这步读
- hunter/pipeline.py 搜完调 `history.save_run` 一次写两张表，不受开关影响
- webapp/backend/server.py 启动调 `history.init_runs` + `history.init_repo_memory` 建表；/history 五个路由走搜索账本、/memory 四个路由走仓库账本

对话侧（S3，钩子已在，占位休眠）：

- server.py 启动调 memory.init_chat_sessions / init_chat_memories / init_notes，/chat 路由流式对话，/chat/session 五个路由管会话存取
- 前端 app.js 的记忆页、会话列表、记忆开关均已就位

## config 需要的改动

config.py 的 SKILL_DIRS 目前只列了 pre_filter 和 repo_detection。对话记忆的 skill 到 S3 迁移时追加：

```python
SKILL_DIRS = [
    _HUNTER_DIR / "pre_filter" / "skills",
    _HUNTER_DIR / "repo_detection" / "skills",
    _HUNTER_DIR / "memory" / "extract" / "skills",
    _HUNTER_DIR / "memory" / "compact" / "skills",
    _HUNTER_DIR / "memory" / "recall" / "skills",
    _HUNTER_DIR / "chat" / "skills",
    _HUNTER_DIR.parent / "skills",
]
```

搜索侧不含任何 skill，这个改动到 S3 才需要。

## 迁移状态

| 子模块 | 归属 | 状态 |
|---|---|---|
| history/repo_memory + history/runs | 搜索侧 | 已迁移、已合并 |
| memory/chat_history | S3.1 对话侧 | 未开始 |
| memory/extract | S3.2 对话侧 | 未开始 |
| memory/compact | S3.3 对话侧 | 未开始 |
| memory/recall | S3.4 对话侧 | 未开始 |
| chat 包 | S3 对话侧 | 未开始 |

## 前端 Vault（已做）

右上角旧的两个入口（Repo Memory、History）已并成一个 `⚙️ 记忆库`（英文 Memory），进去是双栏：左栏搜索记录（按次，展开看那次的花费/查询词/命中仓库），右栏仓库大厅（浏览所有仓库）。点左栏「查看拆解」或右栏仓库卡，右栏切成该仓库的拆解详情 + seen_runs 时间线并丝滑扩宽，复用工作台对话那套 `expanded` 过渡。进 vault 后右上角入口换成「← 搜索」返回工作台。两栏头部的「返回大厅」「清空」是同一套小巧按钮样式（`.vhbtn`）。后端 /history、/memory 两组路由形状没变，只是前端换了一个统一入口消费。

旧的四个视图（view-history / view-history-detail / view-memory / view-memory-detail）和对应的旧渲染函数、状态、事件绑定已全部清除，公共调度（showView / restoreView / applyLang / STAGE_LABEL）里的相关引用也一并拔掉。Vault 复用到的 loadHistory / loadMemory / memCardHTML / headerMetaHTML / renderCost / renderRanked 保留。