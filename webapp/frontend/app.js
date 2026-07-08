(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // 本次会话状态：凭证 + 运行时各阶段数据，刷新即清，不落盘
  const state = {
    creds: { apikey: "", pat: "", model: "", models: {} },
    params: null,
  };
  const run = {};

  // 六个用模型的阶段：后端 MODELS 的键 → 凭证页对应输入框 id
  const STAGE_INPUTS = [
    ["query_understanding", "in-m-qu"],
    ["keypoint_understanding", "in-m-kp"],
    ["content_filter", "in-m-cf"],
    ["chat", "in-m-chat"],
    ["recall", "in-m-recall"],
    ["extract", "in-m-extract"],
  ];

  // 从凭证页六个框收集分阶段模型，只留填了值的，空的不进去（让后端回退默认）
  function collectStageModels() {
    const m = {};
    STAGE_INPUTS.forEach(([key, id]) => {
      const v = ($(id)?.value || "").trim();
      if (v) m[key] = v;
    });
    return m;
  }

  // 组装发给后端的完整六键 models：每键优先用分阶段设的，留空回退凭证页全局模型；
  // overrides 给对话齿轮临时覆盖某几档用
  function buildModels(overrides) {
    const base = state.creds.model || "";
    const cfg = state.creds.models || {};
    const out = {};
    STAGE_INPUTS.forEach(([key]) => { out[key] = cfg[key] || base; });
    if (overrides) Object.assign(out, overrides);
    return out;
  }

  // 右栏对话：注入的 repo 全名（跟 ws-ctx 芯片同步）和多轮对话历史，刷新即清
  const wsContext = [];
  const wsMessages = [];
  // 本次对话的 session id，第一次发消息时生成，同一次对话里不变，存对话时用它 upsert
  let wsSessionId = null;
  // 一次只允许一条在途请求，发送中禁掉发送钮，避免叠流
  let wsSending = false;
  // 最近一轮发给模型的完整提示词（分段 + 消息），点 agent 头像的提示词监控用；每轮 prompt 事件覆盖
  let wsLastPrompt = null;
  // 齿轮里单独设的对话 api/model，只覆盖对话；留空则回退凭证页的 state.creds
  const chatOverride = { apikey: "", model: "" };

  // 左栏当前在哪个 tab（repo 结果三态 / chat 会话列表），以及切回 repo 时该还原到哪个子态
  let leftTab = "repo";
  let lastRepoPane = "search";
  // 最近一次加载的会话列表，切换语言或重渲染时用，不用每次都重新 fetch
  let lastSessions = [];

  // 当前视图名和历史记录数，一起决定 HUD 历史按钮显不显示
  let currentView = "setup";
  let historyCount = 0;
  let memoryCount = 0;
  // popstate（含鼠标侧键前进后退）触发的视图还原期间不再往浏览器历史里压新记录
  let suppressPush = false;

  // ── i18n：界面文案中英切换 ────────────────────
  // 静态文案挂 data-i18n（刷 textContent）或 data-i18n-ph（刷 placeholder），按 key 查这张表
  const I18N = {
    zh: {
      "hud.sub": "GitHub 仓库发现 · 深度排序",
      "hud.history": "历史",
      "hud.memory": "项目记忆",
      "hud.vault": "记忆库",
      "hud.tosearch": "搜索",
      "vault.runs": "搜索记录",
      "vault.hall": "仓库记忆",
      "vault.back": "返回大厅",
      "vault.jump": "查看拆解",
      "vault.timeline": "被搜到的记录",
      "vault.runGone": "那次搜索记录已删除，看不了轨迹了",
      "vault.noTrace": "这次没有留下轨迹",
      "setup.step": "第 01 步 · 凭证",
      "setup.title": "访问凭证",
      "setup.apikey": "DeepSeek API 密钥",
      "setup.model": "模型",
      "setup.stages": "分阶段模型（可选）",
      "setup.stage.ph": "留空用上面的模型",
      "setup.stage.qu": "需求理解",
      "setup.stage.kp": "关键点编译",
      "setup.stage.cf": "搜索深挖",
      "setup.stage.chat": "对话回答",
      "setup.stage.recall": "记忆召回",
      "setup.stage.extract": "记忆提取",
      "setup.pat": "GitHub PAT",
      "setup.getpat": "去申请",
      "setup.pathint": "搜索 GitHub 仓库需要它。",
      "setup.enter": "进入",
      "form.step": "第 02 步 · 需求",
      "form.back": "返回凭证",
      "form.langs": "仓库主要语言",
      "form.langs.sub": "最多 3 个",
      "form.addlang": "+ 添加语言",
      "form.keypoints": "需求清单",
      "form.keypoints.sub": "一条一个",
      "form.addkp": "+ 添加需求",
      "form.kprow.ph": "例如 必须是多 agent",
      "form.kprow.del": "移除",
      "form.kp.tip": "每条只写一个需求，分开写筛选更准。例如：",
      "form.kp.ex": "LLM RL（纯文本 LLM）\n自带数据集\n本地可跑的中小型项目，不要大型企业级\n有完整的 eval 流程",
      "err.keypoints": "至少写一条需求",
      "form.recall": "召回数",
      "form.recall.sub": "top_k",
      "form.usemem": "启用项目记忆",
      "form.usemem.sub": "复用分析过的仓库，跳过重复拆解",
      "form.search": "搜索",
      "run.stage.qu": "QU",
      "run.stage.search": "检索",
      "run.stage.gate": "Coarse Filter",
      "run.stage.content": "Content Filter",
      "run.stage.judge": "判分",
      "run.stage.debate": "Debate & Adjudicate",
      "run.content.title": "Content Filter",
      "run.trace.head": "探查日志",
      "ctable.repo": "仓库",
      "ctable.status": "状态",
      "ctable.round": "轮次",
      "ctable.tools": "工具",
      "ctable.tokens": "Tokens",
      "ctable.hits": "命中",
      "ctable.skipped": "已跳过",
      "ctable.judging": "判定中",
      "results.step": "第 03 步 · 排序",
      "results.title": "排序结果",
      "results.restart": "新搜索",
      "hist.step": "历史",
      "hist.title": "查询历史",
      "hist.sub": "过往查询存在本地，重启后仍在，只有你删除时才会移除。",
      "hist.clear": "清空",
      "hist.back": "返回",
      "hd.step": "历史 · 结果",
      "hd.title": "排序结果",
      "hd.back": "返回历史",
      "mem.step": "项目记忆",
      "mem.title": "项目记忆",
      "mem.sub": "分析过的仓库会跨搜索记住，同一个仓库不会重复深挖第二次。",
      "mem.clear": "清空",
      "mem.back": "返回",
      "mem.none": "暂无记忆",
      "mem.tag.skipped": "已跳过",
      "mem.tag.dissected": "已拆解",
      "mem.lastseen": "最后搜到：",
      "mem.seenq": "被这些需求搜到过",
      "md.step": "项目记忆 · 详情",
      "md.title": "记住的仓库",
      "md.back": "返回记忆",
      "confirm.delmem.title": "删除记忆",
      "confirm.delmem.msg.pre": "把 ",
      "confirm.delmem.msg.post": " 的记忆删掉？此操作无法撤销。",
      "confirm.clearmem.title": "清空全部记忆",
      "confirm.clearmem.msg": "清空所有仓库记忆？此操作无法撤销。",
      "confirm.clearmem.ok": "清空",
      "modal.dontask": "不再提示",
      "modal.cancel": "取消",
      "err.creds": "API Key 和 GitHub PAT 都得填",
      "err.prefix": "出错：",
      "err.unknown": "未知错误",
      "form.langrow.ph": "例如 Python / TypeScript",
      "form.langrow.del": "移除",
      "run.content.deepdive": "Content Filter ",
      "results.sub.pre": "Content Filter了 ",
      "results.sub.post": " 个仓库，按 keypoint 命中排序",
      "rcard.techstack": "技术栈",
      "rcard.keydesigns": "关键设计",
      "rcard.architecture": "架构",
      "rcard.readfiles": "Content Filter读了",
      "rcard.keypoints": "Keypoint 命中",
      "rcard.skipped": "已跳过",
      "rcard.skipreason": "跳过原因",
      "rcard.advocate": "正方",
      "rcard.skeptic": "反方",
      "rcard.verdict": "裁决",
      "rcard.unverified": "锚点未证实",
      "rcard.del": "从这条历史中移除",
      "cost.cap": "Token 与缓存",
      "cost.stage": "阶段",
      "cost.calls": "调用",
      "cost.input": "输入",
      "cost.hit": "命中",
      "cost.miss": "未命中",
      "cost.output": "输出",
      "cost.hitrate": "命中率",
      "cost.total": "合计",
      "hist.noresults": "无结果",
      "hist.empty": "（空）",
      "hist.repos": "个仓库",
      "hist.tracetag": "日志",
      "hist.top": "榜首：",
      "hist.none": "暂无查询",
      "proc.title": "搜索过程",
      "proc.del": "删除过程",
      "proc.meta.trace": "探查",
      "proc.meta.lines": "行",
      "proc.sec.queries": "查询词",
      "proc.sec.keypoint": "Keypoint",
      "proc.sec.trace": "探查日志",
      "confirm.default.title": "确认",
      "confirm.default.ok": "确定",
      "confirm.delete": "删除",
      "confirm.remove": "移除",
      "confirm.delquery.title": "删除查询",
      "confirm.delquery.msg": "删除这条查询的全部结果？此操作无法撤销。",
      "confirm.clear.title": "清空全部历史",
      "confirm.clear.msg": "清空所有查询历史？此操作无法撤销。",
      "confirm.clear.ok": "清空",
      "confirm.delproc.title": "删除过程",
      "confirm.delproc.msg": "删除这条记录的搜索过程（QU、探查日志）？排序结果会保留。",
      "confirm.remrepo.title": "移除仓库",
      "confirm.remrepo.msg.pre": "把 ",
      "confirm.remrepo.msg.post": " 从这条历史中移除？",
      "ws.tab.repo": "结果",
      "ws.tab.chat": "会话",
      "ws.tab.mem": "记忆",
      "ws.mem.search": "搜索仓库记忆…",
      "ws.mem.nomatch": "没匹配到",
      "ws.newchat": "+ 新对话",
      "sess.empty": "还没有会话，点上面开始新对话",
      "sess.ctx.pin": "置顶",
      "sess.ctx.unpin": "取消置顶",
      "sess.ctx.rename": "重命名",
      "sess.ctx.del": "删除",
      "confirm.delsess.title": "删除会话",
      "confirm.delsess.msg": "删除这个会话？此操作无法撤销。",
      "ws.ctx.empty": "上下文为空，从左侧 + 号注入 repo",
      "ws.chat.empty.title": "和 AI 聊聊这些仓库",
      "ws.chat.empty.sub": "从左侧 + 号把 repo 注入上下文，或直接提问",
      "ws.gear.title": "对话模型设置",
      "ws.cfg.title": "对话模型设置",
      "ws.cfg.key.lb": "API 密钥",
      "ws.cfg.key.ph": "留空用凭证页的密钥",
      "ws.cfg.model.lb": "模型",
      "ws.cfg.model.ph": "留空用凭证页的模型",
      "ws.cfg.hint": "只作用于对话，左栏搜索仍用凭证页那套",
      "ws.cfg.ok": "确认",
    },
    en: {
      "hud.sub": "GitHub Repo Discovery · Deep Ranking",
      "hud.history": "History",
      "hud.memory": "Repo Memory",
      "hud.vault": "Memory",
      "hud.tosearch": "Search",
      "vault.runs": "Search Records",
      "vault.hall": "Repo Memory",
      "vault.back": "Back to Hall",
      "vault.jump": "View",
      "vault.timeline": "Search Timeline",
      "vault.runGone": "That search record was deleted, trajectory unavailable",
      "vault.noTrace": "No trajectory recorded for this run",
      "setup.step": "Step 01 · Credentials",
      "setup.title": "Access Credentials",
      "setup.apikey": "DeepSeek API Key",
      "setup.model": "Model",
      "setup.stages": "Per-stage models (optional)",
      "setup.stage.ph": "Leave blank to use the model above",
      "setup.stage.qu": "Query Understanding",
      "setup.stage.kp": "Keypoint Compile",
      "setup.stage.cf": "Deep Dive",
      "setup.stage.chat": "Chat",
      "setup.stage.recall": "Recall",
      "setup.stage.extract": "Extract",
      "setup.pat": "GitHub PAT",
      "setup.getpat": "Get one",
      "setup.pathint": "Needed for searching Github Repos.",
      "setup.enter": "Enter",
      "form.step": "Step 02 · Query",
      "form.back": "Back to Credentials",
      "form.langs": "Main Languages of Repo",
      "form.langs.sub": "up to 3",
      "form.addlang": "+ Add language",
      "form.keypoints": "Requirements",
      "form.keypoints.sub": "one per line",
      "form.addkp": "+ Add requirement",
      "form.kprow.ph": "e.g. must be multi-agent",
      "form.kprow.del": "Remove",
      "form.kp.tip": "Write one requirement per line; separating them filters more accurately. e.g.:",
      "form.kp.ex": "LLM RL (text-only LLM)\nships with its own dataset\nsmall-to-mid project that runs locally, not enterprise-scale\nhas a complete eval pipeline",
      "err.keypoints": "Add at least one requirement",
      "form.recall": "Recall",
      "form.recall.sub": "top_k",
      "form.usemem": "Use Repo Memory",
      "form.usemem.sub": "reuse analysed repos, skip re-dissecting",
      "form.search": "Search",
      "run.stage.qu": "QU",
      "run.stage.search": "Search",
      "run.stage.gate": "Coarse Filter",
      "run.stage.content": "Deep Dive",
      "run.stage.judge": "Judge",
      "run.stage.debate": "Debate & Adjudicate",
      "run.content.title": "Deep Dive",
      "run.trace.head": "EXPLORE TRACE",
      "ctable.repo": "Repo",
      "ctable.status": "Status",
      "ctable.round": "Round",
      "ctable.tools": "Tools",
      "ctable.tokens": "Tokens",
      "ctable.hits": "Hits",
      "ctable.skipped": "Skipped",
      "ctable.judging": "Judging",
      "results.step": "Step 03 · Ranked",
      "results.title": "Ranked Results",
      "results.restart": "New Search",
      "hist.step": "History",
      "hist.title": "Query History",
      "hist.sub": "Past queries are stored locally, kept across restarts, removed only when you delete them.",
      "hist.clear": "Clear All",
      "hist.back": "Back",
      "hd.step": "History · Result",
      "hd.title": "Ranked Results",
      "hd.back": "Back to History",
      "mem.step": "Repo Memory",
      "mem.title": "Repo Memory",
      "mem.sub": "Analysed repos are remembered across searches, so the same repo is not dug through twice.",
      "mem.clear": "Clear All",
      "mem.back": "Back",
      "mem.none": "No memory yet",
      "mem.tag.skipped": "skipped",
      "mem.tag.dissected": "dissected",
      "mem.lastseen": "Last seen: ",
      "mem.seenq": "Searched by these requirements",
      "md.step": "Repo Memory · Detail",
      "md.title": "Remembered Repo",
      "md.back": "Back to Memory",
      "confirm.delmem.title": "Delete Memory",
      "confirm.delmem.msg.pre": "Delete the memory of ",
      "confirm.delmem.msg.post": "? This cannot be undone.",
      "confirm.clearmem.title": "Clear All Memory",
      "confirm.clearmem.msg": "Clear all repo memory? This cannot be undone.",
      "confirm.clearmem.ok": "Clear All",
      "modal.dontask": "Don't ask again",
      "modal.cancel": "Cancel",
      "err.creds": "Both fields required: API Key and GitHub PAT",
      "err.prefix": "Error: ",
      "err.unknown": "Unknown error",
      "form.langrow.ph": "e.g. Python / TypeScript",
      "form.langrow.del": "Remove",
      "run.content.deepdive": "Deep dive ",
      "results.sub.pre": "Explored ",
      "results.sub.post": " repos, ranked by keypoint hits",
      "rcard.techstack": "Tech Stack",
      "rcard.keydesigns": "Key Designs",
      "rcard.architecture": "Architecture",
      "rcard.readfiles": "Files Read",
      "rcard.keypoints": "Keypoint hits",
      "rcard.skipped": "Skipped",
      "rcard.skipreason": "Skip reason",
      "rcard.advocate": "Pro",
      "rcard.skeptic": "Con",
      "rcard.verdict": "Verdict",
      "rcard.unverified": "unverified",
      "rcard.del": "Remove from this history",
      "cost.cap": "Token & Cache",
      "cost.stage": "Stage",
      "cost.calls": "Calls",
      "cost.input": "Input",
      "cost.hit": "Hit",
      "cost.miss": "Miss",
      "cost.output": "Output",
      "cost.hitrate": "Hit%",
      "cost.total": "Total",
      "hist.noresults": "no results",
      "hist.empty": "(empty)",
      "hist.repos": "repos",
      "hist.tracetag": "trace",
      "hist.top": "Top: ",
      "hist.none": "No queries yet",
      "proc.title": "Search Process",
      "proc.del": "Delete process",
      "proc.meta.trace": "trace",
      "proc.meta.lines": "lines",
      "proc.sec.queries": "Queries",
      "proc.sec.keypoint": "Keypoint",
      "proc.sec.trace": "Trace",
      "confirm.default.title": "Confirm",
      "confirm.default.ok": "OK",
      "confirm.delete": "Delete",
      "confirm.remove": "Remove",
      "confirm.delquery.title": "Delete query",
      "confirm.delquery.msg": "Delete all results of this query? This can't be undone.",
      "confirm.clear.title": "Clear all history",
      "confirm.clear.msg": "Clear all query history? This can't be undone.",
      "confirm.clear.ok": "Clear",
      "confirm.delproc.title": "Delete process",
      "confirm.delproc.msg": "Delete the search process (QU, trace) for this entry? The ranked results stay.",
      "confirm.remrepo.title": "Remove repo",
      "confirm.remrepo.msg.pre": "Remove ",
      "confirm.remrepo.msg.post": " from this history?",
      "ws.tab.repo": "Results",
      "ws.tab.chat": "Chats",
      "ws.tab.mem": "Memory",
      "ws.mem.search": "Search repo memory…",
      "ws.mem.nomatch": "No match",
      "ws.newchat": "+ New Chat",
      "sess.empty": "No chats yet — start one above",
      "sess.ctx.pin": "Pin",
      "sess.ctx.unpin": "Unpin",
      "sess.ctx.rename": "Rename",
      "sess.ctx.del": "Delete",
      "confirm.delsess.title": "Delete Chat",
      "confirm.delsess.msg": "Delete this chat? This can't be undone.",
      "ws.ctx.empty": "Context is empty — inject a repo from the + button on the left",
      "ws.chat.empty.title": "Chat with AI about these repos",
      "ws.chat.empty.sub": "Inject a repo from the + button on the left, or just ask",
      "ws.gear.title": "Chat model settings",
      "ws.cfg.title": "Chat Model Settings",
      "ws.cfg.key.lb": "API Key",
      "ws.cfg.key.ph": "Leave blank to use the credentials page key",
      "ws.cfg.model.lb": "Model",
      "ws.cfg.model.ph": "Leave blank to use the credentials page model",
      "ws.cfg.hint": "Only affects chat — search on the left still uses the credentials page",
      "ws.cfg.ok": "Confirm",
    },
  };
  let lang = localStorage.getItem("lang") || "zh";
  const t = (k) => (I18N[lang] && I18N[lang][k]) != null ? I18N[lang][k] : k;

  // 刷一遍所有挂 key 的静态文案，切换语言和首次加载都调
  function applyLang() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
      el.setAttribute("placeholder", t(el.getAttribute("data-i18n-ph")));
    });
    document.querySelectorAll("[data-i18n-title]").forEach((el) => {
      el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
    });
    document.querySelectorAll("#lang-toggle .lang-opt").forEach((b) => {
      b.classList.toggle("active", b.dataset.lang === lang);
    });
    fillKpTip();
    // 动态视图的内容是 JS 现拼的，静态 walk 刷不到，按当前视图用内存数据重渲染一遍
    if (currentView === "results") {
      renderResults();
      if (run.costTable) renderCost($("cost-area"), run.costTable);
      if (run.contentTotal != null) {
        $("results-sub").textContent = t("results.sub.pre") + run.contentTotal + t("results.sub.post");
      }
    } else if (currentView === "vault") {
      renderVaultRuns();
      renderVaultHall();
      // 右栏正看着某个仓库详情就原地重渲染（标题、时间线文案跟着切语言），否则重置大厅标题
      if (!$("vault-detail").hidden && vaultRepoEntry) vaultRenderRepoDetail(vaultRepoEntry);
      else $("vault-right-title").textContent = t("vault.hall");
    } else if (currentView === "workspace" && leftTab === "chat") {
      renderSessions();
    } else if (currentView === "workspace" && leftTab === "mem") {
      renderWsMemory();
    }
    // 右栏对话的空态占位是 JS 拼的，跟 currentView/leftTab 无关，工作台里只要还空着就刷一遍
    if (currentView === "workspace") {
      if (!wsMessages.length) $("ws-chat").innerHTML = wsChatEmptyHTML();
      if (!wsContext.length) {
        $("ws-ctx").innerHTML = '<span class="ws-ctx-empty">' + esc(t("ws.ctx.empty")) + "</span>";
      }
    }
  }

  // 点切换钮：改 lang、记本地、刷文案
  function setLang(l) {
    lang = l;
    localStorage.setItem("lang", l);
    applyLang();
  }

  // ── 小工具 ────────────────────────────────────
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
    );
  }
  function fmtStars(n) {
    n = n || 0;
    return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
  }
  function toMB(kb) {
    return kb ? (kb / 1024).toFixed(1) : "0";
  }

  // architecture 兜底排版：模型给了序号就规整空行，没序号有换行就自动补序号，纯一段不动
  // 序号只认「行首/空白 + 数字 + . + 空格」，且点后不能再跟数字，避开 3.12 React 19 这类误判
  function normalizeArchitecture(text) {
    text = String(text || "").trim();
    const numbered = /(^|\s)\d+\.\s/.test(text);
    if (numbered) {
      // 每个序号前插空行再拆，把模型给的零散换行压成统一的空一行
      const marked = text.replace(/(^|\s)(\d+\.\s)/g, "\n\n$2");
      const parts = marked.split(/\n\n+/).map((s) => s.trim()).filter(Boolean);
      return parts.join("\n\n");
    }
    // 没序号但有换行：按非空行拆段，自动补 1. 2. 3.
    if (/\n/.test(text)) {
      const lines = text.split(/\n+/).map((s) => s.trim()).filter(Boolean);
      if (lines.length > 1) {
        return lines.map((s, i) => (i + 1) + ". " + s).join("\n\n");
      }
    }
    // 既无序号也无换行，原样返回不强切
    return text;
  }

  // ── 视图切换 ──────────────────────────────────
  const STAGE_LABEL = { setup: "SETUP", form: "QUERY", run: "RUNNING", results: "RANKED",
                        vault: "VAULT", workspace: "WORKSPACE" };

  function showView(name) {
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    $("view-" + name).classList.add("active");
    currentView = name;
    updateHistoryBtn();
    // 语言只在凭证页选，进入后锁定，隐藏 header 中间的切换钮
    $("hud-center").hidden = name !== "setup";
    window.scrollTo(0, 0);
    if (suppressPush) return;
    // 进浏览器历史。results 用 replace 顶掉跑动中的 run 那格，免得前进栈里残留半截搜索页
    const st = { view: name };
    if (name === "results") history.replaceState(st, "");
    else history.pushState(st, "");
  }

  // 侧键/浏览器前进后退落到某条记录，按记录里的视图名还原，缺数据就退到最近的可用页
  async function restoreView(st) {
    let name = (st && st.view) || "setup";
    // 浏览器前进后退落回 vault：现拉现渲染两栏，落大厅态（不还原到具体某个仓库详情）
    if (name === "vault") {
      await loadVaultRuns();
      await loadVaultHall();
      renderVaultRuns();
      renderVaultHall();
      vaultShowHall();
    }
    // 旧的 form/run/results 都并进 workspace：有结果还原结果态，还在搜就还原进度态，
    // 都没有才回搜索输入态。SSE 请求本身不受视图切换影响，还在后台跑，还原进度页能接着看
    if (name === "run" || name === "results" || name === "form" || name === "workspace") {
      name = "workspace";
      if (run && run.ranked && run.ranked.length) {
        renderWorkspaceResults(run.ranked);
        showWsPane("list");
      } else if (run && run.inProgress) {
        showWsPane("progress");
      } else {
        showWsPane("search");
      }
    }
    suppressPush = true;
    showView(name);
    suppressPush = false;
  }
  window.addEventListener("popstate", (e) => restoreView(e.state));

  // 停在凭证页一律不给看，免得绕过凭证。不在 vault 里显示「记忆库」入口，在 vault 里显示「返回搜索」
  function updateHistoryBtn() {
    $("btn-vault").hidden = currentView === "setup" || currentView === "vault";
    $("btn-vault-search").hidden = currentView !== "vault";
  }

  function showErr(id, msg) {
    const el = $(id);
    el.textContent = msg || "";
    el.classList.toggle("show", !!msg);
  }

  // ── 凭证页 ────────────────────────────────────
  // 页面加载时把上次记住的 api/pat/模型读回来自动填上，没存过就留空
  async function prefillCreds() {
    try {
      const res = await fetch("/creds");
      if (!res.ok) return;
      const c = await res.json();
      if (c.deepseek_api_key) $("in-apikey").value = c.deepseek_api_key;
      if (c.github_pat) $("in-pat").value = c.github_pat;
      if (c.model) $("in-model").value = c.model;
      // 分阶段模型也带出来预填，没设过的框留空
      const m = c.models || {};
      STAGE_INPUTS.forEach(([key, id]) => { if (m[key]) $(id).value = m[key]; });
    } catch (e) {}
  }

  // 进入时把这次的 api/pat/模型（含分阶段）存到本地，改了就覆盖，下次自动带出来。不挡进入
  function saveCreds(apikey, pat, model, models) {
    fetch("/creds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deepseek_api_key: apikey, github_pat: pat, model: model, models: models || {} }),
    }).catch(() => {});
  }

  function enter() {
    const apikey = $("in-apikey").value.trim();
    const pat = $("in-pat").value.trim();
    const model = $("in-model").value.trim();
    if (!apikey || !pat) {
      showErr("setup-err", t("err.creds"));
      return;
    }
    showErr("setup-err", "");
    const models = collectStageModels();
    state.creds = { apikey, pat, model, models };
    saveCreds(apikey, pat, model, models);
    // 凭证进来直接进双栏，左栏显示搜索输入态（进来就能搜也能聊）
    showWsPane("search");
    showView("workspace");
  }

  $("btn-enter").addEventListener("click", enter);
  ["in-apikey", "in-pat", "in-model"].forEach((id) => {
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") enter();
    });
  });

  // ── 语言动态行（初始 1 个，最多 3 个）────────────
  const MAX_LANG = 3;
  const langList = $("lang-list");
  const addBtn = $("btn-add-lang");

  function langCount() {
    return langList.querySelectorAll(".lang-row").length;
  }
  function refreshAddBtn() {
    addBtn.disabled = langCount() >= MAX_LANG;
  }
  function addLangRow(value) {
    if (langCount() >= MAX_LANG) return;
    const row = document.createElement("div");
    row.className = "lang-row";
    row.innerHTML =
      '<input type="text" class="inp lang-inp" spellcheck="false" data-i18n-ph="form.langrow.ph" placeholder="' + esc(t("form.langrow.ph")) + '">' +
      '<button class="lang-del" data-i18n-title="form.langrow.del" title="' + esc(t("form.langrow.del")) + '">×</button>';
    row.querySelector(".lang-inp").value = value || "";
    row.querySelector(".lang-del").addEventListener("click", () => {
      if (langCount() <= 1) return;
      row.remove();
      refreshAddBtn();
    });
    langList.appendChild(row);
    refreshAddBtn();
  }
  addBtn.addEventListener("click", () => addLangRow());
  addLangRow();

  // ── 需求清单动态行（初始 3 个，可继续加）────────
  const kpList = $("kp-list");
  const addKpBtn = $("btn-add-kp");

  function addKpRow(value) {
    const row = document.createElement("div");
    row.className = "kp-row";
    row.innerHTML =
      '<input type="text" class="inp kp-inp" spellcheck="false" data-i18n-ph="form.kprow.ph" placeholder="' + esc(t("form.kprow.ph")) + '">' +
      '<button class="kp-del" data-i18n-title="form.kprow.del" title="' + esc(t("form.kprow.del")) + '">×</button>';
    row.querySelector(".kp-inp").value = value || "";
    row.querySelector(".kp-del").addEventListener("click", () => {
      // 至少留一行
      if (kpList.querySelectorAll(".kp-row").length <= 1) return;
      row.remove();
    });
    kpList.appendChild(row);
  }
  addKpBtn.addEventListener("click", () => addKpRow());
  addKpRow(); addKpRow(); addKpRow();

  // 把 i18n 的提示和例子填进「i」浮窗，语言切换时重填
  function fillKpTip() {
    const ex = t("form.kp.ex").split("\n").map((l) => "<li>" + esc(l) + "</li>").join("");
    $("kp-tip").innerHTML =
      '<div class="kp-tip-intro">' + esc(t("form.kp.tip")) + "</div>" +
      '<ul class="kp-tip-ex">' + ex + "</ul>";
  }
  fillKpTip();

  // ── 参数收集 ──────────────────────────────────
  function collectParams() {
    const languages = Array.from(langList.querySelectorAll(".lang-inp"))
      .map((i) => i.value.trim())
      .filter(Boolean);
    const keypoints = Array.from(kpList.querySelectorAll(".kp-inp"))
      .map((i) => i.value.trim())
      .filter(Boolean);
    return {
      deepseek_api_key: state.creds.apikey,
      github_pat: state.creds.pat,
      // 六键模型：分阶段设了用分阶段的，留空回退凭证页全局模型
      models: buildModels(),
      keypoints: keypoints,
      languages: languages,
      output_language: lang === "en" ? "English" : "简体中文",
      top_k: parseInt($("in-topk").value, 10) || 12,
      use_memory: $("in-usemem").checked,
    };
  }

  // ── Search：切到运行视图并发起 SSE ──────────────
  $("btn-search").addEventListener("click", () => {
    const p = collectParams();
    if (!p.keypoints.length) {
      showErr("form-err", t("err.keypoints"));
      return;
    }
    showErr("form-err", "");
    state.params = p;
    runPipeline(p);
  });

  // 新搜索：左栏切回输入态（还在双栏，不跳走）
  $("btn-restart").addEventListener("click", () => { showWsPane("search"); showView("workspace"); });

  // ── 工作台双栏 ──────────────────────────────────
  // 左栏三态切换：搜索输入 / 进度 / 结果，显示一个藏两个。搜索这条线内部的三个阶段。
  // 记一下 lastRepoPane，从「会话」tab 切回「结果」tab 时能还原到切走前的子态
  function showWsPane(name) {
    lastRepoPane = name;
    const panes = { search: "ws-search", progress: "ws-progress", list: "ws-list" };
    Object.values(panes).forEach((id) => { $(id).hidden = true; });
    $(panes[name]).hidden = false;
    // 展开态是结果卡的事，切回输入/进度时把左栏拉宽复位
    if (name !== "list") $("ws").classList.remove("expanded");
  }

  // 左栏顶部「结果 / 会话」两个 tab：repo 是既有的搜索三态群组，chat 是会话列表。
  // 切到 chat 时把三个 repo 面板一起藏起来、露出会话列表；切回 repo 就还原 lastRepoPane
  function switchLeftTab(tab) {
    leftTab = tab;
    document.querySelectorAll(".ws-tab").forEach((el) => {
      el.classList.toggle("active", el.dataset.tab === tab);
    });
    const repoPanes = ["ws-search", "ws-progress", "ws-list"];
    if (tab === "chat") {
      repoPanes.forEach((id) => { $(id).hidden = true; });
      $("ws-mem").hidden = true;
      $("ws-sessions").hidden = false;
      $("ws").classList.remove("expanded");
      loadSessions();
    } else if (tab === "mem") {
      repoPanes.forEach((id) => { $(id).hidden = true; });
      $("ws-sessions").hidden = true;
      $("ws-mem").hidden = false;
      $("ws").classList.remove("expanded");
      openWsMemory();
    } else {
      $("ws-sessions").hidden = true;
      $("ws-mem").hidden = true;
      showWsPane(lastRepoPane);
    }
  }
  document.querySelectorAll(".ws-tab").forEach((el) => {
    el.addEventListener("click", () => switchLeftTab(el.dataset.tab));
  });

  // 把 ranked 结果用真实 rcard 渲染进左栏（复用 rcardHTML/renderRanked），展开某张卡时
  // 联动左栏拉宽 2/3。注入钮点了把该 repo 加进右栏上下文芯片。
  function renderWorkspaceResults(ranked) {
    const list = $("ws-list");
    // 初次召回也显示表头（时间/仓库数/keypoint 子弹/查询词 chips），跟历史详情同款；卡片单独放 ws-cards
    list.innerHTML =
      '<div class="hd-meta ws-meta">' +
      headerMetaHTML(run.ts, run.contentTotal, run.keypoints, run.queries) +
      '</div><div class="ws-cards"></div>';
    const cards = list.querySelector(".ws-cards");
    renderRanked(cards, ranked);
    const ws = $("ws");
    // renderRanked 已绑好点 head 展开；这里额外挂一层：任意卡展开就拉宽左栏，全收起就复位
    list.querySelectorAll(".rcard-head").forEach((h) => {
      h.addEventListener("click", () => {
        ws.classList.toggle("expanded", ws.querySelector(".rcard.open") !== null);
      });
    });
    // 每张卡头加一个注入钮（rcardHTML 本身没有），点了把这个 repo 注入右栏上下文
    list.querySelectorAll(".rcard").forEach((card, i) => {
      const fn = (ranked[i] || {}).full_name || "";
      const head = card.querySelector(".rcard-head");
      const btn = document.createElement("button");
      btn.className = "ws-inject";
      btn.title = "注入对话";
      btn.textContent = "+";
      btn.addEventListener("click", (e) => { e.stopPropagation(); injectCtx(fn); });
      // 插在 caret 前，跟统计徽章一排
      head.insertBefore(btn, head.querySelector(".rcard-caret"));
    });
  }

  // 纯 DOM：往上下文条加一枚芯片，不碰 wsContext 数组。injectCtx（加新的）和
  // renderCtxChips（从已有数组整排重建，比如切回一个旧会话）分别调它
  function addCtxChip(name, recalled) {
    const box = $("ws-ctx");
    box.querySelector(".ws-ctx-empty")?.remove();
    const chip = document.createElement("span");
    chip.className = "ws-ctx-chip" + (recalled ? " ws-ctx-recalled" : "");
    chip.dataset.ctx = name;
    // 召回转正的芯片带个「召回」小标，标明是系统帮着捞进来的，不是用户手点的
    chip.innerHTML = (recalled ? '<span class="ws-ctx-tag">召回</span> ' : "") +
      esc(name.split("/").pop()) + ' <button title="' + esc(t("form.langrow.del")) + '">×</button>';
    chip.querySelector("button").addEventListener("click", () => {
      chip.remove();
      const i = wsContext.indexOf(name);
      if (i >= 0) wsContext.splice(i, 1);
      if (!box.querySelector(".ws-ctx-chip")) {
        box.innerHTML = '<span class="ws-ctx-empty">' + esc(t("ws.ctx.empty")) + "</span>";
      }
      // 记忆 tab 里对应的卡同步取消绿标（那卡是「已注入」态，这里移除了就不该再标注）
      document.querySelector('.ws-mem-list .hist-card[data-fn="' + CSS.escape(name) + '"]')
        ?.classList.remove("injected");
    });
    box.appendChild(chip);
  }

  // 上下文芯片：加一个（去重）/ 点 ✕ 移除；空了显示占位。wsContext 数组跟芯片同步，发消息时带给后端
  function injectCtx(name, recalled) {
    const box = $("ws-ctx");
    if (box.querySelector('[data-ctx="' + CSS.escape(name) + '"]')) return;
    wsContext.push(name);
    addCtxChip(name, recalled);
  }

  // 从 wsContext 数组整排重建芯片（不改数组），切换会话时用：先清空框再逐个补
  function renderCtxChips() {
    const box = $("ws-ctx");
    box.innerHTML = wsContext.length ? "" : '<span class="ws-ctx-empty">' + esc(t("ws.ctx.empty")) + "</span>";
    wsContext.forEach(addCtxChip);
  }

  // ── 左栏「记忆」tab：仓库记忆列表 + 模糊搜索，点卡注入对话上下文 ──────────
  // 进 tab 时拉一次 /memory 存本地，搜索是纯本地过滤不重新请求
  let wsMemItems = [];

  async function openWsMemory() {
    wsMemItems = await loadMemory();
    renderWsMemory();
  }

  // 按搜索框内容模糊过滤（全名/描述/tags 任一含关键词），渲染记忆卡，点卡注入上下文。
  // 已在上下文里的卡标成 injected，一眼看出注了哪些
  function renderWsMemory() {
    const q = ($("ws-mem-search").value || "").trim().toLowerCase();
    const items = q
      ? wsMemItems.filter((m) => {
          const hay = (m.full_name + " " + (m.description || "") + " " + (m.tags || []).join(" ")).toLowerCase();
          return hay.includes(q);
        })
      : wsMemItems;
    const list = $("ws-mem-list");
    if (!items.length) {
      list.innerHTML = '<div class="history-empty">' + esc(q ? t("ws.mem.nomatch") : t("mem.none")) + "</div>";
      return;
    }
    list.innerHTML = items.map(memCardHTML).join("");
    Array.from(list.querySelectorAll(".hist-card")).forEach((card) => {
      const fn = card.dataset.fn;
      if (wsContext.includes(fn)) card.classList.add("injected");
      card.addEventListener("click", () => {
        injectCtx(fn);
        card.classList.add("injected");
      });
    });
  }

  $("ws-mem-search")?.addEventListener("input", renderWsMemory);

  // ── 右栏对话 ──────────────────────────────────
  // 往聊天区加一个气泡，返回气泡里放文本的节点，流式时往它 append 文字。role 决定左右对齐样式
  function appendBubble(role, text) {
    const chat = $("ws-chat");
    // 首条消息把空态占位清掉
    $("ws-chat-empty")?.remove();
    const wrap = document.createElement("div");
    wrap.className = "ws-msg ws-msg-" + role;
    // assistant 气泡挂一个可点头像，点它弹出这轮发给模型的提示词（分色）。挂在 wrap 上的
    // _prompt 属性由 sendChat 收到 prompt 事件时填，没填就提示这轮没记录到
    if (role === "assistant") {
      const av = document.createElement("button");
      av.type = "button";
      av.className = "ws-avatar";
      av.title = "查看这轮发给模型的提示词";
      av.textContent = "🍃";
      av.addEventListener("click", () => openPromptInspector(wrap._prompt || null));
      wrap.appendChild(av);
    }
    const body = document.createElement("div");
    body.className = "ws-bubble";
    body.textContent = text || "";
    wrap.appendChild(body);
    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
    return body;
  }

  // 召回判断可视化：点头像时插在 user 消息之后，展示这轮小模型看了用户的话从候选里挑了什么。
  // r 是 recall_debug 段的 recall 数据；没跑召回或被跳过门拦下就说明原因
  function renderRecallInspect(r) {
    if (!r) return "";
    const tag = '<div class="pi-rc-tag">小模型提示词 + 输出（挑仓库用，区别于上面主模型）</div>';
    if (!r.fired) {
      return '<div class="pi-block pi-recall pi-rc-inspect">' + tag +
        '<div class="pi-rc-skip">这轮没跑召回：' + esc(r.reason || "") + "</div></div>";
    }
    const chips = (arr, cls) => (arr && arr.length)
      ? arr.map((x) => '<span class="pi-rc ' + cls + '">' + esc(x) + "</span>").join("")
      : '<span class="pi-rc-none">无</span>';
    const p = r.prompt || [];
    const sysText = (p.find((m) => m.role === "system") || {}).content || "";
    const userText = (p.find((m) => m.role === "user") || {}).content || "";
    // 提示词和回答直接打印（不折叠），跟主模型一样能看全文，只是框小字小、能滚动
    const sub = (label, text) => text
      ? '<div class="pi-rc-sublabel">' + esc(label) + '</div><pre class="pi-rc-pre">' + esc(text) + "</pre>"
      : "";
    return '<div class="pi-block pi-recall pi-rc-inspect">' + tag +
      '<div class="pi-rc-row">从 <b>' + ((r.manifest || []).length) + "</b> 条候选里挑：" +
      "命中仓库 " + chips(r.hit_repos, "pi-rc-hit") +
      "　命中记忆 " + chips(r.hit_memories, "pi-rc-hit") +
      "　分不清·会反问 " + chips(r.ambiguous, "pi-rc-amb") + "</div>" +
      sub("提示词注入 · system（挑选规则）", sysText) +
      sub("提示词注入 · user（候选清单 + 最近对话）", userText) +
      (r.raw
        ? '<div class="pi-rc-sublabel pi-rc-anslabel">小模型的回答（它最终挑的结果）</div>' +
          '<pre class="pi-rc-pre pi-rc-answer">' + esc(r.raw) + "</pre>"
        : "") +
      "</div>";
  }

  // 提示词监控：点 agent 头像弹出这轮发给模型的完整提示词，按板块分色（人设、每个注入仓库、
  // user、assistant 各一色）。ev 是后端的 prompt 事件 {segments, messages}，没有就提示没记录到
  function openPromptInspector(ev) {
    document.querySelector(".pi-overlay")?.remove();
    const ov = document.createElement("div");
    ov.className = "pi-overlay";
    let body;
    if (!ev) {
      body = '<div class="pi-empty">这轮没有记录到提示词。打开页面后聊的那几轮才有。</div>';
    } else {
      // 这轮历史被压过就在顶部标一条，让人知道下面的 messages 是压缩后注入模型的样子、原文在会话记录里
      const pathName = { note_replace: "笔记顶替", summary_fallback: "摘要兜底",
        hard_truncate_circuit: "熔断硬截断", hard_truncate_failed: "摘要失败·硬截断",
        reactive_truncate: "模型拒绝后硬截" }[ev.compact_path] || ev.compact_path;
      const compactNote = ev.compacted
        ? '<div class="pi-compacted">本轮历史已压缩（' + esc(pathName) +
          "）：下面是压缩后注入模型的内容，完整对话在会话记录里</div>"
        : "";
      const kindCls = { persona: "pi-persona", memory: "pi-memory", repo: "pi-repo",
        recall_memory: "pi-recall", recall_hint: "pi-recall" };
      const allSegs = ev.segments || [];
      // 召回判断段单独拎出来，不当普通提示词段渲染，末尾插在 user 消息之后
      const recallDbg = allSegs.find((s) => s.kind === "recall_debug");
      const seg = allSegs.filter((s) => s.kind !== "recall_debug").map((s) => {
        const cls = kindCls[s.kind] || "pi-repo";
        // 召回补进来的仓库段挂个「召回」小标，跟用户手动「+」注入的区分开
        const tag = s.recalled ? ' <span class="pi-recall-tag">召回</span>' : "";
        return '<div class="pi-block ' + cls + (s.recalled ? " pi-recalled" : "") +
          '"><div class="pi-label">' + esc(s.label) + tag +
          "</div><pre>" + esc(s.text) + "</pre></div>";
      }).join("");
      const msg = (ev.messages || []).map((m) => {
        const cls = m.role === "user" ? "pi-user" : "pi-assistant";
        return '<div class="pi-block ' + cls + '"><div class="pi-label">' + esc(m.role) +
          "</div><pre>" + esc(m.content) + "</pre></div>";
      }).join("");
      body = compactNote + seg + msg + renderRecallInspect(recallDbg && recallDbg.recall);
    }
    ov.innerHTML =
      '<div class="pi-box"><div class="pi-head"><span>上一轮发给模型的提示词</span>' +
      '<button class="pi-close" type="button">×</button></div>' +
      '<div class="pi-body">' + body + "</div></div>";
    document.body.appendChild(ov);
    const close = () => ov.remove();
    ov.querySelector(".pi-close").addEventListener("click", close);
    ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  }

  // 发一条消息：读输入框 + wsContext，POST /chat，SSE 逐 delta 追加进 AI 气泡
  async function sendChat() {
    if (wsSending) return;
    const input = $("ws-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    input.style.height = "auto";

    // 第一条消息时给这次对话生成一个 session id，用创建时间戳（YYYYMMDD-HHMMSS），
    // 一眼看出啥时候开的、也能排序；后面每轮都存进同一行
    if (!wsSessionId) {
      const d = new Date();
      const p = (n) => String(n).padStart(2, "0");
      wsSessionId = "" + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) +
        "-" + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
    }

    // 用户气泡先上屏，存进多轮历史
    appendBubble("user", text);
    wsMessages.push({ role: "user", content: text });

    // AI 气泡先建空的，delta 来了往里填；发送中禁钮
    wsSending = true;
    $("ws-send").disabled = true;
    const bubble = appendBubble("assistant", "");
    bubble.classList.add("streaming");
    // 这轮 AI 气泡的外层，收到 prompt 事件就把提示词挂它身上，点头像时读
    const bubbleWrap = bubble.parentElement;
    let acc = "";
    // 后端这轮若压缩了历史，会 yield compacted 事件带来压缩后的数组，先存这，finally 里替换
    let pendingCompact = null;

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // 齿轮设了就用齿轮的，没设回退凭证页；只影响对话
          deepseek_api_key: chatOverride.apikey || state.creds.apikey,
          // 六键模型，齿轮的对话模型只覆盖对话回答（chat）那档，召回/提取仍用凭证页配置
          models: buildModels(chatOverride.model ? { chat: chatOverride.model } : null),
          // 只把 role/content 发给模型，挂在消息上的提示词分段是本地存的、不发（发了没用还占 token）
          messages: wsMessages.map((m) => ({ role: m.role, content: m.content })),
          context: wsContext,
          // 带上会话 id，后端聊完按它触发提取记忆
          session_id: wsSessionId,
        }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let m;
        const sepRe = /\r?\n\r?\n/;
        while ((m = sepRe.exec(buf))) {
          const frame = buf.slice(0, m.index);
          buf = buf.slice(m.index + m[0].length);
          const line = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
          if (ev.type === "prompt") {
            // 这轮完整提示词（分段 + 消息）：存最近一次，也挂到这轮气泡头像上
            wsLastPrompt = ev;
            if (bubbleWrap) bubbleWrap._prompt = ev;
          } else if (ev.type === "recall") {
            // 召回帮着捞进来的仓库，转正成右栏芯片，往后每轮常驻（跟点过「+」一样，用户可随手删）
            (ev.repos || []).forEach((r) => injectCtx(r, true));
          } else if (ev.type === "delta") {
            acc += ev.text || "";
            bubble.textContent = acc;
            $("ws-chat").scrollTop = $("ws-chat").scrollHeight;
          } else if (ev.type === "compacted") {
            // 后端压了历史，把压缩后的数组收下，finally 里替换 wsMessages（不影响已显示的气泡）
            pendingCompact = ev.messages || null;
          } else if (ev.type === "error") {
            acc += (acc ? "\n\n" : "") + "出错：" + (ev.message || "未知错误");
            bubble.textContent = acc;
            bubble.classList.add("error");
          }
        }
      }
    } catch (e) {
      bubble.textContent = (acc ? acc + "\n\n" : "") + "出错：" + String(e);
      bubble.classList.add("error");
    } finally {
      bubble.classList.remove("streaming");
      // 空回复（比如一上来就报错）不入历史，免得脏了后续多轮。把这轮的提示词分段挂在 assistant
      // 这条上一起存进会话，退出重进点头像还能看（含召回判断）；发给模型时上面会剥掉、只发 role/content
      if (acc) {
        const seg = (bubbleWrap && bubbleWrap._prompt) ? bubbleWrap._prompt.segments : null;
        wsMessages.push({ role: "assistant", content: acc, prompt: seg ? { segments: seg } : null });
      }
      wsSending = false;
      $("ws-send").disabled = false;
      input.focus();
      // 先存完整对话底账（此刻 wsMessages 是完整的，saveSession 里 JSON.stringify 同步取值）
      saveSession();
      // 再看这轮有没有被后端压缩：有就用压缩后的数组替换本地历史，下轮发的就小了。
      // 完整对话已经存进 chat_sessions，替换只影响下轮要发的内容，已显示的气泡不受影响
      if (pendingCompact) {
        wsMessages.length = 0;
        for (const msg of pendingCompact) wsMessages.push(msg);
        pendingCompact = null;
      }
    }
  }

  // 把当前完整对话 upsert 到后端。标题不归这个接口管：新建时后端自动给 Session N，
  // 改过名字的也不会被这里冲掉。存失败只吞掉不打扰用户，会话列表下次刷新再看
  async function saveSession() {
    if (!wsSessionId || !wsMessages.length) return;
    try {
      await fetch("/chat/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: wsSessionId,
          context: wsContext,
          messages: wsMessages,
        }),
      });
      // 存完刷新一下会话列表（如果正显示着），新会话能立刻在列表里看到
      if (leftTab === "chat") loadSessions();
    } catch (e) {
      // 存历史失败不影响对话本身，静默
    }
  }

  // 空态占位的原始 HTML，「+ 新对话」和打开一个空会话时用它复位聊天区。用函数现拼而不是
  // 冻结的常量，随 lang 变化取当前语言的文案
  function wsChatEmptyHTML() {
    return '<div class="ws-chat-empty" id="ws-chat-empty">' +
      '<div class="ws-chat-empty-acc"></div><p>' + esc(t("ws.chat.empty.title")) + "</p>" +
      "<span>" + esc(t("ws.chat.empty.sub")) + "</span></div>";
  }

  // 按 wsMessages 整个重建聊天区气泡（不带流式），切换会话/新对话时用
  function renderChatFromMessages() {
    const chat = $("ws-chat");
    chat.innerHTML = wsMessages.length ? "" : wsChatEmptyHTML();
    wsMessages.forEach((m) => {
      const body = appendBubble(m.role, m.content);
      // 还原 assistant 气泡时把存下的提示词分段挂回头像，点开还能看这轮发了什么（含召回判断）
      if (m.role === "assistant" && m.prompt && body.parentElement) {
        body.parentElement._prompt = m.prompt;
      }
    });
    chat.scrollTop = chat.scrollHeight;
  }

  // ── 会话列表（左栏「会话」tab）──────────────────
  async function loadSessions() {
    try {
      const res = await fetch("/chat/session");
      lastSessions = res.ok ? (await res.json()).items || [] : [];
    } catch (e) {
      lastSessions = [];
    }
    renderSessions();
  }

  function sessRowHTML(s) {
    const active = s.session_id === wsSessionId ? " active" : "";
    const pin = s.pinned ? '<span class="sess-pin">📌</span>' : "";
    return (
      '<div class="sess-row' + active + '" data-sid="' + esc(s.session_id) + '">' +
      pin +
      '<span class="sess-title">' + esc(s.title || "") + "</span>" +
      '<span class="sess-time">' + fmtTime(s.updated_at) + "</span>" +
      "</div>"
    );
  }

  function renderSessions() {
    const list = $("sess-list");
    if (!lastSessions.length) {
      list.innerHTML = '<div class="sess-empty">' + esc(t("sess.empty")) + "</div>";
      return;
    }
    list.innerHTML = lastSessions.map(sessRowHTML).join("");
    Array.from(list.querySelectorAll(".sess-row")).forEach((row) => {
      row.addEventListener("click", () => openSession(row.dataset.sid));
      row.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const sid = row.dataset.sid;
        const s = lastSessions.find((x) => x.session_id === sid);
        if (s) openSessCtxMenu(s, e.clientX, e.clientY);
      });
    });
  }

  // 点一个会话：取完整对话，还原消息、上下文、session id，重渲染聊天区和上下文芯片。
  // 回复流式接收中不许切，否则这轮收完会把回复错存进切过去的那个会话，串号
  async function openSession(sid) {
    if (wsSending) return;
    try {
      const res = await fetch("/chat/session/" + encodeURIComponent(sid));
      if (!res.ok) return;
      const data = await res.json();
      wsSessionId = data.session_id;
      wsMessages.length = 0;
      (data.messages || []).forEach((m) => wsMessages.push(m));
      wsContext.length = 0;
      (data.context || []).forEach((c) => wsContext.push(c));
      renderChatFromMessages();
      renderCtxChips();
      renderSessions();
    } catch (e) {
      // 取失败就不动当前态，静默
    }
  }

  // 「+ 新对话」：清掉当前 session id / 消息 / 上下文，聊天区和上下文条复位成空态。
  // 回复流式接收中不许新起对话，理由同 openSession
  function startNewChat() {
    if (wsSending) return;
    wsSessionId = null;
    wsMessages.length = 0;
    wsContext.length = 0;
    renderChatFromMessages();
    renderCtxChips();
    renderSessions();
  }
  $("btn-new-chat")?.addEventListener("click", startNewChat);

  // 会话右键菜单：置顶/取消置顶、重命名、删除。ctxMenuSid 记菜单当前对着哪个会话
  let ctxMenuSid = null;

  function openSessCtxMenu(s, x, y) {
    ctxMenuSid = s.session_id;
    $("sess-ctx-pin").textContent = s.pinned ? t("sess.ctx.unpin") : t("sess.ctx.pin");
    const menu = $("sess-ctxmenu");
    menu.hidden = false;
    // 贴着鼠标开，靠近右/下边缘时往回缩，别把菜单开到视口外
    const left = Math.min(x, window.innerWidth - menu.offsetWidth - 8);
    const top = Math.min(y, window.innerHeight - menu.offsetHeight - 8);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  function closeSessCtxMenu() {
    $("sess-ctxmenu").hidden = true;
    ctxMenuSid = null;
  }

  document.addEventListener("click", (e) => {
    const menu = $("sess-ctxmenu");
    if (!menu.hidden && !menu.contains(e.target)) closeSessCtxMenu();
  });

  $("sess-ctx-pin")?.addEventListener("click", async () => {
    const sid = ctxMenuSid;
    if (!sid) return;
    const s = lastSessions.find((x) => x.session_id === sid);
    const nextPinned = !(s && s.pinned);
    closeSessCtxMenu();
    await fetch("/chat/session/" + encodeURIComponent(sid), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned: nextPinned }),
    });
    loadSessions();
  });

  $("sess-ctx-rename")?.addEventListener("click", () => {
    const sid = ctxMenuSid;
    closeSessCtxMenu();
    if (sid) startRename(sid);
  });

  $("sess-ctx-del")?.addEventListener("click", async () => {
    const sid = ctxMenuSid;
    closeSessCtxMenu();
    if (!sid) return;
    const r = await openConfirm({
      title: t("confirm.delsess.title"),
      message: t("confirm.delsess.msg"),
      confirmLabel: t("confirm.delete"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/chat/session/" + encodeURIComponent(sid), { method: "DELETE" });
    // 删的正好是当前打开的会话，右栏也得跟着复位，免得对着一个已经不存在的 session 继续发消息
    if (wsSessionId === sid) startNewChat();
    else loadSessions();
  });

  // 把会话行的标题换成一个输入框就地改名，Enter/失焦提交，Escape 放弃
  function startRename(sid) {
    const row = document.querySelector('.sess-row[data-sid="' + CSS.escape(sid) + '"]');
    if (!row) return;
    const titleEl = row.querySelector(".sess-title");
    const old = titleEl.textContent;
    const input = document.createElement("input");
    input.className = "sess-rename-inp";
    input.value = old;
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let cancelled = false;
    function commit() {
      if (cancelled) return;
      const val = input.value.trim();
      if (val && val !== old) {
        fetch("/chat/session/" + encodeURIComponent(sid), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: val }),
        }).finally(loadSessions);
      } else {
        loadSessions();
      }
    }
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { e.preventDefault(); cancelled = true; loadSessions(); }
    });
    input.addEventListener("blur", commit);
  }

  // 发送钮点击 + 输入框 Enter 发送（Shift+Enter 换行），textarea 随内容自增高
  $("ws-send")?.addEventListener("click", sendChat);
  $("ws-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $("ws-input")?.addEventListener("input", () => {
    const el = $("ws-input");
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  });

  // 齿轮：默认不展开，对话默认用凭证页的 api/model。点开才露出设置面板，
  // 输入过程只改草稿 draft，不点「确认」不落进 chatOverride——不管是再点一次齿轮
  // 还是点外面关掉，草稿都直接丢弃，chatOverride 保持上次确认的值不变
  const cfgDraft = { apikey: "", model: "" };
  $("ws-gear")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const cfg = $("ws-cfg");
    if (cfg.hidden) {
      // 打开时把输入框填成上次确认的值，草稿从这份值起
      cfgDraft.apikey = chatOverride.apikey;
      cfgDraft.model = chatOverride.model;
      $("ws-cfg-key").value = chatOverride.apikey;
      $("ws-cfg-model").value = chatOverride.model;
    }
    cfg.hidden = !cfg.hidden;
  });
  $("ws-cfg-key")?.addEventListener("input", (e) => { cfgDraft.apikey = e.target.value.trim(); });
  $("ws-cfg-model")?.addEventListener("input", (e) => { cfgDraft.model = e.target.value.trim(); });
  // 点确认才把草稿落进 chatOverride 并收起弹窗
  $("ws-cfg-ok")?.addEventListener("click", (e) => {
    e.stopPropagation();
    chatOverride.apikey = cfgDraft.apikey;
    chatOverride.model = cfgDraft.model;
    $("ws-cfg").hidden = true;
  });
  // 点弹窗外面收起，草稿直接丢弃不保存
  document.addEventListener("click", (e) => {
    const cfg = $("ws-cfg");
    if (cfg && !cfg.hidden && !cfg.contains(e.target) && e.target.id !== "ws-gear") {
      cfg.hidden = true;
    }
  });

  // 搜索完由 onContentDone 自动进 workspace；这个钮只在已有结果时用来回到双栏，没结果不响应
  $("btn-ws-preview")?.addEventListener("click", () => {
    if (!(run && run.ranked && run.ranked.length)) return;
    renderWorkspaceResults(run.ranked);
    showWsPane("list");
    showView("workspace");
  });

  // ── 阶段进度条 ────────────────────────────────
  const STAGE_ORDER = ["qu", "search", "content"];

  function setStageActive(stage) {
    const idx = STAGE_ORDER.indexOf(stage);
    document.querySelectorAll(".stage-item").forEach((el) => {
      const i = STAGE_ORDER.indexOf(el.dataset.stage);
      el.classList.toggle("done", i < idx);
      el.classList.toggle("active", i === idx);
    });
    document.querySelectorAll(".stage-bar").forEach((bar, k) => {
      bar.classList.toggle("fill", k < idx);
    });
  }
  function setStageAllDone() {
    document.querySelectorAll(".stage-item").forEach((el) => {
      el.classList.add("done");
      el.classList.remove("active");
    });
    document.querySelectorAll(".stage-bar").forEach((bar) => bar.classList.add("fill"));
  }

  // ── SSE 读取与分发 ───────────────────────────
  function resetRunState() {
    run.contentRowEls = {};
    run.contentLogEls = {};
    run.ranked = [];
    // 本次搜索的时间戳，结果页表头显示用（跟历史详情同款）
    run.ts = Date.now();
    $("qu-strip").hidden = true;
    $("qu-strip").innerHTML = "";
    // kp-std-box 是后加的元素，旧 index.html 缓存里可能没有，判空防止整个搜索流程崩
    const kpBox = $("kp-std-box");
    if (kpBox) {
      kpBox.hidden = true;
      kpBox.innerHTML = "";
    }
    $("panel-content").hidden = true;
    $("content-tbody").innerHTML = "";
    $("results-list").innerHTML = "";
    $("cost-area").innerHTML = "";
    showErr("run-err", "");
  }

  async function runPipeline(params) {
    resetRunState();
    // 搜索时留在双栏，左栏切进度态（不再跳独立的 run 视图）
    showWsPane("progress");
    showView("workspace");
    setStageActive("qu");
    // 标记搜索正在跑，restoreView 靠它判断后退/前进要不要回到进度页
    run.inProgress = true;
    try {
      const res = await fetch("/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      if (!res.ok) {
        onError({ message: "HTTP " + res.status });
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE 帧以空行分隔，sse-starlette 发的是 \r\n\r\n，这里两种换行都认，逐帧切出来处理
        let m;
        const sepRe = /\r?\n\r?\n/;
        while ((m = sepRe.exec(buf))) {
          handleFrame(buf.slice(0, m.index));
          buf = buf.slice(m.index + m[0].length);
        }
      }
    } catch (e) {
      onError({ message: String(e) });
    }
  }

  function handleFrame(frame) {
    // 一帧里取 data: 行，ping 之类的注释帧没有 data: 行，直接跳过
    const line = frame.split("\n").find((l) => l.startsWith("data:"));
    if (!line) return;
    let ev;
    try {
      ev = JSON.parse(line.slice(5).trim());
    } catch (e) {
      return;
    }
    dispatch(ev);
  }

  function dispatch(ev) {
    const handlers = {
      qu_done: onQuDone,
      keypoints_compiled: onKeypointsCompiled,
      search_done: onSearchDone,
      repo_event: onRepoEvent,
      content_log: onContentLog,
      content_done: onContentDone,
      cost: onCost,
      done: onDone,
      error: onError,
    };
    const fn = handlers[ev.type];
    if (fn) fn(ev);
  }

  // ── 各阶段渲染 ────────────────────────────────
  function onQuDone(ev) {
    // 存进 run，供结果页表头显示（跟历史详情同一套）
    run.queries = ev.queries || [];
    run.keypoints = ev.keypoints || [];
    const qs = (ev.queries || []).map(
      (q) => '<span class="qu-chip">' + esc(q.q || q) + "</span>"
    );
    const kps = (ev.keypoints || []).map(
      (k) => '<span class="qu-chip kp">' + esc(k) + "</span>"
    );
    const strip = $("qu-strip");
    strip.innerHTML = qs.concat(kps).join("");
    strip.hidden = qs.length + kps.length === 0;
    setStageActive("search");
  }

  // 编译出的判定标准回显：每条 keypoint 一行「原文：标准」，让用户看到系统怎么理解需求。
  // 标准空的（编译失败降级）不显示分隔符和标准，只显示原文
  function onKeypointsCompiled(ev) {
    const box = $("kp-std-box");
    if (!box) return;
    const rows = (ev.compiled || []).map((c) => {
      const kp = '<span class="kp-std-kp">' + esc(c.keypoint) + "</span>";
      const std = c.standard
        ? '<span class="kp-std-arrow">：</span><span class="kp-std-txt">' + esc(c.standard) + "</span>"
        : "";
      return '<div class="kp-std-row">' + kp + std + "</div>";
    });
    box.innerHTML = rows.join("");
    box.hidden = rows.length === 0;
  }

  function onSearchDone(ev) {
    run.contentRowEls = {};
    run.contentLogEls = {};
    // 没有初筛，候选搜回来直接亮出 content 面板，行随每个仓库的第一条进度事件动态加
    $("panel-content").hidden = false;
    $("content-tbody").innerHTML = "";
    $("content-meta").textContent = t("run.content.deepdive") + "0";
    setStageActive("content");
  }

  // 流式事件总入口：现在只有 content 一路
  function onRepoEvent(ev) {
    onContentEvent(ev);
  }

  // 一个仓库来第一条进度事件就给 content 表加一行，全名带斜杠不好做 selector，用引用映射存着。
  // 进度行下面跟一个默认收起的展开行，装这个仓库自己的工具调用日志，点仓库名那一格切换展开
  function addContentRow(fn) {
    if (run.contentRowEls[fn]) return run.contentRowEls[fn];
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td class="c-name"><span class="c-caret">+</span>' +
        '<span class="c-repo">' + esc(fn) + "</span></td>" +
      '<td class="c-status"><span class="cdot"></span><span class="c-phase"></span><span class="c-skip-tag"></span></td>' +
      '<td class="c-mono c-round">0</td>' +
      '<td class="c-mono c-tools">0</td>' +
      '<td class="c-mono c-tok">0</td>' +
      '<td class="c-mono c-hit">0%</td>';
    $("content-tbody").appendChild(tr);

    // 展开行：默认收起，点进度行切换。日志追加进里面的 c-log-body
    const logtr = document.createElement("tr");
    logtr.className = "c-logrow";
    logtr.style.display = "none";
    logtr.innerHTML = '<td colspan="6"><div class="c-log-body"></div></td>';
    $("content-tbody").appendChild(logtr);

    tr.querySelector(".c-name").addEventListener("click", () => {
      const open = logtr.style.display !== "none";
      logtr.style.display = open ? "none" : "";
      tr.querySelector(".c-caret").textContent = open ? "+" : "−";
    });

    run.contentRowEls[fn] = tr;
    run.contentLogEls[fn] = logtr.querySelector(".c-log-body");
    $("content-meta").textContent = t("run.content.deepdive") + Object.keys(run.contentRowEls).length;
    return tr;
  }

  function onContentEvent(ev) {
    // 第一条 content 事件来时把阶段条推到 content；该仓库还没有行就补一行
    setStageActive("content");
    const tr = run.contentRowEls[ev.full_name] || addContentRow(ev.full_name);
    const cls = { running: "is-running", judging: "is-judging", done: "is-done", degraded: "is-degraded", skipped: "is-skipped" };
    tr.querySelector(".cdot").className = "cdot " + (cls[ev.status] || "");
    // judging 是探查完到出结果之间的判定态，挂个「判定中」小字，辩论 worker 有计数就带上
    // (如「判定中 3/6」，6 = keypoint 数 × 正反两方)，转到别的状态时清掉
    const judgeCnt = ev.status === "judging" && ev.debate_total > 0 ? ` ${ev.debate_done}/${ev.debate_total}` : "";
    tr.querySelector(".c-phase").textContent = ev.status === "judging" ? t("ctable.judging") + judgeCnt : "";
    // gate 判跳过：整行置灰、状态旁挂「已跳过」、跳过原因收进展开区，点开仓库名才看，不撑宽那一行
    if (ev.status === "skipped") {
      tr.classList.add("row-skipped");
      tr.querySelector(".c-skip-tag").textContent = t("ctable.skipped");
      const box = run.contentLogEls[ev.full_name];
      if (box && ev.reason) {
        box.innerHTML = '<div class="log-line">' + esc(ev.reason) + "</div>";
      }
    }
    tr.querySelector(".c-round").textContent = ev.round || 0;
    tr.querySelector(".c-tools").textContent = ev.tools || 0;
    tr.querySelector(".c-tok").textContent = ev.tokens || 0;
    tr.querySelector(".c-hit").textContent = Math.round((ev.hit_rate || 0) * 100) + "%";
  }

  function onContentLog(ev) {
    // 只收带 full_name 的仓库日志，进它自己的展开区；不带 full_name 的是 QU/检索零星日志，
    // 已删掉全局日志窗、直接丢弃。仓库还没建行就先补一行，免得日志先到、行没到而丢日志
    if (!ev.full_name) return;
    if (!run.contentLogEls[ev.full_name]) addContentRow(ev.full_name);
    const body = run.contentLogEls[ev.full_name];
    const div = document.createElement("div");
    div.className = "log-line";
    div.textContent = ev.line;
    body.appendChild(div);
    body.scrollTop = body.scrollHeight;
    // 限制行数，防止长跑把 DOM 撑爆
    while (body.childElementCount > 600) body.removeChild(body.firstChild);
  }

  function onContentDone(ev) {
    run.ranked = ev.ranked || [];
    run.contentTotal = ev.total || 0;
    setStageAllDone();
    // 搜索完左栏切结果态：真实 rcard 渲染进左栏，右栏对话就绪
    renderWorkspaceResults(run.ranked);
    showWsPane("list");
    // 亮出 hud 的「工作台」钮，去了别的视图能回来
    $("btn-ws-preview").hidden = false;
    showView("workspace");
  }

  function rcardHTML(r, i, deletable) {
    const d = r.dissection || {};
    // gate 判跳过的仓库没克隆没深挖，卡片置灰、头上挂「已跳过」、展开只给跳过原因
    const skipped = !!r.skipped;
    const kd = (d.key_designs || [])
      .map(
        (k) =>
          '<div class="kd-item">' +
          '<span class="kd-name">' + esc(k.name || "") + "</span>" +
          (k.where ? '<span class="kd-where">' + esc(k.where) + "</span>" : "") +
          "<div>" + esc(k.detail || "") + "</div>" +
          "</div>"
      )
      .join("");
    // keypoint 全中绿、全失红、部分中黄；满分给卡片发光
    const kpFull = r.keypoint_total > 0 && r.keypoint_hits === r.keypoint_total;
    const kpCls = kpFull ? "kp-full" : r.keypoint_hits === 0 ? "kp-none" : "kp-part";
    // 辩论一方的论据行：论据 + 锚点(mono) + 未证实标；空手时显示 searched
    function sideHTML(side, label, cls) {
      if (!side) return "";
      const text = side.evidence || side.searched || "";
      if (!text) return "";
      return (
        '<div class="kpj-side ' + cls + '">' +
        '<span class="kpj-side-tag">' + esc(label) + "</span>" +
        esc(text) +
        (side.where ? '<span class="kpj-where">' + esc(side.where) + "</span>" : "") +
        (side.unverified ? '<span class="kpj-unv">' + esc(t("rcard.unverified")) + "</span>" : "") +
        "</div>"
      );
    }
    // 每条 keypoint 命中绿、没中红，附裁决理由和正反双方论据
    const kpDetail = ((r.detail && r.detail.keypoints) || [])
      .map((k) =>
        '<div class="kp-judge ' + (k.status === "hit" ? "hit" : "miss") + '">' +
        '<span class="kpj-dot"></span>' +
        '<span class="kpj-text">' + esc(k.keypoint || "") + "</span>" +
        sideHTML(k.advocate, t("rcard.advocate"), "adv") +
        sideHTML(k.skeptic, t("rcard.skeptic"), "ske") +
        sideHTML({ evidence: k.evidence }, "⚖️ " + t("rcard.verdict"), "adj") +
        "</div>"
      ).join("");
    // 跳过的卡头上挂灰标签，正常卡挂 keypoint 命中徽章
    const badge = skipped
      ? '<span class="rcard-skip-tag">' + esc(t("rcard.skipped")) + "</span>"
      : '<span class="rcard-kp ' + kpCls + '">keypoint ' + r.keypoint_hits + "/" + r.keypoint_total + "</span>";

    // 跳过的卡展开只放 github 链接和跳过原因，正常卡照旧放整份拆解
    const body = skipped
      ? '<a class="repo-link" href="https://github.com/' + esc(r.full_name) +
          '" target="_blank" rel="noopener">github.com/' + esc(r.full_name) + "</a>" +
        (r.reason ? '<div class="diss-sec">' + esc(t("rcard.skipreason")) + '</div><div class="diss-skip">' + esc(r.reason) + "</div>" : "")
      : (d.purpose ? '<div class="diss-lead">' + esc(d.purpose) + "</div>" : "") +
        '<a class="repo-link" href="https://github.com/' + esc(r.full_name) +
          '" target="_blank" rel="noopener">github.com/' + esc(r.full_name) + "</a>" +
        (d.tech_stack
          ? '<div class="diss-sec">' + esc(t("rcard.techstack")) + '</div><div class="diss-tech">' + esc(d.tech_stack) + "</div>"
          : "") +
        (kpDetail ? '<div class="diss-sec">' + esc(t("rcard.keypoints")) + '</div><div class="kp-judge-list">' + kpDetail + "</div>" : "") +
        (kd ? '<div class="diss-sec">' + esc(t("rcard.keydesigns")) + '</div><div class="kd-list">' + kd + "</div>" : "") +
        (d.architecture
          ? '<div class="diss-sec">' + esc(t("rcard.architecture")) + '</div><div class="diss-arch">' + esc(normalizeArchitecture(d.architecture)) + "</div>"
          : "") +
        (r.read_files && r.read_files.length
          ? '<div class="diss-sec">' + esc(t("rcard.readfiles")) + '</div><div class="diss-files">' + r.read_files.map(esc).join("、") + "</div>"
          : "");

    // 每个仓库自己的探查日志，挂卡片最底、默认折叠、点击展开（trace 由 pipeline 按仓库分组后放进 r.trace）
    const traceSec = (r.trace && r.trace.length)
      ? '<div class="rcard-trace"><div class="rcard-trace-head">📜 ' + esc(t("run.trace.head")) +
        ' <span class="rcard-trace-n">' + r.trace.length + " " + esc(t("proc.meta.lines")) + "</span></div>" +
        '<div class="rcard-trace-body">' +
        r.trace.map((l) => '<div class="log-line">' + esc(l) + "</div>").join("") +
        "</div></div>"
      : "";

    return (
      '<div class="rcard' + (skipped ? " skipped" : "") + (r.degraded ? " degraded" : "") + (kpFull && !skipped ? " perfect" : "") + '">' +
      '<div class="rcard-head' + (deletable ? " has-del" : "") + '">' +
      '<span class="rcard-rank">#' + (i + 1) + "</span>" +
      '<span class="rcard-title">' + esc(r.full_name) + "</span>" +
      '<span class="rcard-stats">' +
      badge +
      "<span>🌟 " + fmtStars(r.stars) + "</span>" +
      "<span>" + toMB(r.size) + "MB</span>" +
      "</span>" +
      '<span class="rcard-caret"></span>' +
      (deletable ? '<button class="rcard-del" data-fn="' + esc(r.full_name) + '" title="' + esc(t("rcard.del")) + '">×</button>' : "") +
      "</div>" +
      '<div class="rcard-body"><div class="rcard-inner"><div class="rcard-content">' +
      body + traceSec +
      "</div></div></div>" +
      "</div>"
    );
  }

  // 把 ranked 渲染进任意容器，结果页和历史详情页共用。传 onDelete 才在每张卡上加单删按钮
  function renderRanked(list, ranked, onDelete) {
    list.innerHTML = (ranked || []).map((r, i) => rcardHTML(r, i, !!onDelete)).join("");
    Array.from(list.querySelectorAll(".rcard-head")).forEach((h) => {
      h.addEventListener("click", () => h.parentElement.classList.toggle("open"));
    });
    // 每仓库探查日志的折叠头：点击展开/收起自己那块，别冒泡去动整卡
    Array.from(list.querySelectorAll(".rcard-trace-head")).forEach((th) => {
      th.addEventListener("click", (e) => {
        e.stopPropagation();
        th.parentElement.classList.toggle("open");
      });
    });
    if (onDelete) {
      Array.from(list.querySelectorAll(".rcard-del")).forEach((b) => {
        b.addEventListener("click", (e) => {
          // 拦住冒泡，别让点删除顺带把卡片展开
          e.stopPropagation();
          onDelete(b.dataset.fn);
        });
      });
    }
  }

  function renderResults() {
    renderRanked($("results-list"), run.ranked);
  }

  // 把 cost 表渲染进任意容器，结果页和历史详情页共用。参数叫 tbl，别和翻译函数 t() 撞名
  function renderCost(area, tbl) {
    tbl = tbl || {};
    const label = { query_understanding: t("run.stage.qu"), keypoint_understanding: "KU",
                    skip_gate: t("run.stage.gate"), content_filter: t("run.stage.content"),
                    keypoint_judge: t("run.stage.judge"), debate: t("run.stage.debate") };
    const fmt = (n) => n.toLocaleString("en-US");
    const keys = Object.keys(tbl);
    const rows = keys.map((k) => {
      const c = tbl[k];
      const hr = c.prompt ? Math.round((c.hit / c.prompt) * 100) : 0;
      return (
        "<tr><td>" + (label[k] || k) + "</td><td>" + fmt(c.calls) + "</td><td>" + fmt(c.prompt) +
        "</td><td>" + fmt(c.hit) + "</td><td>" + fmt(c.miss) + "</td><td>" + fmt(c.completion) + "</td><td>" + hr + "%</td></tr>"
      );
    });
    if (keys.length) {
      const sum = (f) => keys.reduce((a, k) => a + tbl[k][f], 0);
      const P = sum("prompt");
      const H = sum("hit");
      rows.push(
        "<tr><td>" + esc(t("cost.total")) + "</td><td>" + fmt(sum("calls")) + "</td><td>" + fmt(P) + "</td><td>" + fmt(H) +
        "</td><td>" + fmt(sum("miss")) + "</td><td>" + fmt(sum("completion")) + "</td><td>" +
        (P ? Math.round((H / P) * 100) : 0) + "%</td></tr>"
      );
    }
    area.innerHTML =
      '<div class="cost-cap">' + esc(t("cost.cap")) + "</div>" +
      '<div class="cost-table-wrap"><table class="cost-table"><thead><tr><th>' + esc(t("cost.stage")) + "</th><th>" + esc(t("cost.calls")) +
      "</th><th>" + esc(t("cost.input")) + "</th><th>" + esc(t("cost.hit")) + "</th>" +
      "<th>" + esc(t("cost.miss")) + "</th><th>" + esc(t("cost.output")) + "</th><th>" + esc(t("cost.hitrate")) + "</th></tr></thead><tbody>" +
      rows.join("") +
      "</tbody></table></div>";
  }

  function onCost(ev) {
    run.costTable = ev.table || {};
    renderCost($("cost-area"), run.costTable);
  }

  function onDone() {
    setStageAllDone();
    run.inProgress = false;
    // 后端流水线收尾已落库、也写回了记忆，这里刷新历史和记忆两个按钮的计数
    refreshHistoryCount();
    refreshMemoryCount();
  }

  // ── 查询历史：数据全在后端 SQLite，前端按需 fetch ──
  function fmtTime(ts) {
    const d = new Date(ts);
    const pad = (n) => String(n).padStart(2, "0");
    return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
      pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  // 带年份的完整时间，给历史详情顶部的浮窗用
  function fmtTimeFull(ts) {
    const d = new Date(ts);
    return d.getFullYear() + "-" + fmtTime(ts);
  }

  async function loadHistory() {
    try {
      const res = await fetch("/history");
      if (!res.ok) return [];
      return (await res.json()).items || [];
    } catch (e) {
      return [];
    }
  }

  // 刷新 HUD 历史按钮的计数与显隐，页面加载、跑完一次、删除后都调
  async function refreshHistoryCount() {
    const items = await loadHistory();
    historyCount = items.length;
    updateHistoryBtn();
  }

  // ── 仓库列表（Vault 大厅复用）──────────────────
  async function loadMemory() {
    try {
      const res = await fetch("/memory");
      if (!res.ok) return [];
      return (await res.json()).items || [];
    } catch (e) {
      return [];
    }
  }

  // 刷新 HUD 记忆按钮的计数与显隐，页面加载、跑完一次、删除后都调
  async function refreshMemoryCount() {
    const items = await loadMemory();
    memoryCount = items.length;
    updateHistoryBtn();
  }

  function memCardHTML(m) {
    // 有拆解的挂绿标、被跳过的挂灰标；标题就是仓库全名，副行给 tags 和最后搜到时间
    const tag = m.has_dissection
      ? '<span class="hist-proc-tag">' + esc(t("mem.tag.dissected")) + "</span>"
      : '<span class="rcard-skip-tag">' + esc(t("mem.tag.skipped")) + "</span>";
    const tags = (m.tags || []).map((x) => '<span class="qu-chip">' + esc(x) + "</span>").join("");
    return (
      '<div class="hist-card" data-fn="' + esc(m.full_name) + '">' +
      '<button class="hist-del" data-fn="' + esc(m.full_name) + '" title="' + esc(t("confirm.delmem.title")) + '">×</button>' +
      '<div class="hist-card-head">' +
      tag +
      '<span class="hist-count">🌟 ' + fmtStars(m.stars) + " · " + toMB(m.size) + "MB</span>" +
      "</div>" +
      '<div class="hist-query">' + esc(m.full_name) + "</div>" +
      (m.description ? '<div class="mem-desc">' + esc(m.description) + "</div>" : "") +
      (tags ? '<div class="proc-chips">' + tags + "</div>" : "") +
      '<div class="hist-meta"><span class="hist-top">' + esc(t("mem.lastseen")) + fmtTime(m.last_seen) + "</span></div>" +
      "</div>"
    );
  }

  // 结果页/Vault 搜索记录展开共用的表头：时间 + 仓库数 + keypoint 子弹清单 + 查询词 chips
  function headerMetaHTML(ts, total, keypoints, queries) {
    const kpBullets = (keypoints || []).length
      ? '<ul class="hd-kp-list">' + keypoints.map((k) => "<li>" + esc(k) + "</li>").join("") + "</ul>"
      : "";
    const qChips = (queries || []).length
      ? '<div class="diss-sec">' + esc(t("proc.sec.queries")) + "</div>" +
        '<div class="proc-chips">' +
        queries.map((q) => '<span class="qu-chip">' + esc(typeof q === "string" ? q : (q.q || "")) + "</span>").join("") +
        "</div>"
      : "";
    return (
      '<div class="hd-meta-label">' +
        '<span class="hd-badge">' + esc(fmtTimeFull(ts)) + "</span>" +
        '<span class="hd-count">' + (total || 0) + " " + esc(t("hist.repos")) + "</span>" +
      "</div>" +
      kpBullets + qChips
    );
  }

  // 通用确认弹窗，返回 Promise<{ok, dontAsk}>。showDontAsk 控制要不要露「下次不再提示」
  function openConfirm(opt) {
    return new Promise((resolve) => {
      const modal = $("confirm-modal");
      $("confirm-title").textContent = opt.title || t("confirm.default.title");
      $("confirm-msg").textContent = opt.message || "";
      $("confirm-ok").textContent = opt.confirmLabel || t("confirm.default.ok");
      const chk = $("confirm-dontask");
      chk.checked = false;
      $("confirm-dontask-row").hidden = !opt.showDontAsk;
      modal.hidden = false;

      function cleanup() {
        modal.hidden = true;
        $("confirm-ok").removeEventListener("click", onOk);
        $("confirm-cancel").removeEventListener("click", onCancel);
        modal.removeEventListener("click", onBackdrop);
      }
      function onOk() {
        const d = chk.checked;
        cleanup();
        resolve({ ok: true, dontAsk: d });
      }
      function onCancel() {
        cleanup();
        resolve({ ok: false, dontAsk: false });
      }
      function onBackdrop(e) {
        if (e.target === modal) onCancel();
      }
      $("confirm-ok").addEventListener("click", onOk);
      $("confirm-cancel").addEventListener("click", onCancel);
      modal.addEventListener("click", onBackdrop);
    });
  }

  // ── Vault：搜索记录（左）+ 仓库大厅/拆解详情（右），右栏点开详情扩宽 ──
  // 右栏从大厅切到拆解详情：藏大厅、露详情和返回钮、加 expanded 让右栏丝滑扩宽
  function vaultShowDetail() {
    $("vault-hall").hidden = true;
    $("vault-detail").hidden = false;
    $("vault-back").hidden = false;
    $("vault").classList.add("expanded");
  }
  // 收回大厅：藏详情、露大厅、去掉返回钮和 expanded，宽度复位五五开，标题回大厅名
  function vaultShowHall() {
    $("vault-detail").hidden = true;
    $("vault-hall").hidden = false;
    $("vault-back").hidden = true;
    $("vault").classList.remove("expanded");
    $("vault-right-title").textContent = t("vault.hall");
  }

  // 左栏搜索记录：列表摘要拿 loadHistory（/history）现成的，展开某条才拉 /history/{id} 详情
  let vaultRuns = [];

  async function loadVaultRuns() {
    vaultRuns = await loadHistory();
  }

  // 一条搜索记录的折叠卡：时间 + 那次需求 + 命中仓库数 + 删除。展开体懒加载，先留空
  function vaultRunCardHTML(h) {
    return (
      '<div class="vault-run" data-id="' + h.id + '">' +
      '<div class="vault-run-head">' +
      '<span class="vault-run-time">' + fmtTime(h.ts) + "</span>" +
      '<span class="vault-run-kp">' +
        esc(h.keypoints && h.keypoints.length ? h.keypoints.join(" · ") : (h.query || t("hist.empty"))) +
      "</span>" +
      '<span class="vault-run-count">' + h.total + "</span>" +
      '<button class="hist-del vault-del" title="' + esc(t("confirm.delquery.title")) + '">×</button>' +
      "</div>" +
      '<div class="vault-run-body" hidden></div>' +
      "</div>"
    );
  }

  // 展开体里一个命中仓库的行：仓库名 + 命中数（跳过的标跳过）+ 跳转到右栏看拆解
  function vaultRepoRowHTML(r) {
    const hit = r.skipped
      ? '<span class="vru-hit">' + esc(t("mem.tag.skipped")) + "</span>"
      : '<span class="vru-hit">' + (r.keypoint_hits || 0) + "/" + (r.keypoint_total || 0) + "</span>";
    return (
      '<div class="vault-run-repo">' +
      '<span class="vru-name">' + esc(r.full_name) + "</span>" +
      hit +
      '<button class="vru-jump" data-fn="' + esc(r.full_name) + '">' + esc(t("vault.jump")) + "</button>" +
      "</div>"
    );
  }

  // 填一条搜索记录的展开体：那次的元数据（keypoints/查询词）+ 命中仓库清单 + 花费明细
  function renderVaultRunBody(bodyEl, detail) {
    const p = detail.process || {};
    bodyEl.innerHTML =
      headerMetaHTML(detail.ts, detail.total, detail.keypoints, p.queries) +
      '<div class="vault-run-repos">' + (detail.ranked || []).map(vaultRepoRowHTML).join("") + "</div>" +
      '<div class="vault-run-cost"></div>';
    renderCost(bodyEl.querySelector(".vault-run-cost"), detail.cost);
    // 每个仓库的「查看拆解」跳到右栏，别冒泡去触发卡片折叠
    Array.from(bodyEl.querySelectorAll(".vru-jump")).forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        vaultOpenRepo(b.dataset.fn);
      });
    });
  }

  // 展开/收起一条搜索记录，第一次展开才拉 /history/{id} 填展开体，之后不重复请求
  async function vaultToggleRun(id, cardEl) {
    const body = cardEl.querySelector(".vault-run-body");
    const open = cardEl.classList.toggle("open");
    body.hidden = !open;
    if (open && !body.dataset.loaded) {
      body.innerHTML = '<div class="history-empty">…</div>';
      try {
        const res = await fetch("/history/" + id);
        if (res.ok) {
          renderVaultRunBody(body, await res.json());
          body.dataset.loaded = "1";
        }
      } catch (e) {
        /* 拉不到就留个空展开体，下次展开再试 */
      }
    }
  }

  // 把搜索记录卡渲染进左栏并绑事件（点头折叠、× 删整条），openVault 和切语言时都调
  function renderVaultRuns() {
    const list = $("vault-runs");
    const items = vaultRuns;
    $("btn-vault-runs-clear").hidden = items.length === 0;
    if (!items.length) {
      list.innerHTML = '<div class="history-empty">' + esc(t("hist.none")) + "</div>";
      return;
    }
    list.innerHTML = items.map(vaultRunCardHTML).join("");
    Array.from(list.querySelectorAll(".vault-run")).forEach((card) => {
      const id = parseInt(card.dataset.id, 10);
      card.querySelector(".vault-run-head").addEventListener("click", () => vaultToggleRun(id, card));
      card.querySelector(".vault-del").addEventListener("click", (e) => {
        e.stopPropagation();
        vaultDeleteRun(id);
      });
    });
  }

  async function vaultDeleteRun(id) {
    const r = await openConfirm({
      title: t("confirm.delquery.title"),
      message: t("confirm.delquery.msg"),
      confirmLabel: t("confirm.delete"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/history/" + id, { method: "DELETE" });
    await loadVaultRuns();
    renderVaultRuns();
    refreshHistoryCount();
  }

  async function vaultClearRuns() {
    const r = await openConfirm({
      title: t("confirm.clear.title"),
      message: t("confirm.clear.msg"),
      confirmLabel: t("confirm.clear.ok"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/history", { method: "DELETE" });
    await loadVaultRuns();
    renderVaultRuns();
    refreshHistoryCount();
  }

  // 右栏仓库大厅：所有存过的仓库卡，复用记忆页的 memCardHTML，点卡进拆解详情
  let vaultHallItems = [];

  async function loadVaultHall() {
    vaultHallItems = await loadMemory();
  }

  function renderVaultHall() {
    const list = $("vault-hall");
    const items = vaultHallItems;
    $("btn-vault-hall-clear").hidden = items.length === 0;
    if (!items.length) {
      list.innerHTML = '<div class="history-empty">' + esc(t("mem.none")) + "</div>";
      return;
    }
    list.innerHTML = items.map(memCardHTML).join("");
    Array.from(list.querySelectorAll(".hist-card")).forEach((c) => {
      c.addEventListener("click", () => vaultOpenRepo(c.dataset.fn));
    });
    Array.from(list.querySelectorAll(".hist-del")).forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        vaultDeleteRepo(b.dataset.fn);
      });
    });
  }

  async function vaultDeleteRepo(fullName) {
    const r = await openConfirm({
      title: t("confirm.delmem.title"),
      message: t("confirm.delmem.msg.pre") + fullName + t("confirm.delmem.msg.post"),
      confirmLabel: t("confirm.delete"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/memory/repo?full_name=" + encodeURIComponent(fullName), { method: "DELETE" });
    await loadVaultHall();
    renderVaultHall();
    refreshMemoryCount();
  }

  async function vaultClearHall() {
    const r = await openConfirm({
      title: t("confirm.clearmem.title"),
      message: t("confirm.clearmem.msg"),
      confirmLabel: t("confirm.clearmem.ok"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/memory", { method: "DELETE" });
    await loadVaultHall();
    renderVaultHall();
    refreshMemoryCount();
  }

  // 当前右栏详情展示的仓库，切语言时拿它原地重渲染
  let vaultRepoEntry = null;

  // seen_runs 时间线：这仓库被哪几次搜到过，最近的排上面，每行时间 + 那次需求 + 命中数。
  // 带 run_id 的行可点开，拉那次这个仓库的 agent 轨迹；老数据没 run_id 的就纯展示
  function vaultSeenRunsHTML(seenRuns, fullName) {
    if (!seenRuns || !seenRuns.length) return "";
    const rows = seenRuns
      .slice()
      .reverse()
      .map((s) => {
        const kps = (s.keypoints || []).join(" · ");
        const clickable = s.run_id != null;
        const rowCls = "vault-tl-row" + (clickable ? " clickable" : "");
        const data = clickable ? ' data-run-id="' + s.run_id + '" data-fn="' + esc(fullName) + '"' : "";
        const caret = clickable ? '<span class="vault-tl-caret">+</span>' : "";
        return (
          '<div class="vault-tl-item">' +
          '<div class="' + rowCls + '"' + data + ">" +
          '<span class="vault-tl-time">' + fmtTime(s.ts) + "</span>" +
          '<span class="vault-tl-kp">' + esc(kps) + "</span>" +
          '<span class="vault-tl-hit">' + (s.hits || 0) + "/" + (s.total || 0) + "</span>" +
          caret +
          "</div>" +
          '<div class="vault-tl-trace"></div>' +
          "</div>"
        );
      })
      .join("");
    return '<div class="diss-sec">' + esc(t("vault.timeline")) + '</div><div class="vault-tl">' + rows + "</div>";
  }

  // 点开一条时间线：拉 /history/{run_id} 取那次的 ranked，筛出这个仓库那次的 trace 渲染出来。
  // 首次展开才请求，之后不重复；那次搜索被删了或这次没轨迹就给一句提示
  async function vaultToggleSeenTrace(row) {
    const item = row.parentElement;
    const box = item.querySelector(".vault-tl-trace");
    const caret = row.querySelector(".vault-tl-caret");
    const open = item.classList.toggle("open");
    if (caret) caret.textContent = open ? "−" : "+";
    if (!open || box.dataset.loaded) return;

    box.innerHTML = '<div class="history-empty">…</div>';
    try {
      const res = await fetch("/history/" + row.dataset.runId);
      if (!res.ok) {
        box.innerHTML = '<div class="history-empty">' + esc(t("vault.runGone")) + "</div>";
        return;
      }
      const run = await res.json();
      const hit = (run.ranked || []).find((r) => r.full_name === row.dataset.fn);
      const trace = (hit && hit.trace) || [];
      if (!trace.length) {
        box.innerHTML = '<div class="history-empty">' + esc(t("vault.noTrace")) + "</div>";
        return;
      }
      box.innerHTML = trace.map((l) => '<div class="log-line">' + esc(l) + "</div>").join("");
      box.dataset.loaded = "1";
    } catch (e) {
      box.innerHTML = '<div class="history-empty">' + esc(t("vault.runGone")) + "</div>";
    }
  }

  // 渲染右栏拆解详情：元数据头 + seen_runs 时间线 + 拆解本体（复用结果卡 renderRanked）
  function vaultRenderRepoDetail(m) {
    $("vault-right-title").textContent = m.full_name;
    const skipped = !m.dissection || !Object.keys(m.dissection).length;
    $("vault-detail").innerHTML =
      '<div class="hd-meta-label">' +
        '<span class="hd-badge">' + esc(t("mem.lastseen")) + fmtTimeFull(m.last_seen) + "</span>" +
        '<span class="hd-count">🌟 ' + fmtStars(m.stars) + " · " + toMB(m.size) + "MB · " + esc(m.language || "") + "</span>" +
      "</div>" +
      '<div class="hd-query-box">' + esc(m.full_name) + "</div>" +
      vaultSeenRunsHTML(m.seen_runs, m.full_name) +
      '<div class="vault-detail-diss"></div>';
    // 时间线每行点开看那次这个仓库的 agent 轨迹，首次展开才拉 /history
    Array.from($("vault-detail").querySelectorAll(".vault-tl-row.clickable")).forEach((row) => {
      row.addEventListener("click", () => vaultToggleSeenTrace(row));
    });
    // 把记忆行拼成结果卡认识的形状，没拆解的当跳过卡；不带单删按钮（删走大厅卡的 ×）
    const asRepo = {
      full_name: m.full_name, dissection: m.dissection || {}, stars: m.stars, size: m.size,
      read_files: m.read_files || [], skipped: skipped, reason: m.skip_note || "",
      keypoint_hits: 0, keypoint_total: 0, detail: { keypoints: [] },
    };
    renderRanked($("vault-detail").querySelector(".vault-detail-diss"), [asRepo], null);
  }

  // 点仓库卡或左栏「查看拆解」跳右栏：拉 /memory/repo 渲染拆解 + seen_runs，再扩宽右栏
  async function vaultOpenRepo(fullName) {
    let m;
    try {
      const res = await fetch("/memory/repo?full_name=" + encodeURIComponent(fullName));
      if (!res.ok) return;
      m = await res.json();
    } catch (e) {
      return;
    }
    vaultRepoEntry = m;
    vaultRenderRepoDetail(m);
    vaultShowDetail();
  }

  // 进 vault：默认落大厅态，左栏搜索记录、右栏仓库大厅现拉现渲染
  async function openVault() {
    vaultShowHall();
    await Promise.all([loadVaultRuns(), loadVaultHall()]);
    renderVaultRuns();
    renderVaultHall();
    showView("vault");
  }
  // 右上角「返回搜索」：从 vault 回工作台，按当前搜索状态还原（有结果看结果、在跑看进度、否则搜索输入）
  function vaultBackToSearch() {
    if (run && run.ranked && run.ranked.length) {
      renderWorkspaceResults(run.ranked);
      showWsPane("list");
    } else if (run && run.inProgress) {
      showWsPane("progress");
    } else {
      showWsPane("search");
    }
    showView("workspace");
  }
  $("btn-vault").addEventListener("click", openVault);
  $("btn-vault-search").addEventListener("click", vaultBackToSearch);
  $("vault-back").addEventListener("click", vaultShowHall);
  $("btn-vault-runs-clear").addEventListener("click", vaultClearRuns);
  $("btn-vault-hall-clear").addEventListener("click", vaultClearHall);

  // 语言切换钮接线，首次按存的 lang（默认简体中文）刷一遍文案
  document.querySelectorAll("#lang-toggle .lang-opt").forEach((b) => {
    b.addEventListener("click", () => setLang(b.dataset.lang));
  });
  applyLang();

  // 给当前起始页（凭证页）压一条基准记录，后退到底有地方落
  history.replaceState({ view: currentView, id: null }, "");
  refreshHistoryCount();
  refreshMemoryCount();
  prefillCreds();

  function onError(ev) {
    // 出错也算搜索结束，别让后退/前进以后一直卡在进度页
    run.inProgress = false;
    showErr("run-err", t("err.prefix") + (ev.message || t("err.unknown")));
  }

  // ── 开发者监控抽屉：看每轮对话背后的 extract/notes/压缩过程 ──────────
  // 自成一体，不 hook 别的函数：抽屉开着时 1 秒轮询 wsSessionId，变了就重连流+刷快照
  let devAbort = null;
  let devSid = null;
  let devPoll = null;

  function devEsc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function devJson(o) { try { return JSON.stringify(o, null, 2); } catch (e) { return String(o); } }
  function devFold(label, body) {
    return '<details class="dev-sec"><summary>' + devEsc(label) +
      '</summary><div class="dev-pre">' + devEsc(body) + "</div></details>";
  }

  function openDev() {
    $("dev-drawer").hidden = false;
    devReconnect();
    if (!devPoll) devPoll = setInterval(() => { if (wsSessionId !== devSid) devReconnect(); }, 1000);
  }
  function closeDev() {
    $("dev-drawer").hidden = true;
    if (devAbort) { devAbort.abort(); devAbort = null; }
    if (devPoll) { clearInterval(devPoll); devPoll = null; }
    devSid = null;
    // 复位回实时视图，下次开抽屉从实时开始，不停在审计态
    $("dev-audit").hidden = true;
    $("dev-live").hidden = false;
    auditSid = null;
  }

  async function devReconnect() {
    if (devAbort) { devAbort.abort(); devAbort = null; }
    devSid = wsSessionId;
    $("dev-feed").innerHTML = "";
    $("dev-state").innerHTML = "";
    if (!devSid) { $("dev-hint").textContent = "发一条消息开始对话后，这里自动开始监控。"; return; }
    $("dev-hint").textContent = "监控本会话 " + devSid + "。开关关着则不记录。";
    try {
      const snap = await (await fetch("/chat/dev/" + encodeURIComponent(devSid))).json();
      $("dev-toggle").checked = !!snap.enabled;
      renderDevState(snap);
      (snap.records || []).forEach(renderDevRecord);
    } catch (e) {}
    devStream(devSid);
  }

  async function devStream(sid) {
    const ac = new AbortController();
    devAbort = ac;
    try {
      const res = await fetch("/chat/dev/" + encodeURIComponent(sid) + "/stream", { signal: ac.signal });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let m; const sep = /\r?\n\r?\n/;
        while ((m = sep.exec(buf))) {
          const frame = buf.slice(0, m.index); buf = buf.slice(m.index + m[0].length);
          const line = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let rec; try { rec = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
          renderDevRecord(rec);
          try { renderDevState(await (await fetch("/chat/dev/" + encodeURIComponent(sid))).json()); } catch (e) {}
        }
      }
    } catch (e) {}
  }

  function renderDevState(snap) {
    const note = (snap.note && snap.note.notes) || "";
    const sm = snap.session_memories || [];
    const gm = snap.general_memories || [];
    const memLine = (x) => '<div class="dev-meta">· [' + devEsc(x.type) + "] <b>" + devEsc(x.name) +
      "</b>：" + devEsc(x.content || "") + "</div>";
    $("dev-state").innerHTML =
      '<div class="dev-state-h">当前会话笔记</div>' +
      (note ? '<div class="dev-pre">' + devEsc(note) + "</div>" : '<div class="dev-skip">（还没建笔记，对话够长才建）</div>') +
      '<div class="dev-state-h">本会话提取的记忆 (' + sm.length + ")</div>" +
      (sm.length ? sm.map(memLine).join("") : '<div class="dev-skip">（暂无）</div>') +
      '<div class="dev-state-h">全局通用记忆 (' + gm.length + ")</div>" +
      (gm.length ? gm.map(memLine).join("") : '<div class="dev-skip">（暂无）</div>');
  }

  function renderDevRecord(rec, feed) {
    feed = feed || $("dev-feed");
    const card = document.createElement("div");
    card.className = "dev-card";
    let html = '<span class="dev-card-kind ' + devEsc(rec.kind) + '">' + devEsc(rec.kind) + "</span>";
    if (rec.kind === "context") {
      const tk = rec.tokens || {};
      html += '<div class="dev-meta"><b>' + devEsc(rec.model) + "</b> · " + (rec.elapsed_ms || 0) + "ms · " +
        "prompt <b>" + (tk.prompt != null ? tk.prompt : "?") + "</b> / 出 " + (tk.completion != null ? tk.completion : "?") +
        " / 总 " + (tk.total != null ? tk.total : "?") +
        (tk.cache_hit != null ? " · 缓存命中 <b>" + tk.cache_hit + "</b>/未命中 " + tk.cache_miss : "") + "</div>";
      html += devFold("发给模型的完整 payload", devJson(rec.payload));
    } else if (rec.kind === "extract") {
      if (!rec.fired) {
        html += '<div class="dev-skip">未提取：' + devEsc(rec.reason) + "</div>";
      } else {
        const st = rec.stat || {};
        html += '<div class="dev-meta">落库 +' + (st.added || 0) + " ~" + (st.updated || 0) + " -" + (st.deleted || 0) +
          " · 打码 " + (st.redacted || 0) + " · 编造丢弃 " + ((rec.dropped || []).length) + "</div>";
        html += devFold("提取提示词（含拼接）", devJson(rec.prompt));
        html += devFold("模型原始返回", rec.raw || "");
        html += devFold("解析出的动作", devJson(rec.actions));
        if ((rec.dropped || []).length) html += devFold("被丢弃的（编造的仓库）", devJson(rec.dropped));
      }
    } else if (rec.kind === "notes") {
      if (!rec.fired) {
        html += '<div class="dev-skip">未更新笔记：' + devEsc(rec.reason) + "</div>";
      } else {
        html += '<div class="dev-meta">覆盖到第 <b>' + rec.notes_cursor + "</b> 条 · " + rec.notes_tokens + " tok</div>";
        html += devFold("笔记提示词（含拼接）", devJson(rec.prompt));
        html += '<div class="dev-pre">' + devEsc(rec.notes || "") + "</div>";
      }
    } else if (rec.kind === "compact") {
      const path = { note_replace: "笔记顶替", summary_fallback: "摘要兜底",
        hard_truncate_circuit: "熔断硬截断", hard_truncate_failed: "摘要失败·硬截断",
        reactive_truncate: "模型拒绝后硬截" }[rec.path] || rec.path;
      html += '<div class="dev-meta">压缩：<b>' + devEsc(path) + "</b> · " + (rec.before || []).length +
        " 条压缩为 " + (rec.after || []).length + " 条 · 熔断计数 " + (rec.fail_count || 0) + "</div>";
      html += devFold("压缩前", devJson(rec.before));
      html += devFold("压缩后", devJson(rec.after));
    } else if (rec.kind === "recall") {
      if (!rec.fired) {
        html += '<div class="dev-skip">未召回：' + devEsc(rec.reason) + "</div>";
      } else {
        html += '<div class="dev-meta">清单 <b>' + (rec.manifest || []).length + "</b> 条 · 命中仓库 " +
          (rec.hit_repos || []).length + " · 命中记忆 " + (rec.hit_memories || []).length +
          " · 候选 " + (rec.ambiguous || []).length + "</div>";
        html += devFold("给挑选器的清单", devJson(rec.manifest));
        html += devFold("模型原始返回", rec.raw || "");
        html += devFold("挑中结果", devJson({ picks: rec.picks, ambiguous: rec.ambiguous,
          hit_repos: rec.hit_repos, hit_memories: rec.hit_memories }));
      }
    }
    card.innerHTML = html;
    feed.appendChild(card);
    feed.scrollTop = feed.scrollHeight;
  }

  $("ws-dev-btn")?.addEventListener("click", openDev);
  $("dev-close")?.addEventListener("click", closeDev);
  $("dev-toggle")?.addEventListener("change", (e) => {
    fetch("/chat/dev", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: e.target.checked }) }).catch(() => {});
  });

  // ── 审计视图：看落库的历史记录，重启后仍在 ──────────
  // auditSid 非空表示正看某会话的记录，空表示在会话列表。返回钮据此决定退到列表还是退回实时
  let auditSid = null;

  function openAudit() {
    $("dev-live").hidden = true;
    $("dev-audit").hidden = false;
    showAuditList();
  }
  // 退回实时视图，关抽屉和点返回都复位到这
  function backToLive() {
    $("dev-audit").hidden = true;
    $("dev-live").hidden = false;
    auditSid = null;
  }
  // 审计里回到会话列表那一层
  function showAuditList() {
    auditSid = null;
    $("dev-audit-list").hidden = false;
    $("dev-audit-feed").hidden = true;
    $("dev-audit-feed").innerHTML = "";
    $("dev-audit-back").textContent = "返回实时";
    $("dev-audit-clear").hidden = false;
    loadAuditList();
  }

  async function loadAuditList() {
    let items = [];
    try { items = (await (await fetch("/chat/dev/audit")).json()).items || []; } catch (e) {}
    const list = $("dev-audit-list");
    if (!items.length) {
      list.innerHTML = '<div class="dev-skip">（暂无审计记录，打开监控开关后聊天才会落库）</div>';
      return;
    }
    list.innerHTML = "";
    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "dev-audit-row";
      row.innerHTML =
        '<span class="dev-audit-sid">' + devEsc(it.session_id) + "</span>" +
        '<span class="dev-audit-meta">' + it.count + " 条 · " + fmtTime(it.last_ts) + "</span>" +
        '<button class="dev-audit-del" title="删除">×</button>';
      row.addEventListener("click", () => openAuditSession(it.session_id));
      row.querySelector(".dev-audit-del").addEventListener("click", async (e) => {
        e.stopPropagation();
        const r = await openConfirm({
          title: "删除审计", message: "删除这次会话的审计记录？此操作无法撤销。",
          confirmLabel: "删除", showDontAsk: false,
        });
        if (!r.ok) return;
        await fetch("/chat/dev/audit/" + encodeURIComponent(it.session_id), { method: "DELETE" });
        loadAuditList();
      });
      list.appendChild(row);
    });
  }

  // 点一次会话：拉它落库的完整记录，复用 renderDevRecord 渲染进审计 feed
  async function openAuditSession(sid) {
    auditSid = sid;
    $("dev-audit-list").hidden = true;
    const feed = $("dev-audit-feed");
    feed.hidden = false;
    feed.innerHTML = "";
    $("dev-audit-back").textContent = "返回列表";
    $("dev-audit-clear").hidden = true;
    let records = [];
    try { records = (await (await fetch("/chat/dev/audit/" + encodeURIComponent(sid))).json()).records || []; } catch (e) {}
    if (!records.length) { feed.innerHTML = '<div class="dev-skip">（无记录）</div>'; return; }
    records.forEach((rec) => renderDevRecord(rec, feed));
  }

  $("dev-audit-btn")?.addEventListener("click", openAudit);
  $("dev-audit-back")?.addEventListener("click", () => { if (auditSid) showAuditList(); else backToLive(); });
  $("dev-audit-clear")?.addEventListener("click", async () => {
    const r = await openConfirm({
      title: "清空审计", message: "清空全部审计记录？此操作无法撤销。",
      confirmLabel: "清空", showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/chat/dev/audit", { method: "DELETE" });
    loadAuditList();
  });

  window.RepoHunter = { state: state, run: run, collectParams: collectParams, showView: showView };
})();
