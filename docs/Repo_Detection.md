# Repo Detection

从用户需求到排序结果的完整流水线，按处理顺序分四个阶段：S1 把需求翻成查询并检索出候选池，S2 编判定标准、预抓资料、gate 粗筛方向，S3 读真源码出客观拆解，S4 逐条 keypoint 辩论裁决后排序。`content_filter.py` 是把 S2 到 S4 串起来的总编排，`pipeline.py` 再往外套一层、把 S1 也接上。实现细节看各文件 docstring，本文档只管地图和路线。

```text
hunter/
├── pipeline.py                       # run_pipeline：串联 QU → keypoint 编译 → 检索 → Repo_Detection
├── config.py                         # 密钥、各阶段模型名、共用 DeepSeek 客户端、并发闸、克隆目录、load_skill
├── cost.py                           # token 计量：COST 按阶段汇总 + TokenMeter 盯单条调用链
├── clients.py                        # GitHub MCP 连接封装，检索阶段用它搜仓库
├── pre_filter/                       # S1、S2 的文件
│   ├── query_understanding.py
│   ├── keypoint_understanding.py
│   ├── repo_languages.py
│   ├── search_repo.py
│   └── skills/                       # query_understanding.md · keypoint_understanding.md
├── repo_detection/                   # S2、S3、S4 的文件
│   ├── content_filter.py
│   ├── prefetch.py
│   ├── agent_tools.py
│   ├── tool_loop.py
│   ├── log_visual.py
│   ├── explorer.py
│   ├── audit.py
│   ├── debate.py
│   ├── scoring.py
│   └── skills/                       # system_header · skip_gate · content_filter · advocate · skeptic · adjudicate
├── memory/                           # 占位 shim（未迁移，休眠）
└── chat/                             # 占位桩（未迁移，休眠）
```

## S1 QU & Repo Search

用户的需求清单进来，抽出 GitHub 能搜的查询词，语言字段纠错归一后当硬过滤，检索去重出候选仓库池。

按序读三个文件：

1. `pre_filter/query_understanding.py`：调模型从需求清单抽搜索查询，keypoints 原样带回不改写；输出 JSON 坏掉走修格式重试、正则抠取、空壳兜底三级。skill 在 `skills/query_understanding.md`。
2. `pre_filter/repo_languages.py`：语言名三级归一（别名、精确、模糊），两字母以内不走模糊防错纠；Python 和 Jupyter Notebook 绑成一对同搜。
3. `pre_filter/search_repo.py`：每条查询剥引号合成单条 OR 组（带引号短语和 OR 混用 GitHub 会静默返回零结果），拼上语言、归档、fork 限定并发搜，按仓库全名并集去重。

## S2 Pre-Filter

深挖前的三样准备：把每条 keypoint 编译成一句可判定的标准，让所有仓库用同一把尺子；预抓 gate 和后续角色要看的资料；gate 只看资料判整体方向，明显不符的软跳过（置灰沉底不淘汰，拿不准一律放行）。

按序读三处：

4. `pre_filter/keypoint_understanding.py`：每条 keypoint 独立并发编译成一句判定标准，单条失败给空串不挡流程。skill 在 `skills/keypoint_understanding.md`。
5. `repo_detection/prefetch.py`：走 GitHub API 预抓三样：README（按标题砍掉 changelog、license 等噪声段再截断）、两层目录树、stars 和体积。体积拼进 system 给模型判体量，防 README 自称轻量误判。
6. `repo_detection/explorer.py` 里的 `_call_gate`：不带工具的一次调用，只看预抓资料判 skip 还是 proceed，解析失败默认放行（错跳不可逆）。skill 在 `skills/skip_gate.md`。

## S3 Content Filter

gate 放行的仓库浅克隆到本地，explorer 拿四个只读工具循环读真源码，产出一份不含任何 keypoint 的客观架构拆解（目的、技术栈、带锚点的关键设计、架构总览）。拆解里每个锚点由代码对克隆核验，对不上先打回让模型重修一次，仍对不上删点兜底。

按序读五个文件，先工具后主循环：

7. `repo_detection/agent_tools.py`：四个只读工具的后端（列目录、读文件带行号和已读缓存、ripgrep 正则搜索、按文件名找文件）加发给模型的工具清单，全部对本地克隆操作。
8. `repo_detection/tool_loop.py`：explorer 和辩论共用的循环底件：解析模型输出 JSON 带兜底、执行单个工具调用并兜错、拦截重复读 README 和根目录。
9. `repo_detection/explorer.py`：全篇核心。主循环每轮把工具摆给模型，模型调工具就执行回灌进下一轮，不调了这轮回复就是最终拆解；工具预算二十次用满逼停，半程和停滞各注入一次提醒。skill 在 `skills/content_filter.md`，共享 system 模板在 `skills/system_header.md`。
10. `repo_detection/audit.py`：锚点审计与自愈：逐个核对拆解里的文件路径和符号真实存在，坏的拼成重修指令打回，重修后仍坏的删掉；辩论论据的锚点也走同一套核验。
11. `repo_detection/log_visual.py`：Visual 和 ReAct 两种日志格式化，不影响主线，扫一眼即可。

## S4 Debate & Adjudicate

拆解出来后，keypoint 判定走辩论：每条 keypoint 一条独立链路，正反两个带工具的 worker 并行取证（正方找支持证据、反方找反证），锚点核验后交一个上下文干净的裁决者判 hit 或 miss。所有链路整体并发，判定结果按下标对齐回清单，最后按命中数排序。

按序读两个文件：

12. `repo_detection/debate.py`：单个立场 worker 的小号工具循环（预算四次）、正反和裁决三方的 prompt 填充、强制 JSON 的裁决请求。skills 在 `advocate.md`、`skeptic.md`、`adjudicate.md`。
13. `repo_detection/scoring.py`：判定按位置对齐回发出的清单（不靠模型照抄原文），漏判补 miss；数命中数不算分不设及格线；排序按命中数降序、平手比 stars，跳过和降级的沉底。

## 完整运行逻辑

需求清单和语言过滤进来，Query_Understanding 翻成搜索查询（keypoints 原样带回），Keypoint_Understanding 给每条编一句判定标准，语言归一后拼进硬过滤，Search_Repositories 并发搜出去重候选池。

候选池交给 Repo_Detection，所有仓库并发跑，单仓库内部：prefetch 抓 README、目录树、stars/size 拼成六样资料页 system；gate 只看资料页判方向，跳过的直接出结果沉底；放行的浅克隆到本地，explorer 工具循环读源码出拆解、过锚点审计；然后按 keypoint 扇出，每条一对正反 worker 并行取证、核锚点、立刻单条裁决。全部跑完 scoring 按命中数排序。

省钱主线是 DeepSeek 前缀缓存：同一仓库的 gate、explorer、正反方四次调用共享逐字节相同的资料页 system，README 那三千多字只真算一次钱；多轮循环只往消息末尾追加、绝不改写历史，每轮前缀都完整命中。

memory 在这条线里休眠：查记忆永远空手、每个仓库实打实深挖，写回是空操作。接回真 memory 模块后命中的仓库跳过 explorer 直接复用拆解。
