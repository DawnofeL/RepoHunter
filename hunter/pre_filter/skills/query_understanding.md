---
name: query-understanding
description: 从用户写的需求清单抽出 GitHub 搜索查询
---

# 任务

输入是用户写的一条条需求。产出一样东西：搜索查询 queries，也就是拿去 GitHub 搜仓库的关键词串。需求清单本身是后面逐条核对用的，不归你管，你只负责从里面抽出能搜的关键词。

# 最高优先级铁律：严禁瞎编

只提取用户在需求里真正说出的概念，绝不引入用户没说的东西。queries 只能对用户说出的概念扩同义词、单复数、拼写变体，严禁把一个概念替换或扩展成另一个用户没提的概念。用户说 agentic，扩成 agent 可以（同一概念的不同写法），扩成 multi-agent 不行，那是用户没提的另一个概念。写完通篇扫一遍，凡是在用户需求里找不到出处的概念，一律删掉。

# queries 怎么抽

- **需求里可能混着多种语言，每种语言承载的概念都要抓全。** 不准只盯英文词，中文、日文、法文、西班牙文等任何语言表达的概念都算。比如「多agent」里「多」是中文的 multi、「agent」是英文，合起来是 multi-agent，绝不能因为「多」是中文就只抓 agent。先把所有语言的概念识别全，再统一翻成英文。

- **抓全概念后，关键词必须全部是英文。** GitHub 上 repo 名称、描述、topic 几乎全是英文，非英文词搜不到。

- 一个概念有多种英文写法时全部进 OR，但只扩**同一概念**的单复数、缩写、拼写变体，不许扩成别的概念。

- **同义词和缩写用括号 OR 合进一条**，严禁一个词一条。

- **严禁纯简写。** OR 组里除了简写，必须至少放一个最关键的全称写法。

- **queries 只放项目的核心身份概念（项目「是什么」、属于哪个领域），用户那些具体功能要求一律不进 queries。** GitHub 仓库搜索里空格分隔的概念组是 AND 关系，每多 AND 一组就把召回窄一截；把一串功能要求都 AND 进一条 query，会严到搜出 0 个。比如需求是 agentic RAG 项目加 reranker、bm25、dense vector、evaluation，query 到 `(agentic OR agent) (RAG OR retrieval-augmented-generation)` 为止，后面那些功能要求一概不进。

- **OR 项严禁加引号，带空格的写法也直接写、不要用引号裹起来。** 比如「多智能体」这个概念，写成 `(multi-agent OR multi agent OR multiagent)`，绝不能写成 `(multi-agent OR "multi agent" OR "multi-agents")`。引号短语和别的项 OR 在一起，GitHub 会静默返回 0 个结果。

# 输出格式

只输出一个 JSON，框架如下，不要别的话。

```json
{
  "queries": [
    { "q": "查询串" }
  ]
}
```

# 例子

> 用户的需求清单：
> - 用 LoRA 微调扩散模型
> - 能生成图像
> - 轻量

身份概念是扩散模型、LoRA 微调；「能生成图像」「轻量」是功能和体量要求，不进 query。

```json
{
  "queries": [
    { "q": "(diffusion OR stable-diffusion OR LoRA OR text-to-image)" }
  ]
}
```



---



> 用户的需求清单：
> - 实时目标检测
> - 能跑在边缘设备上
> - 必须是 YOLO 系

身份概念只有目标检测（object detection）和 YOLO。

**反例（瞎编，错误示范）：**

```json
{
  "queries": [
    { "q": "(object-detection OR detection OR OCR OR image-captioning OR YOLO)" }
  ]
}
```

**错在哪：**queries 里塞了 `OCR`、`image-captioning` 这两个用户没提的别的视觉任务，一个文字识别、一个图像描述，都跟目标检测不沾边，属于瞎编。

**正例：**

```json
{
  "queries": [
    { "q": "(object-detection OR detection OR YOLO OR You Only Look Once)" }
  ]
}
```

目标检测只扩成同概念的 object-detection、detection，不引入别的任务；YOLO 是简写，补一个全称 You Only Look Once。「实时」「边缘设备」是功能要求，不进 query。



---



> 用户的需求清单：
> - agentic RAG，本地可跑
> - 包含 reranker
> - bm25 和 dense vector 双路召回
> - 有 evaluation

身份概念只有 agentic RAG。reranker、bm25、dense vector、evaluation、本地可跑都是功能要求，不进 query。

**反例（过度填写，错误示范）：**

```json
{
  "queries": [
    { "q": "(agentic OR agent) (RAG OR retrieval-augmented-generation) (reranker OR rerank) BM25 (dense vector OR dense retrieval) (evaluation OR eval OR benchmark)" }
  ]
}
```

**错在哪：**把 reranker、BM25、dense vector、evaluation 这些功能要求全 AND 进了一条 query。GitHub 仓库搜索里这几组是 AND 关系，要求一个仓库的名字、描述、topics、README 里同时出现这六样，现实里几乎没有，会搜出 0 个。

**正例：**

```json
{
  "queries": [
    { "q": "(agentic OR agent) (RAG OR retrieval-augmented-generation)" }
  ]
}
```

query 只留核心身份概念 agentic RAG，后面那些功能要求一概不进。
