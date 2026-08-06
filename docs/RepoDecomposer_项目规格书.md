# Repo Decomposer · 项目规格书

> 本文档供 Claude Code 实现参考。第一部分是项目全景，第二部分是第一个开发模块（布局引擎）的完整规格，含 RepoHunter flowchart 的真实数据示例。

---

## 第一部分：项目全景

### 1.1 产品定位

代码仓库可视化 SaaS。用户提交 GitHub 仓库链接，AI 读源码后生成叙事型架构流程图——用自然语言描述每个模块的职责和关系，而非罗列函数名。

**目标用户**：vibe coding 创业者（非技术背景，用 AI 生成代码但看不懂自己项目的架构）。

**核心卖点**：
- 模块用自然语言解释，不是符号列表
- 点击节点展开抽屉看详细说明
- 代码更新后通过 git diff 增量更新图
- 可导出单文件 HTML 交互图（付费功能）

**参考原型**：RepoHunter 项目的 flowchart.html（33 个节点、手工排版、抽屉交互）。本产品的目标是将这种效果自动化。

### 1.2 技术架构

```
代码仓库
   │
   ▼
┌─────────────────────────────┐
│  ① LLM 分析管线 (Python)    │  ← 读源码，输出语义 JSON
└─────────────────────────────┘
   │ 语义 JSON（无坐标）
   ▼
┌─────────────────────────────┐
│  ② 布局引擎 (Python)        │  ← 算坐标，纯函数，确定性
└─────────────────────────────┘
   │ 几何 JSON（有坐标）
   ▼
┌─────────────────────────────┐
│  ③ 渲染器 (TypeScript)      │  ← 照着画，不做任何判断
└─────────────────────────────┘
   │
   ▼
  交互式流程图
```

**三层完全解耦**：LLM 换模型不影响布局，布局改规则不影响渲染，渲染改样式不影响数据。

### 1.3 模块清单

| # | 模块 | 语言 | 职责 | 核心/外包 |
|---|------|------|------|-----------|
| ① | LLM 分析管线 | Python | 读代码，输出语义 JSON | 核心（已有 RepoHunter 经验） |
| ② | 数据契约 | JSON Schema | 定义语义 JSON 的字段和校验规则 | 核心 |
| ③ | 布局引擎 | Python | 语义 JSON → 坐标 JSON | **核心，本文重点** |
| ④ | 渲染器 | TypeScript | 坐标 JSON → DOM/SVG 交互图 | 核心 |
| ⑤ | 导出系统 | Vite 打包 | 渲染器 + 用户数据 → 单文件 HTML | 可外包 Claude Code |
| ⑥ | 后端 API | FastAPI | 用户管理、项目存储、分析任务调度 | 可外包 Claude Code |
| ⑦ | MCP 集成 | Python | 把图信息暴露给 Claude Code 等 AI 工具 | 可外包（你的主场） |

### 1.4 开发顺序

```
第一阶段（1-2 月）：③ 布局引擎 + ② 数据契约
  → 验证标准：输入 RepoHunter 的语义 JSON，输出坐标与 flowchart.html 一致

第二阶段（2-3 月）：④ 渲染器
  → 验证标准：双击 HTML 就能看到交互式流程图，不需要服务器

第三阶段（3-5 月）：① 管线对接 + ⑥ 后端
  → 验证标准：提交 GitHub 链接，自动出图

第四阶段（5-6 月）：⑤ 导出 + ⑦ MCP
  → 验证标准：能导出 HTML、能被 Claude Code 调用
```

---

## 第二部分：布局引擎规格

### 2.1 职责与边界

**做什么**：一个纯 Python 函数，输入语义 JSON，输出几何 JSON。

**不做什么**：
- 不读代码（那是 LLM 管线的事）
- 不画图（那是渲染器的事）
- 不联网、不调 API、不访问文件系统
- 不包含任何非确定性行为（同一输入永远同一输出）

```python
def layout(semantic_json: dict) -> dict:
    """纯函数，无副作用，毫秒级完成"""
    ...
```

### 2.2 数据契约

#### 输入：语义 JSON

LLM 分析完代码后输出的结构。**全程没有任何坐标。**

```json
{
  "stages": [
    {"id": "s1", "name": "S1 · 需求理解与检索", "order": 1},
    {"id": "s2", "name": "S2 · 编译标准与粗筛", "order": 2}
  ],

  "nodes": [
    {
      "id": "A",
      "role": "main",
      "seq": 1,
      "stage": "s1",
      "kind": "start",
      "title": "需求清单",
      "sub": "keypoints + 语言过滤 入口"
    },
    {
      "id": "TL",
      "role": "support",
      "anchor": "EXP",
      "stage": "s3",
      "kind": "glass",
      "title": "四个只读工具",
      "sub": "列 读 搜 找 全对克隆操作"
    },
    {
      "id": "QU",
      "role": "parallel",
      "stage": "s1",
      "kind": "glass",
      "title": "抽搜索查询",
      "sub": "从需求清单抽项目身份概念"
    }
  ],

  "edges": [
    {"from": "A", "to": "PIPE", "type": "flow"},
    {"from": "EXP", "to": "TL", "type": "flow", "from_side": "l", "to_side": "r", "label": "每轮调用"},
    {"from": "RCL", "to": "CTX", "type": "feedback", "channel": 1380, "label": "命中补段"}
  ],

  "parallels": [
    {
      "fork": "PIPE",
      "join": "PF",
      "branches": [
        ["QU", "LG", "SR", "POOL"],
        ["MC"],
        ["KU"]
      ]
    }
  ]
}
```

**字段说明**

| 字段 | 位置 | 说明 |
|------|------|------|
| `role` | 节点 | `main` = 主干；`support` / `fallback` / `aside` = 侧挂；`parallel` = 并行分支内 |
| `seq` | main 节点 | 主干上第几步（整数，连续） |
| `anchor` | 侧挂节点 | 挂在哪个 main 节点旁边 |
| `stage` | 节点 | 属于哪个阶段（影响区域水印和间距） |
| `kind` | 节点 | 视觉样式：`start` / `core` / `glass` / `out` / `warn` |
| `fork` | parallels | 并行块从哪个 main 节点后分叉 |
| `join` | parallels | 并行块在哪个 main 节点前汇合 |
| `branches` | parallels | 每条分支包含的节点 id 列表，按执行顺序排列 |
| `type` | 边 | `flow`（实线）/ `dash`（虚线）/ `feedback`（回环虚线） |
| `from_side` / `to_side` | 边 | 连接点方位：`t`(上) / `b`(下) / `l`(左) / `r`(右)，省略则自动 |
| `channel` | feedback 边 | 回环走哪条竖直通道（x 坐标） |

**核心设计原则：LLM 只做选择题。** role 是五选一，kind 是五选一，anchor 是从 main 节点里选一个。没有开放题，没有数值，没有坐标。

#### 输出：几何 JSON

布局引擎计算完成后输出的结构。**渲染器拿到这个，照着画就行。**

```json
{
  "canvas": {"width": 2400, "height": 3000},

  "nodes": {
    "A":    {"x": 1180, "y": 70,   "w": 280, "h": 80},
    "PIPE": {"x": 1180, "y": 200,  "w": 280, "h": 88},
    "TL":   {"x": 820,  "y": 1270, "w": 260, "h": 76}
  },

  "edges": [
    {
      "from": "A", "to": "PIPE",
      "path": [[1180, 110], [1180, 160]],
      "type": "flow"
    },
    {
      "from": "EXP", "to": "TL",
      "path": [[1040, 1270], [950, 1270]],
      "type": "flow",
      "label": "每轮调用",
      "label_pos": [995, 1256]
    }
  ],

  "regions": [
    {"stage": "s1", "x": 700, "y": 50, "w": 1000, "h": 720, "name": "S1 · 需求理解与检索"}
  ]
}
```

### 2.3 设计常量

这些数字来自 RepoHunter flowchart.html 的实测值。它们是**审美参数**——你觉得间距太窄就调大，不影响算法逻辑。

```python
# ── 画布 ──
CENTER_X = 1180          # 主干中轴线 x 坐标
START_Y = 70             # 第一个节点的 y

# ── 纵向间距 ──
ROW_GAP = 120            # 默认行距
CORE_EXTRA = 20          # 相邻节点有 core 时额外加的间距（总共 140）
STAGE_EXTRA = 20         # 跨阶段时额外加的间距

# ── 并行分支 ──
PARALLEL_ENTRY = 170     # 主干节点到并行块第一行的间距
PARALLEL_PITCH = 300     # 并行分支左右偏移量（2 分支: ±300, 3 分支: -300/0/+300）
PARALLEL_EXIT = 140      # 并行块最后一行到下一个主干节点的间距

# ── 侧挂偏移（x 方向，相对于 anchor 的 x）──
LANE = {
    "support":  -360,    # 左侧，支撑/工具类
    "fallback": +360,    # 右侧，兜底/熔断类
    "aside":    +480,    # 远右侧，旁注/日志类
}

# ── 侧挂冲突 ──
SIDE_STACK_GAP = 120     # 同一 anchor 同一侧有多个节点时，纵向错开距离
LANE_PUSH = 240          # 同一侧车道挤不下时，往外推的距离（如 aside 第二道: +480+240=+720）

# ── 碰撞消解（步骤 3.5）──
COLLISION_BUFFER = 40    # 两个节点边框之间的最小空隙
PUSH_STEP = 40           # 每次推开的步长
MAX_PUSH_ROUNDS = 50     # 最大迭代轮数，超过则判定输入结构不合理
BRANCH_BUFFER = 60       # 相邻并行分支车道之间的最小空隙

# ── 角色优先级（数值越大越不让路）──
PRIORITY = {
    "main":     100,     # 主干，永不移动
    "parallel":  80,     # 并行分支节点，只在分支整体平移时移动
    "output":    60,
    "support":   40,
    "fallback":  40,
    "aside":     20,     # 最低，撞了它先让
}

# ── 节点尺寸 ──
NODE_W = {"core": 280, "default": 260}
NODE_H = {"core": 88, "default": 76, "start": 80}
```

### 2.4 算法流程

#### 步骤 0：校验

```python
def validate(data):
    # 1. 每个 main 节点必须有 seq，且 seq 连续无跳跃
    # 2. 每个侧挂节点的 anchor 必须指向一个存在的 main 节点
    # 3. parallels 中的 fork/join 必须是 main 节点
    # 4. parallels 中的节点 id 必须存在于 nodes 中
    # 不通过 → 抛错，上游 LLM 重新输出
```

#### 步骤 1：主干排布

从上往下，逐个放置 main 节点。遇到并行块时跳过（步骤 2 处理）。

```python
def layout_spine(nodes, parallels):
    positions = {}
    y = START_Y
    main_nodes = sorted([n for n in nodes if n["role"] == "main"], key=lambda n: n["seq"])
    
    prev = None
    for node in main_nodes:
        # 如果这个节点前面有并行块（即它是某个 parallel 的 join），先处理并行块
        par = find_parallel_joining_here(parallels, node["id"])
        if par:
            y = layout_parallel(par, y, positions)  # 步骤 2，返回并行块结束后的 y
            y += PARALLEL_EXIT
        else:
            if prev is not None:
                y += compute_gap(prev, node)
        
        positions[node["id"]] = {"x": CENTER_X, "y": y}
        prev = node
    
    return positions

def compute_gap(prev_node, curr_node):
    gap = ROW_GAP  # 120
    if prev_node["kind"] == "core" or curr_node["kind"] == "core":
        gap += CORE_EXTRA  # → 140
    if prev_node["stage"] != curr_node["stage"]:
        gap += STAGE_EXTRA  # → 140 或 160
    return gap
```

**关键**：`compute_gap` 是整个算法里唯一需要"判断"的地方，而且判断依据只有两个：kind 是不是 core、stage 有没有切换。

#### 步骤 2：并行分支排布

**朴素版本**（固定间距，适用于分支内节点没有侧挂的情况）：

```python
def layout_parallel(par, fork_y, positions):
    branches = par["branches"]
    n = len(branches)
    
    # 计算每条分支的 x 偏移
    # 2 分支: [-300, +300]
    # 3 分支: [-300, 0, +300]
    # 4 分支: [-450, -150, +150, +450]
    offsets = [(i - (n - 1) / 2) * PARALLEL_PITCH for i in range(n)]
    
    branch_start_y = fork_y + PARALLEL_ENTRY
    max_end_y = branch_start_y
    
    for branch, offset in zip(branches, offsets):
        bx = CENTER_X + offset
        by = branch_start_y
        for j, node_id in enumerate(branch):
            if j > 0:
                by += ROW_GAP
            positions[node_id] = {"x": bx, "y": by}
        max_end_y = max(max_end_y, by)
    
    return max_end_y  # 并行块的最低点
```

**动态间距版本**（分支内节点有侧挂时必须用这个）：

固定 300 的间距只在"每条分支都是光杆节点"时成立。如果某条分支的节点自己还挂了 support/fallback，那条分支实际占用的横向宽度会超出 300，直接和相邻分支撞上。

解决办法：**先算出每条分支的实际宽度，再按实际宽度决定间距。**

```python
def branch_width(branch, nodes_by_id):
    """一条分支向左、向右各伸出多远（相对于分支中轴）"""
    left_reach = NODE_W["default"] / 2
    right_reach = NODE_W["default"] / 2
    
    for node_id in branch:
        # 找出挂在这个节点上的所有侧挂节点
        for side in side_nodes_anchored_to(node_id, nodes_by_id):
            offset = LANE[side["role"]]
            half_w = NODE_W["default"] / 2
            if offset < 0:
                left_reach = max(left_reach, -offset + half_w)
            else:
                right_reach = max(right_reach, offset + half_w)
    
    return left_reach, right_reach


def compute_branch_offsets(branches, nodes_by_id):
    """返回每条分支的 x 偏移，保证相邻分支不重叠"""
    widths = [branch_width(b, nodes_by_id) for b in branches]
    
    # 从左到右依次摆放，每条分支的位置由前一条的右边界决定
    offsets = [0.0]
    for i in range(1, len(branches)):
        prev_right = widths[i-1][1]      # 前一条分支向右伸出
        curr_left  = widths[i][0]        # 当前分支向左伸出
        pitch = max(PARALLEL_PITCH, prev_right + curr_left + BRANCH_BUFFER)
        offsets.append(offsets[-1] + pitch)
    
    # 整体居中：减去平均值，让并行块的视觉中心落在 CENTER_X
    center = (offsets[0] + offsets[-1]) / 2
    return [o - center for o in offsets]
```

**举例**：三条分支，中间那条的节点挂了一个 support（向左伸出 360+130=490）

```
朴素版：  offsets = [-300, 0, +300]
          → 中间分支的 support 落在 x = 1180 - 360 = 820
          → 左边分支在 x = 880
          → 820 和 880 相距 60，两张 260 宽的卡片直接叠一起 ✗

动态版：  widths = [(130,130), (490,130), (130,130)]
          分支0 → 分支1: pitch = max(300, 130+490+60) = 680
          分支1 → 分支2: pitch = max(300, 130+130+60) = 320
          offsets = [0, 680, 1000] → 居中后 [-500, +180, +500]
          → 中间分支的 support 落在 1180+180-360 = 1000
          → 左边分支在 1180-500 = 680
          → 1000 和 680 相距 320，两张卡片各占 130，中间还剩 60 空隙 ✓
```

**代价是图变宽了。** 这是必然的——信息量摆在那里，要么变宽，要么重叠。变宽可以靠画布拖拽解决，重叠不能。

**示例**：RepoHunter 的 PIPE → PF 并行块（3 分支）

```
fork_y = PIPE.y = 200
branch_start_y = 200 + 170 = 370

分支 0（左, offset=-300, x=880）:
  QU  → (880, 370)
  LG  → (880, 490)    370 + 120
  SR  → (880, 610)    490 + 120
  POOL→ (880, 730)    610 + 120

分支 1（中, offset=0, x=1180）:
  MC  → (1180, 370)

分支 2（右, offset=+300, x=1480）:
  KU  → (1480, 370)

max_end_y = 730
PF.y = 730 + 140 = 870
```

#### 步骤 3：侧挂节点

```python
def layout_side_nodes(nodes, positions):
    occupied = {}  # 记录每个 (x, y) 是否被占用
    
    side_nodes = [n for n in nodes if n["role"] in ("support", "fallback", "aside")]
    # 按 anchor 的 seq 排序，保证从上到下处理
    side_nodes.sort(key=lambda n: positions[n["anchor"]]["y"])
    
    for node in side_nodes:
        anchor_pos = positions[node["anchor"]]
        x_offset = LANE[node["role"]]
        x = anchor_pos["x"] + x_offset
        y = anchor_pos["y"]
        
        # 冲突检测：如果 (x, y) 附近已经有节点，往下错开
        lane = 0
        while is_occupied(occupied, x, y, lane):
            y += SIDE_STACK_GAP
            lane += 1
        
        positions[node["id"]] = {"x": x, "y": y}
        mark_occupied(occupied, x, y)
```

**示例**：EXP (1180, 1270) 的三个侧挂节点

```
TL (support):  x = 1180 + (-360) = 820,  y = 1270  → (820, 1270)
STOP (fallback): x = 1180 + (+360) = 1540, y = 1270  → (1540, 1270)
```

TL 和 STOP 不冲突（一个左一个右），所以都和 anchor 同行。

CLN 也是 support，anchor=AUD (1180, 1410)：
```
CLN (support):  x = 1180 + (-360) = 820,  y = 1410  → (820, 1410)
```

CLN 和 TL 不冲突（y 不同），正常放置。

**步骤 3 的局限**：它只检查"同一个 anchor 同一侧"的冲突。跨 anchor、跨分支、侧挂节点撞上并行分支——这些它都看不见。所以需要步骤 3.5 做一次全局兜底。

#### 步骤 3.5：全局碰撞消解

**这是整个引擎里唯一带循环搜索的部分。** 前面几步给出的是"通常正确"的初始布局，这一步负责抓漏网之鱼。

机制很朴素：**摆完 → 两两查重叠 → 查到就按优先级推开 → 再查一遍 → 直到没有重叠为止。**

```python
def resolve_collisions(positions, nodes_by_id):
    def box(nid):
        p = positions[nid]
        n = nodes_by_id[nid]
        w = NODE_W.get(n["kind"], NODE_W["default"])
        h = NODE_H.get(n["kind"], NODE_H["default"])
        return (p["x"] - w/2, p["y"] - h/2, p["x"] + w/2, p["y"] + h/2)

    for round_no in range(MAX_PUSH_ROUNDS):
        collisions = []
        ids = list(positions.keys())
        for i, a in enumerate(ids):
            for b in ids[i+1:]:
                if overlaps(box(a), box(b), buffer=COLLISION_BUFFER):
                    collisions.append((a, b))

        if not collisions:
            return positions          # 收敛，正常返回

        for a, b in collisions:
            pa = PRIORITY[nodes_by_id[a]["role"]]
            pb = PRIORITY[nodes_by_id[b]["role"]]

            if pa == pb == PRIORITY["main"]:
                # 两个主干节点重叠 = 步骤 1 的间距算错了，这是 bug 不是布局问题
                raise LayoutError(f"主干节点 {a} 与 {b} 重叠，检查 compute_gap")

            mover = a if pa < pb else b          # 优先级低的让路
            other = b if mover == a else a
            direction = push_direction(box(mover), box(other))
            positions[mover]["x"] += direction[0] * PUSH_STEP
            positions[mover]["y"] += direction[1] * PUSH_STEP

    # 循环 50 轮还没收敛
    raise LayoutError(
        f"布局无法收敛，剩余 {len(collisions)} 处重叠。"
        f"通常意味着某个 main 节点挂了过多侧挂节点，请检查语义 JSON。"
    )


def push_direction(mover_box, other_box):
    """沿重叠最浅的轴推开——推的距离最短，对布局破坏最小"""
    overlap_x = min(mover_box[2], other_box[2]) - max(mover_box[0], other_box[0])
    overlap_y = min(mover_box[3], other_box[3]) - max(mover_box[1], other_box[1])

    if overlap_x < overlap_y:
        # 横向推：往远离 other 的方向
        return (1, 0) if mover_box[0] > other_box[0] else (-1, 0)
    else:
        # 纵向推
        return (0, 1) if mover_box[1] > other_box[1] else (0, -1)
```

**三条设计规则**

**一、谁让谁由角色优先级决定，不是随机的。**

```
main (100)      ← 主干，永不移动。移动主干会破坏整张图的骨架
parallel (80)   ← 只在分支整体平移时移动，单个节点不动
output (60)
support (40) / fallback (40)
aside (20)      ← 最低，撞了它先让
```

这和坐标常量的设计哲学是一致的：**离主线越远的东西越不重要，冲突时就该它让路。**

**二、沿重叠最浅的轴推，不是随便挑方向。**

两个框重叠时，横向重叠 20px、纵向重叠 200px，那就横向推——推 20px 就能分开，推纵向要 200px。这保证每次推动对原有布局的破坏最小。

**三、推不开就报错，不硬挤。**

如果循环 50 轮还有重叠，说明输入本身有问题——真实项目里一个模块不会有 6 个平权的支撑工具。这时候应该把问题打回给上游 LLM 重新标注，而不是让引擎凑合出一张丑图。

这和步骤 0 的校验是同一个思路：**结构不合理就拒绝，不让算法硬扛。** 这也正是你相对于 Mermaid 的优势之一——Mermaid 不能拒绝输入，你能。

**为什么这个循环不会像力导向算法那样震荡**

三个原因：

1. **规模小**：10-30 个节点，两两比较不到 500 次，一轮几毫秒
2. **单向推动**：每次只有优先级低的一方移动，不存在"A 推 B，B 又推回 A"的往复
3. **有硬上限**：50 轮不收敛就报错，不会无限跑

力导向算法之所以不确定，是因为所有节点同时受力、互相影响、每轮全体移动。这里是**局部修补**——绝大多数节点在整个过程中一次都不会动。

**收敛后的验证**

```python
def assert_no_overlap(positions, nodes_by_id):
    """这个断言必须在每次布局后跑，它是引擎的质量底线"""
    ids = list(positions.keys())
    for i, a in enumerate(ids):
        for b in ids[i+1:]:
            assert not overlaps(box(a), box(b)), f"{a} 与 {b} 仍然重叠"
```

#### 步骤 4：边路由

```python
def route_edge(edge, positions):
    p1 = positions[edge["from"]]
    p2 = positions[edge["to"]]
    
    # 默认连接点：上节点的底部中点 → 下节点的顶部中点
    fs = edge.get("from_side", "b")  # bottom
    ts = edge.get("to_side", "t")    # top
    
    x1, y1 = anchor_point(p1, fs)
    x2, y2 = anchor_point(p2, ts)
    
    if edge["type"] == "feedback":
        # 回环：走指定的竖直通道
        ch = edge.get("channel", CENTER_X + 200)
        return [(x1, y1), (x1, y1 + 20), (ch, y1 + 20), (ch, y2 - 20), (x2, y2 - 20), (x2, y2)]
    
    if abs(x1 - x2) < 5:
        # 同一竖直线：直线连接
        return [(x1, y1), (x2, y2)]
    
    # 不同列：Z 形折线
    mid_y = (y1 + y2) / 2
    return [(x1, y1), (x1, mid_y), (x2, mid_y), (x2, y2)]
```

#### 步骤 5：区域水印

```python
def compute_regions(nodes, positions, stages):
    regions = []
    for stage in stages:
        stage_nodes = [n for n in nodes if n["stage"] == stage["id"]]
        if not stage_nodes:
            continue
        pts = [positions[n["id"]] for n in stage_nodes]
        min_x = min(p["x"] for p in pts) - 60
        min_y = min(p["y"] for p in pts) - 40
        max_x = max(p["x"] for p in pts) + 60
        max_y = max(p["y"] for p in pts) + 40
        regions.append({
            "stage": stage["id"],
            "x": min_x, "y": min_y,
            "w": max_x - min_x, "h": max_y - min_y,
            "name": stage["name"]
        })
    return regions
```

### 2.5 完整示例：RepoHunter flowchart

以下是 flowchart.html 全部 33 个节点的完整数据。

#### 输入：语义 JSON

```json
{
  "stages": [
    {"id": "s1", "name": "S1 · 需求理解与检索", "order": 1},
    {"id": "s2", "name": "S2 · 编译标准与粗筛", "order": 2},
    {"id": "s3", "name": "S3 · 深挖拆解", "order": 3},
    {"id": "s4", "name": "S4 · 辩论与裁决", "order": 4},
    {"id": "hm", "name": "搜索侧记忆", "order": 5},
    {"id": "ch", "name": "对话主循环", "order": 6},
    {"id": "cm", "name": "对话侧记忆", "order": 7}
  ],

  "nodes": [
    {"id": "A",     "role": "main",     "seq": 1,  "stage": "s1", "kind": "start", "title": "需求清单",           "sub": "keypoints + 语言过滤 入口"},
    {"id": "PIPE",  "role": "main",     "seq": 2,  "stage": "s1", "kind": "core",  "title": "Pipeline 总编排",     "sub": "串联五阶段 · 边跑边吐事件流"},
    {"id": "PF",    "role": "main",     "seq": 3,  "stage": "s2", "kind": "glass", "title": "预抓六样资料页",      "sub": "README 清洗 两层树 stars size"},
    {"id": "GATE",  "role": "main",     "seq": 4,  "stage": "s2", "kind": "glass", "title": "gate 粗筛",          "sub": "不带工具一次调用 只判方向"},
    {"id": "CLONE", "role": "main",     "seq": 5,  "stage": "s3", "kind": "glass", "title": "浅克隆到本地",        "sub": "depth 1 只取当前快照"},
    {"id": "EXP",   "role": "main",     "seq": 6,  "stage": "s3", "kind": "core",  "title": "工具循环深挖",        "sub": "读真源码摆事实 只探不判"},
    {"id": "AUD",   "role": "main",     "seq": 7,  "stage": "s3", "kind": "glass", "title": "锚点审计自愈",        "sub": "打回重修一次 仍坏删点"},
    {"id": "DIS",   "role": "main",     "seq": 8,  "stage": "s3", "kind": "out",   "title": "客观架构拆解",        "sub": "不含任何 keypoint"},
    {"id": "FAN",   "role": "main",     "seq": 9,  "stage": "s4", "kind": "core",  "title": "按 keypoint 扇出",    "sub": "每条一条独立链路 全并发"},
    {"id": "TRI",   "role": "main",     "seq": 10, "stage": "s4", "kind": "glass", "title": "分诊",               "sub": "中立一问 拆解够不够判这条"},
    {"id": "DEB",   "role": "main",     "seq": 11, "stage": "s4", "kind": "glass", "title": "正反辩论",            "sub": "同一份材料 各表一次态"},
    {"id": "ADJ",   "role": "main",     "seq": 12, "stage": "s4", "kind": "glass", "title": "裁决",               "sub": "干净上下文收口 hit / miss"},
    {"id": "SCR",   "role": "main",     "seq": 13, "stage": "s4", "kind": "glass", "title": "对齐与计数",          "sub": "按位置对齐 漏判补 miss"},
    {"id": "RES",   "role": "main",     "seq": 14, "stage": "s4", "kind": "out",   "title": "排序结果",            "sub": "命中数降序 平手比 stars"},
    {"id": "CHAT",  "role": "main",     "seq": 15, "stage": "ch", "kind": "core",  "title": "对话主循环",          "sub": "stream_chat 流式答话 事件流"},
    {"id": "CTX",   "role": "main",     "seq": 16, "stage": "ch", "kind": "glass", "title": "拼 system",           "sub": "人设 索引 注入仓库 召回段"},
    {"id": "RCL",   "role": "main",     "seq": 17, "stage": "cm", "kind": "glass", "title": "按需召回",            "sub": "小模型挑选器 三档输出"},

    {"id": "QU",    "role": "parallel", "stage": "s1", "kind": "glass", "title": "抽搜索查询",       "sub": "从需求清单抽项目身份概念"},
    {"id": "LG",    "role": "parallel", "stage": "s1", "kind": "glass", "title": "语言归一",         "sub": "别名 精确 模糊 三级纠错"},
    {"id": "SR",    "role": "parallel", "stage": "s1", "kind": "glass", "title": "并发搜 GitHub",     "sub": "剥引号合 OR 组 拼硬过滤"},
    {"id": "POOL",  "role": "parallel", "stage": "s1", "kind": "out",   "title": "候选仓库池",       "sub": "按 full_name 并集去重"},
    {"id": "MC",    "role": "parallel", "stage": "s1", "kind": "glass", "title": "MCP 连接封装",     "sub": "握手 重试 返回统一解析"},
    {"id": "KU",    "role": "parallel", "stage": "s2", "kind": "glass", "title": "编译判定标准",     "sub": "每条 keypoint 一把统一尺子"},

    {"id": "TL",    "role": "support",  "anchor": "EXP",  "stage": "s3", "kind": "glass", "title": "四个只读工具",   "sub": "列 读 搜 找 全对克隆操作"},
    {"id": "CLN",   "role": "support",  "anchor": "AUD",  "stage": "s3", "kind": "glass", "title": "滚动清理",       "sub": "到线归档旧返回 占位可重读"},
    {"id": "EVD",   "role": "support",  "anchor": "TRI",  "stage": "s4", "kind": "glass", "title": "按需取证",       "sub": "纯代码照清单抠符号周围"},
    {"id": "CPT",   "role": "support",  "anchor": "CTX",  "stage": "cm", "kind": "glass", "title": "会话压缩",       "sub": "笔记优先 摘要兜底 硬截保命"},

    {"id": "SKIP",  "role": "fallback", "anchor": "CLONE","stage": "s2", "kind": "warn",  "title": "跳过沉底",       "sub": "置灰不淘汰 拿不准一律放行"},
    {"id": "STOP",  "role": "fallback", "anchor": "EXP",  "stage": "s3", "kind": "warn",  "title": "逼停与熔断",     "sub": "预算烧光 / 同指纹三连撞"},
    {"id": "EXT",   "role": "fallback", "anchor": "CTX",  "stage": "cm", "kind": "glass", "title": "记忆提取",       "sub": "攒 12 条或说「记住」 单飞补跑"},

    {"id": "RUNS",  "role": "aside",    "anchor": "SCR",  "stage": "hm", "kind": "glass", "title": "搜索账本 runs",   "sub": "一次搜索一行 轻量增量"},
    {"id": "RM",    "role": "aside",    "anchor": "RES",  "stage": "hm", "kind": "glass", "title": "仓库账本 repo_memory", "sub": "一仓库一行 拆解分语言格"},
    {"id": "NOTE",  "role": "aside",    "anchor": "CHAT", "stage": "cm", "kind": "glass", "title": "会话笔记",       "sub": "攒 1500 tok 整份重写"}
  ],

  "edges": [
    {"from": "A",     "to": "PIPE",  "type": "flow"},
    {"from": "PIPE",  "to": "QU",    "type": "flow",     "label": "抽查询"},
    {"from": "PIPE",  "to": "KU",    "type": "flow",     "label": "并行编标准"},
    {"from": "PIPE",  "to": "MC",    "type": "flow",     "label": "开 MCP 连接"},
    {"from": "QU",    "to": "LG",    "type": "flow"},
    {"from": "LG",    "to": "SR",    "type": "flow"},
    {"from": "SR",    "to": "POOL",  "type": "flow"},
    {"from": "MC",    "to": "SR",    "type": "flow",     "from_side": "l", "to_side": "r", "label": "session"},
    {"from": "POOL",  "to": "PF",    "type": "flow",     "label": "候选池"},
    {"from": "KU",    "to": "PF",    "type": "flow",     "label": "判定标准"},
    {"from": "PF",    "to": "GATE",  "type": "flow"},
    {"from": "GATE",  "to": "SKIP",  "type": "flow",     "label": "明显不符"},
    {"from": "GATE",  "to": "CLONE", "type": "flow",     "label": "放行"},
    {"from": "CLONE", "to": "EXP",   "type": "flow"},
    {"from": "EXP",   "to": "TL",    "type": "flow",     "from_side": "l", "to_side": "r", "label": "每轮调用"},
    {"from": "TL",    "to": "CLN",   "type": "flow",     "label": "读得越多越要清"},
    {"from": "EXP",   "to": "STOP",  "type": "flow",     "from_side": "r", "to_side": "l", "label": "烧光 / 空转"},
    {"from": "EXP",   "to": "AUD",   "type": "flow",     "label": "最终 JSON"},
    {"from": "STOP",  "to": "AUD",   "type": "flow",     "label": "逼停出的 JSON 同过审计"},
    {"from": "AUD",   "to": "DIS",   "type": "flow"},
    {"from": "DIS",   "to": "FAN",   "type": "flow"},
    {"from": "FAN",   "to": "TRI",   "type": "flow",     "label": "每条 keypoint"},
    {"from": "TRI",   "to": "EVD",   "type": "flow",     "label": "不够 开清单"},
    {"from": "TRI",   "to": "DEB",   "type": "flow",     "label": "够判 直接辩"},
    {"from": "EVD",   "to": "DEB",   "type": "flow"},
    {"from": "DEB",   "to": "ADJ",   "type": "flow"},
    {"from": "ADJ",   "to": "SCR",   "type": "flow"},
    {"from": "SCR",   "to": "RES",   "type": "flow"},
    {"from": "SCR",   "to": "RUNS",  "type": "dash",     "from_side": "r", "to_side": "l", "label": "save_run 写回"},
    {"from": "RUNS",  "to": "RM",    "type": "flow",     "label": "upsert 追时间线"},
    {"from": "RM",    "to": "CHAT",  "type": "dash",     "label": "拆解读回对话侧"},
    {"from": "CHAT",  "to": "CPT",   "type": "flow",     "label": "答前压缩"},
    {"from": "CHAT",  "to": "CTX",   "type": "flow",     "label": "拼 system"},
    {"from": "CHAT",  "to": "EXT",   "type": "flow",     "label": "聊完后台提取"},
    {"from": "CHAT",  "to": "NOTE",  "type": "flow",     "label": "聊完续写笔记"},
    {"from": "CTX",   "to": "RCL",   "type": "flow",     "label": "先跑挑选器"},
    {"from": "RCL",   "to": "CTX",   "type": "feedback", "from_side": "r", "to_side": "r", "channel": 1380, "label": "命中补段"}
  ],

  "parallels": [
    {
      "fork": "PIPE",
      "join": "PF",
      "branches": [
        ["QU", "LG", "SR", "POOL"],
        ["MC"],
        ["KU"]
      ]
    }
  ]
}
```

#### 算法执行过程

以下逐步演示算法如何处理上述输入。

**步骤 1：主干排布**

```
seq  1: A     → kind=start, stage=s1
                y = START_Y = 70
                → (1180, 70)

seq  2: PIPE  → kind=core, stage=s1
                gap = 120 + 20(core) = 140, 但实际 A 是 start 特殊处理: gap=130
                y = 70 + 130 = 200
                → (1180, 200)

         ── 遇到并行块 (fork=PIPE, join=PF)，跳到步骤 2 ──

seq  3: PF    → kind=glass, stage=s2
                并行块结束于 y=730, 加 PARALLEL_EXIT=140
                y = 730 + 140 = 870
                → (1180, 870)

seq  4: GATE  → kind=glass, stage=s2 (同 stage)
                gap = 120
                y = 870 + 120 = 990
                → (1180, 990)

seq  5: CLONE → kind=glass, stage=s3 (跨 stage)
                gap = 120 + 20(stage) = 140
                y = 990 + 140 = 1130
                → (1180, 1130)

seq  6: EXP   → kind=core, stage=s3
                gap = 120 + 20(core) = 140
                y = 1130 + 140 = 1270
                → (1180, 1270)

seq  7: AUD   → kind=glass, stage=s3
                gap = 120 + 20(prev是core) = 140
                y = 1270 + 140 = 1410
                → (1180, 1410)

seq  8: DIS   → kind=out, stage=s3
                gap = 120
                y = 1410 + 120 = 1530
                → (1180, 1530)

seq  9: FAN   → kind=core, stage=s4 (跨 stage + core)
                gap = 120 + 20(core) = 140, 不与 stage 叠加
                y = 1530 + 140 = 1670
                实际值 1650, 偏差 20 ← 可微调常量
                → (1180, 1670)

seq 10: TRI   → kind=glass, stage=s4
                gap = 120 + 20(prev是core) = 140
                y = 1670 + 140 = 1810
                实际值 1790, 偏差 20 ← 累积自 FAN
                → (1180, 1810)

seq 11: DEB   → kind=glass, stage=s4
                gap = 120, 但 EVD 侧挂在 TRI-DEB 之间需要额外空间
                有侧挂节点在中间时: gap = 120 × 2 = 240
                y = 1810 + 240 = 2050
                实际值 2030, 偏差 20
                → (1180, 2050)

seq 12: ADJ   → gap=120, y = 2050 + 120 = 2170 (实际 2150, 偏差 20)
seq 13: SCR   → gap=120, y = 2170 + 120 = 2290 (实际 2270, 偏差 20)
seq 14: RES   → gap=120, y = 2290 + 120 = 2410 (实际 2390, 偏差 20)
seq 15: CHAT  → kind=core, stage=ch (跨 stage + core)
                gap = 120 + 20(core) + 20(stage) = 160
                y = 2410 + 160 = 2570 ← 偏差归零（stage 间距吸收了累积偏差的一部分）
                实际值 2570, 偏差 0 ✓
                → (1180, 2570)

seq 16: CTX   → gap = 120 + 20(prev是core) = 140, y = 2570 + 140 = 2710 (实际 2710) ✓
seq 17: RCL   → gap = 120, y = 2710 + 120 = 2830 (实际 2830) ✓
```

**步骤 2：并行分支**

```
fork=PIPE (y=200), 3 条分支, offsets = [-300, 0, +300]
branch_start_y = 200 + 170 = 370

分支 0 (x = 1180-300 = 880):
  QU   → (880, 370)   实际 (880, 370)  ✓
  LG   → (880, 490)   实际 (880, 490)  ✓
  SR   → (880, 610)   实际 (880, 610)  ✓
  POOL → (880, 730)   实际 (880, 730)  ✓

分支 1 (x = 1180+0 = 1180):
  MC   → (1180, 370)  实际 (1180, 610)  偏差 240
         注：原图中 MC 与 SR 对齐(y=610)是审美选择
         算法 V1 统一顶部对齐，可接受

分支 2 (x = 1180+300 = 1480):
  KU   → (1480, 370)  实际 (1480, 370)  ✓
```

**步骤 3：侧挂节点**

```
TL   (support, anchor=EXP)  → x=1180-360=820,  y=1270  实际(820, 1270)  ✓
CLN  (support, anchor=AUD)  → x=1180-360=820,  y=1410  实际(820, 1410)  ✓
EVD  (support, anchor=TRI)  → x=1180-360=820,  y=1810  实际(820, 1910)  偏差 100
      注：EVD 有出边(EVD→DEB)，需要额外 y 偏移，引擎应检测此情况
      规则补丁：侧挂节点若有出边连向主干，y = anchor.y + ROW_GAP
      修正后 → y = 1810+120 = 1930, 偏差 20 (可接受)
CPT  (support, anchor=CTX)  → x=1180-360=820,  y=2710  实际(820, 2710)  ✓

SKIP (fallback, anchor=CLONE)→ x=1180+360=1540, y=1130  实际(1560, 1130)  x偏差 20
STOP (fallback, anchor=EXP) → x=1180+360=1540, y=1270  实际(1540, 1270)  ✓
EXT  (fallback, anchor=CTX) → x=1180+360=1540, y=2710  实际(1540, 2710)  ✓

RUNS (aside, anchor=SCR)    → x=1180+480=1660, y=2290  实际(1660, 2270)  y偏差 20
RM   (aside, anchor=RES)    → x=1180+480=1660, y=2410  实际(1660, 2390)  y偏差 20
NOTE (aside, anchor=CHAT)   → x=1180+480=1660, y=2570  实际(1900, 2710)  偏差大
      注：NOTE 在原图中是第二车道(+720)且与 CTX 同行
      规则补丁：同侧已有 aside (EXT在+360, 算fallback不算aside)
      NOTE 需要 LANE_PUSH: x=1180+480+240=1900 ✓
      y 应跟 CTX(2710) 而非 CHAT(2570): anchor 改为 CTX 或引擎检测同行节点
```

#### 输出：坐标对比表

| 节点 | 角色 | 算法输出 (x, y) | 原始坐标 (x, y) | x 偏差 | y 偏差 | 说明 |
|------|------|-----------------|-----------------|--------|--------|------|
| A | main | (1180, 70) | (1180, 70) | 0 | 0 | ✓ |
| PIPE | main | (1180, 200) | (1180, 200) | 0 | 0 | ✓ |
| QU | parallel | (880, 370) | (880, 370) | 0 | 0 | ✓ |
| LG | parallel | (880, 490) | (880, 490) | 0 | 0 | ✓ |
| SR | parallel | (880, 610) | (880, 610) | 0 | 0 | ✓ |
| POOL | parallel | (880, 730) | (880, 730) | 0 | 0 | ✓ |
| MC | parallel | (1180, 370) | (1180, 610) | 0 | 240 | 原图居中对齐，算法顶部对齐 |
| KU | parallel | (1480, 370) | (1480, 370) | 0 | 0 | ✓ |
| PF | main | (1180, 870) | (1180, 870) | 0 | 0 | ✓ |
| GATE | main | (1180, 990) | (1180, 990) | 0 | 0 | ✓ |
| CLONE | main | (1180, 1130) | (1180, 1130) | 0 | 0 | ✓ |
| EXP | main | (1180, 1270) | (1180, 1270) | 0 | 0 | ✓ |
| TL | support | (820, 1270) | (820, 1270) | 0 | 0 | ✓ |
| STOP | fallback | (1540, 1270) | (1540, 1270) | 0 | 0 | ✓ |
| CLN | support | (820, 1410) | (820, 1410) | 0 | 0 | ✓ |
| AUD | main | (1180, 1410) | (1180, 1410) | 0 | 0 | ✓ |
| DIS | main | (1180, 1530) | (1180, 1530) | 0 | 0 | ✓ |
| SKIP | fallback | (1540, 1130) | (1560, 1130) | 20 | 0 | 微调 |
| FAN | main | (1180, 1670) | (1180, 1650) | 0 | 20 | 累积偏差 |
| TRI | main | (1180, 1810) | (1180, 1790) | 0 | 20 | 累积偏差 |
| EVD | support | (820, 1930) | (820, 1910) | 0 | 20 | 累积偏差 |
| DEB | main | (1180, 2050) | (1180, 2030) | 0 | 20 | 累积偏差 |
| ADJ | main | (1180, 2170) | (1180, 2150) | 0 | 20 | 累积偏差 |
| SCR | main | (1180, 2290) | (1180, 2270) | 0 | 20 | 累积偏差 |
| RES | main | (1180, 2410) | (1180, 2390) | 0 | 20 | 累积偏差 |
| RUNS | aside | (1660, 2290) | (1660, 2270) | 0 | 20 | 跟随 anchor 偏差 |
| RM | aside | (1660, 2410) | (1660, 2390) | 0 | 20 | 跟随 anchor 偏差 |
| CHAT | main | (1180, 2570) | (1180, 2570) | 0 | 0 | ✓ 偏差归零 |
| CPT | support | (820, 2710) | (820, 2710) | 0 | 0 | ✓ |
| CTX | main | (1180, 2710) | (1180, 2710) | 0 | 0 | ✓ |
| EXT | fallback | (1540, 2710) | (1540, 2710) | 0 | 0 | ✓ |
| NOTE | aside | (1900, 2710) | (1900, 2710) | 0 | 0 | ✓ 需 LANE_PUSH |
| RCL | main | (1180, 2830) | (1180, 2830) | 0 | 0 | ✓ |

**统计**：33 个节点中，22 个完全命中 (67%)，10 个偏差 ≤30px (30%)，1 个偏差 240px (MC 对齐策略不同)。

偏差 20px 的节点分两类：8 个源于 FAN 处的间距累积（通过微调 DIS→FAN 常量即可消除），SKIP 的 x 偏差 20px 是个别微调。MC 的 240px 偏差是对齐策略选择（顶部对齐 vs 内容对齐），不影响可读性。

### 2.6 泛化策略

**V1 只支持一种版式：流水线型。** 有明确主轴和阶段的项目——AI agent 框架、数据管线、编译器、ML pipeline、请求处理链——天然适合。这也是目标用户（vibe coding 创业者）最常见的项目结构。

**V2 增加版式选择**：

| 版式 | 适合的项目类型 | 主要变化 |
|------|---------------|---------|
| 流水线型 | agent 框架、数据处理、编译器 | V1 已实现 |
| 分层型 | 前端应用（路由→页面→组件→服务） | x 轴改为分层深度 |
| 中心辐射型 | 插件系统、微服务网关 | 核心在中间，扩展放周围 |

LLM 先判断项目属于哪种版式（三选一，选择题），再交给对应的布局函数。判断错了就换一个重跑——成本可忽略，因为布局是毫秒级的，只有 LLM 分析是分钟级的。

### 2.7 测试策略

```python
# tests/test_layout.py

def test_repohunter_spine():
    """主干 17 个节点的 x 必须全部 = 1180，y 偏差 < 30"""
    result = layout(load("tests/data/repohunter_semantic.json"))
    expected = load("tests/data/repohunter_expected.json")
    for nid in SPINE_IDS:
        assert result[nid]["x"] == 1180
        assert abs(result[nid]["y"] - expected[nid]["y"]) < 30

def test_repohunter_parallel():
    """并行分支的 x 必须在 [880, 1180, 1480] 中"""
    result = layout(load("tests/data/repohunter_semantic.json"))
    for nid in ["QU", "LG", "SR", "POOL"]:
        assert result[nid]["x"] == 880
    assert result["KU"]["x"] == 1480

def test_repohunter_side_nodes():
    """侧挂节点必须在 anchor 的对应侧"""
    result = layout(load("tests/data/repohunter_semantic.json"))
    assert result["TL"]["x"] < result["EXP"]["x"]    # support 在左
    assert result["STOP"]["x"] > result["EXP"]["x"]   # fallback 在右
    assert result["RUNS"]["x"] > result["STOP"]["x"]   # aside 比 fallback 更远

def test_no_overlap():
    """任意两个节点不能重叠"""
    result = layout(load("tests/data/repohunter_semantic.json"))
    boxes = [(v["x"], v["y"], 280, 88) for v in result.values()]
    for i, a in enumerate(boxes):
        for b in boxes[i+1:]:
            assert not rects_overlap(a, b)

def test_deterministic():
    """同一输入跑 100 次，结果必须完全相同"""
    data = load("tests/data/repohunter_semantic.json")
    first = layout(data)
    for _ in range(100):
        assert layout(data) == first


# ── 碰撞消解专项测试 ──

def test_main_nodes_never_move():
    """碰撞消解不能移动任何主干节点"""
    data = load("tests/data/crowded_case.json")   # 故意造的拥挤输入
    result = layout(data)
    for nid in main_node_ids(data):
        assert result[nid]["x"] == CENTER_X

def test_dense_side_nodes_no_overlap():
    """一个 anchor 挂 4 个 support，消解后不能重叠"""
    data = make_case(anchor="EXP", supports=4)
    result = layout(data)
    assert_no_overlap(result, index(data))

def test_parallel_branch_with_side_nodes():
    """并行分支的节点自己挂侧挂时，分支间距必须自动加宽"""
    data = make_case(branches=3, side_on_branch=1)
    result = layout(data)
    assert_no_overlap(result, index(data))
    # 分支间距应超过默认的 300
    assert abs(result["B0"]["x"] - result["B1"]["x"]) > PARALLEL_PITCH

def test_unresolvable_raises():
    """极端拥挤的输入必须报错，而不是凑合出丑图"""
    data = make_case(anchor="EXP", supports=12)
    with pytest.raises(LayoutError, match="无法收敛"):
        layout(data)

def test_collision_converges_fast():
    """正常输入不应该触发大量推动轮次"""
    data = load("tests/data/repohunter_semantic.json")
    result, stats = layout(data, return_stats=True)
    assert stats["push_rounds"] <= 3     # RepoHunter 这种正常结构应该 0-1 轮就收敛
```

**验证流程**：跑测试 → 看哪些断言失败 → 调整常量或补规则 → 重跑。一轮一秒，可以迭代几十次。

### 2.8 文件结构（建议）

```
repo-decomposer/
├── layout/
│   ├── __init__.py
│   ├── engine.py          # layout() 主函数
│   ├── spine.py           # 主干排布
│   ├── parallel.py        # 并行分支排布（含动态间距计算）
│   ├── side.py            # 侧挂节点排布
│   ├── collision.py       # 全局碰撞消解（步骤 3.5）
│   ├── routing.py         # 边路由
│   ├── regions.py         # 区域水印
│   ├── constants.py       # 所有设计常量
│   └── validate.py        # 输入校验
├── tests/
│   ├── data/
│   │   ├── repohunter_semantic.json   # 输入（本文档 2.5 节的 JSON）
│   │   └── repohunter_expected.json   # 标准答案（flowchart.html 的坐标）
│   └── test_layout.py
└── README.md
```

---

## 附录：关键决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| LLM 是否参与布局 | 否 | 空间推理差、非确定性、无法测试 |
| 布局算法是否用 Sugiyama | 借骨架，替换核心步骤 | Sugiyama 解 NP 难的排序问题，我们用语义标签查表绕过 |
| 坐标谁决定 | 设计常量（人定）+ 规则（代码算） | 审美参数人定，计算过程确定性 |
| 并行结构如何表达 | JSON 中显式声明 fork/join/branches | 比让引擎从边推断更可靠 |
| 并行分支间距 | 按分支实际宽度动态计算 | 固定 300 只在分支无侧挂时成立 |
| 重叠如何处理 | 摆完后全局检测 + 按角色优先级推开 | 局部修补，不像力导向那样全体震荡 |
| 推不开怎么办 | 报错，打回上游重新标注 | 不硬挤出丑图；能拒绝输入是相对 Mermaid 的优势 |
| 后端用什么 | FastAPI (Python) | 与 LLM 管线同语言，不引入 Node 后端 |
| 渲染器用什么 | 独立 TypeScript，不用 React | 保证导出为单文件 HTML 的能力 |
| 前端框架 | React（仅管理界面） | 渲染器外面的壳子，不涉及图本身 |
