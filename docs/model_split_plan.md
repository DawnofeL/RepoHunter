# 分阶段选模型 设计与实现 plan

给每个用模型的阶段单独选模型，默认跟凭证页那一个模型；能把简单阶段换成便宜快的 flash 省钱，核心阶段保持 pro。加一个设置界面可视化选，存进 creds.json，下次进带出来预填。当前代码是未开工状态（之前的尝试已全部回滚），MODELS 还是 query_understanding / keypoint_understanding / content_filter 三键。

## 关键约束：前缀缓存决定了怎么分

DeepSeek 的前缀缓存按「模型 + 前缀」分池，换个模型，同样的前缀也不复用、缓存全废。

搜索侧的 gate、explorer 拆解、debate 辩论，共享同一个仓库的资料页 system（README + 目录树 + stars/size），README 那几千字四次调用只真算一次钱。这三个必须用同一个模型，拆开用不同模型这个缓存就跨不过模型边界、每个模型各算一次，搜十个仓库费用不降反升。所以它们绑成一块，不拆。

对话侧的 chat 回答、recall 召回、extract 提取，system 各不相同（llm_skill / recall_memories / extract_memories），本来就不共享前缀缓存，可以自由分开选模型。召回、提取单独用 flash 不会拖累对话回答的缓存。

## 六块划分

```
① query_understanding   需求理解           独立
② keypoint_understanding 关键点编译         独立
③ deep_dive             搜索深挖            gate + explorer + debate 绑定，共享资料页缓存
④ chat                  对话回答            独立
⑤ recall                召回挑选            独立
⑥ extract               记忆提取            独立
```

比 8 全拆少 2 块，就是把 gate / explorer / debate 合成 deep_dive 一档，拆了没用还废缓存。

## 默认填法

- pro（deepseek-v4-pro）：③ deep_dive、④ chat
- flash（deepseek-v4-flash）：① QU、② KU、⑤ recall、⑥ extract
- 界面默认逻辑：各阶段留空就回退凭证页模型；上面这份当作预存好的初始配置

## 后端改动

**1. hunter/config.py 的 MODELS**，三键改成六键，删 content_filter：

```python
MODELS = {
    "query_understanding":    "deepseek-v4-pro",
    "keypoint_understanding": "deepseek-v4-pro",
    "deep_dive":              "deepseek-v4-pro",   # gate + explorer + 辩论，共享资料页缓存，必须同模型
    "chat":                   "deepseek-v4-pro",
    "recall":                 "deepseek-v4-pro",
    "extract":                "deepseek-v4-pro",
}
```

**2. 各处取模型的键改掉**（都是 `MODELS["content_filter"]` 换成对应键）：

- `hunter/repo_detection/explorer.py`（3 处 `model or MODELS["content_filter"]`）→ deep_dive
- `hunter/repo_detection/debate.py`（2 处）→ deep_dive
- `hunter/repo_detection/content_filter.py`（1 处 `config.MODELS["content_filter"]`）→ deep_dive
- `hunter/chat/session.py` 三处分家：
  - build_segments 那处（召回用的 model 参数）→ recall
  - call_deepseek stream 那处（对话回答）→ chat
  - spawn_extraction 那处（提取）→ extract

不要动的同名字符串（这些不是模型键，是 skill 文件名和 cost 阶段标签）：`pipeline.py` 的 `load_skill("content_filter")`、`explorer.py` 的 `track("content_filter", resp)`、`cost.py` docstring 里的举例。

**3. hunter/config.py 的 configure**，不用改，它已经支持 models dict（`for stage, name in models.items(): if name and stage in MODELS`），传六键进去、空值的阶段自动跳过保持默认。

**4. webapp/backend/server.py**，前端发完整 models dict，后端统一注入：

- RunRequest：删 qu_model / content_model，加 `models: dict = {}`
- ChatRequest：删 model，加 `models: dict = {}`
- CredsRequest：加 `models: dict = {}`
- `/run`：`config.configure(deepseek_api_key=..., github_pat=..., models=req.models)`
- `/chat`：`config.configure(deepseek_api_key=..., models=req.models)`
- `/creds`：`save_creds(req.deepseek_api_key, req.github_pat, req.model, req.models)`

**5. hunter/creds_store.py**，存读加 models：

- load_creds 的 blank 和返回都加 `"models": d.get("models", {})`
- save_creds 加参数 `models: dict | None = None`，写文件时带上 `"models": models or {}`，全空判断也加上 models

## 前端改动（webapp/frontend）

**1. 设置界面**：六个阶段各一个模型输入框（或下拉）。入口可以放凭证页，或对话齿轮扩展成完整设置面板。每个框留空表示用凭证页模型。

**2. 状态**：`state.creds` 加一个 `models` 对象（六键）。凭证页那个单模型 `state.creds.model` 保留，作为各阶段留空时的默认。

**3. 默认回退**：发送 /run、/chat 时组装 body.models，每个阶段 `= 用户设的 || state.creds.model`，这样留空的阶段自动用凭证页模型，后端收到的都是具体模型名。

**4. 帮填初始配置**：设置界面提供一个预设，或首次进就填好 deep_dive + chat = deepseek-v4-pro，QU + KU + recall + extract = deepseek-v4-flash。

**5. 持久化**：saveCreds 带上 models 发 /creds 存 creds.json；进入时 /creds 读回来预填设置界面。

**6. 发送**：/run 的 body 带完整 models（六键），/chat 的 body 带完整 models（六键，对话侧只用到 chat/recall/extract，多发无妨）。当前 /run 发的是 qu_model / content_model，/chat 发的是 model，都改成发 models。

## 数据流

```
设置界面 → state.creds.models（六键）
  → /run·/chat 的 body.models
  → server config.configure(models=...)
  → 覆盖 hunter.config.MODELS
  → 各阶段 call_deepseek 取自己那一键
```

## flash 的确切模型名

deepseek-v4-flash（跟 pro 同一套第三方命名，已确认）。

## 实现顺序

1. 后端：config 六键 → 各处引用改键 → server 请求和注入 → creds_store 持久化。改完 `python -c "import hunter.config; from webapp.backend import server"` 验证导入。
2. 前端：设置界面 → 状态和默认回退 → 持久化 → 发送。
3. 验证：真机确认每个阶段真取到对应模型；重点确认 deep_dive 的三个阶段（gate/explorer/debate）用的是同一个模型（缓存不破）；退出重进设置项还在。

## 迁移到新 session 的第一步

直接读这份 plan，从「后端改动」第 1 步 config.py 开始做。当前代码是干净的三键状态，没有半成品残留。
