---
name: triage
description: 分诊,判架构拆解够不够判这条需求,不够就列出要看哪些源码
---

# 任务

上面 system 给了这个仓库的资料和一份架构拆解。给你文末一条需求,判断手上的拆解够不够判这个仓库满不满足它。

够判就直接判够。不够就列出还要看哪几个文件或符号,交给程序把那几段源码抠出来,下一步再判。

# 判够不够

- 拆解的 key_designs 里有跟这条需求直接对应的设计、带 `where` 锚点、说清了做没做 → 够。
- 需求是关于体量或热度的(如「轻量」「star 多」),看 system 里的体积和 star 数字就能判 → 够。
- 需求问的能力拆解没提、只含糊带过、或没落到具体文件 → 不够。
- 拿不准算不够。取证只是让程序抠几段源码、代价很小,判不够去核实比凭空判稳。

# 不够时怎么列取证清单

`need` 是一个数组,每项固定两个字段,字段名必须是 `path` 和 `symbol`:

- `path`:仓库内的文件相对路径,必须指向具体文件、不是目录。优先照 key_designs 里相关的 `where` 锚点填,那些路径已被核验过真实存在。
- `symbol`:该文件里要看的类名或函数名。不确定就填空字符串(会取该文件开头)。不要填文件名、不要填自然语言描述。
- 最多列 6 项,挑跟这条需求最相关的,不要贪多、不要列重复的文件。

# 输出

只输出一个 JSON,不要别的话。

够判:

```json
{"sufficient": true, "need": []}
```

不够,要看源码:

```json
{"sufficient": false, "need": [{"path": "tradingagents/graph/setup.py", "symbol": "setup_graph"}, {"path": "tradingagents/dataflows/loader.py", "symbol": ""}]}
```

反例(会被程序丢弃):`path` 填了目录如 `"tradingagents/"`;`symbol` 填成文件名或「数据加载逻辑」这类描述;字段名写成 `file`、`name`、`function`。

# 你要处理的需求

{keypoint}