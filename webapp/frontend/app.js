(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // 本次会话状态：凭证 + 运行时各阶段数据，刷新即清，不落盘
  const state = {
    creds: { apikey: "", pat: "", model: "" },
    params: null,
  };
  const run = {};

  // 右栏对话：注入的 repo 全名（跟 ws-ctx 芯片同步）和多轮对话历史，刷新即清
  const wsContext = [];
  const wsMessages = [];
  // 本次对话的 session id，第一次发消息时生成，同一次对话里不变，存对话时用它 upsert
  let wsSessionId = null;
  // 一次只允许一条在途请求，发送中禁掉发送钮，避免叠流
  let wsSending = false;
  // 齿轮里单独设的对话 api/model，只覆盖对话；留空则回退凭证页的 state.creds
  const chatOverride = { apikey: "", model: "" };

  // 左栏当前在哪个 tab（repo 结果三态 / chat 会话列表），以及切回 repo 时该还原到哪个子态
  let leftTab = "repo";
  let lastRepoPane = "search";
  // 最近一次加载的会话列表，切换语言或重渲染时用，不用每次都重新 fetch
  let lastSessions = [];

  // 当前打开的历史详情完整数据，单删 repo 时拿它的 id 调接口、本地改它再重渲染
  let detailEntry = null;

  // 当前打开的记忆详情完整数据，删记忆时拿它的 full_name 调接口
  let memEntry = null;

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
      "setup.step": "第 01 步 · 凭证",
      "setup.title": "访问凭证",
      "setup.apikey": "DeepSeek API 密钥",
      "setup.model": "模型",
      "setup.pat": "GitHub PAT",
      "setup.getpat": "去申请 ↗",
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
      "run.stage.qu": "意图理解",
      "run.stage.search": "检索",
      "run.stage.gate": "初筛",
      "run.stage.content": "Content Filter",
      "run.stage.judge": "判分",
      "run.stage.debate": "辩论裁决",
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
      "results.restart": "↺ 新搜索",
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
      "setup.step": "Step 01 · Credentials",
      "setup.title": "Access Credentials",
      "setup.apikey": "DeepSeek API Key",
      "setup.model": "Model",
      "setup.pat": "GitHub PAT",
      "setup.getpat": "Get one ↗",
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
      "run.stage.qu": "Translate",
      "run.stage.search": "Search",
      "run.stage.gate": "Gate",
      "run.stage.content": "Deep Dive",
      "run.stage.judge": "Judge",
      "run.stage.debate": "Debate",
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
      "results.restart": "↺ New Search",
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
    } else if (currentView === "history") {
      renderHistoryList();
    } else if (currentView === "history-detail" && detailEntry) {
      renderDetail();
    } else if (currentView === "memory") {
      renderMemoryList();
    } else if (currentView === "workspace" && leftTab === "chat") {
      renderSessions();
    } else if (currentView === "memory-detail" && memEntry) {
      renderMemDetail();
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
                        history: "HISTORY", "history-detail": "HISTORY",
                        memory: "MEMORY", "memory-detail": "MEMORY", workspace: "WORKSPACE" };

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
    const st = { view: name, id: name === "history-detail" && detailEntry ? detailEntry.id : null,
                 fn: name === "memory-detail" && memEntry ? memEntry.full_name : null };
    if (name === "results") history.replaceState(st, "");
    else history.pushState(st, "");
  }

  // 侧键/浏览器前进后退落到某条记录，按记录里的视图名还原，缺数据就退到最近的可用页
  async function restoreView(st) {
    let name = (st && st.view) || "setup";
    if (name === "history-detail") {
      if (st.id == null) {
        name = "history";
      } else if (!detailEntry || detailEntry.id !== st.id) {
        try {
          const res = await fetch("/history/" + st.id);
          if (res.ok) detailEntry = await res.json();
          else name = "history";
        } catch (e) {
          name = "history";
        }
      }
      if (name === "history-detail") renderDetail();
    }
    if (name === "memory-detail") {
      if (st.fn == null) {
        name = "memory";
      } else if (!memEntry || memEntry.full_name !== st.fn) {
        try {
          const res = await fetch("/memory/repo?full_name=" + encodeURIComponent(st.fn));
          if (res.ok) memEntry = await res.json();
          else name = "memory";
        } catch (e) {
          name = "memory";
        }
      }
      if (name === "memory-detail") renderMemDetail();
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

  // 停在凭证页（还没接入）一律不给看历史，免得从历史返回绕过凭证；进了且有记录才显示
  function updateHistoryBtn() {
    $("history-count").textContent = historyCount;
    $("memory-count").textContent = memoryCount;
    // setup 页没凭证不给看，已经在自己那类页里了也不用再显示自己这个入口
    const inHistory = currentView === "history" || currentView === "history-detail";
    const inMemory = currentView === "memory" || currentView === "memory-detail";
    $("btn-history").hidden = currentView === "setup" || inHistory || historyCount === 0;
    $("btn-memory").hidden = currentView === "setup" || inMemory || memoryCount === 0;
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
    } catch (e) {}
  }

  // 进入时把这次的 api/pat/模型存到本地，改了就覆盖，下次自动带出来。不挡进入
  function saveCreds(apikey, pat, model) {
    fetch("/creds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deepseek_api_key: apikey, github_pat: pat, model: model }),
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
    state.creds = { apikey, pat, model };
    saveCreds(apikey, pat, model);
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
    // 模型在凭证页填一个，两阶段统一用它；留空则后端走 config 默认（全 pro）
    const model = state.creds.model || "";
    return {
      deepseek_api_key: state.creds.apikey,
      github_pat: state.creds.pat,
      qu_model: model,
      content_model: model,
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
    if (tab === "chat") {
      ["ws-search", "ws-progress", "ws-list"].forEach((id) => { $(id).hidden = true; });
      $("ws-sessions").hidden = false;
      $("ws").classList.remove("expanded");
      loadSessions();
    } else {
      $("ws-sessions").hidden = true;
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
  function addCtxChip(name) {
    const box = $("ws-ctx");
    box.querySelector(".ws-ctx-empty")?.remove();
    const chip = document.createElement("span");
    chip.className = "ws-ctx-chip";
    chip.dataset.ctx = name;
    chip.innerHTML = esc(name.split("/").pop()) + ' <button title="' + esc(t("form.langrow.del")) + '">×</button>';
    chip.querySelector("button").addEventListener("click", () => {
      chip.remove();
      const i = wsContext.indexOf(name);
      if (i >= 0) wsContext.splice(i, 1);
      if (!box.querySelector(".ws-ctx-chip")) {
        box.innerHTML = '<span class="ws-ctx-empty">' + esc(t("ws.ctx.empty")) + "</span>";
      }
    });
    box.appendChild(chip);
  }

  // 上下文芯片：加一个（去重）/ 点 ✕ 移除；空了显示占位。wsContext 数组跟芯片同步，发消息时带给后端
  function injectCtx(name) {
    const box = $("ws-ctx");
    if (box.querySelector('[data-ctx="' + CSS.escape(name) + '"]')) return;
    wsContext.push(name);
    addCtxChip(name);
  }

  // 从 wsContext 数组整排重建芯片（不改数组），切换会话时用：先清空框再逐个补
  function renderCtxChips() {
    const box = $("ws-ctx");
    box.innerHTML = wsContext.length ? "" : '<span class="ws-ctx-empty">' + esc(t("ws.ctx.empty")) + "</span>";
    wsContext.forEach(addCtxChip);
  }

  // ── 右栏对话 ──────────────────────────────────
  // 往聊天区加一个气泡，返回气泡里放文本的节点，流式时往它 append 文字。role 决定左右对齐样式
  function appendBubble(role, text) {
    const chat = $("ws-chat");
    // 首条消息把空态占位清掉
    $("ws-chat-empty")?.remove();
    const wrap = document.createElement("div");
    wrap.className = "ws-msg ws-msg-" + role;
    const body = document.createElement("div");
    body.className = "ws-bubble";
    body.textContent = text || "";
    wrap.appendChild(body);
    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
    return body;
  }

  // 发一条消息：读输入框 + wsContext，POST /chat，SSE 逐 delta 追加进 AI 气泡
  async function sendChat() {
    if (wsSending) return;
    const input = $("ws-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    input.style.height = "auto";

    // 第一条消息时给这次对话生成一个 session id，标题拿它当占位，后面每轮都存进同一行
    if (!wsSessionId) {
      wsSessionId = "s_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    }

    // 用户气泡先上屏，存进多轮历史
    appendBubble("user", text);
    wsMessages.push({ role: "user", content: text });

    // AI 气泡先建空的，delta 来了往里填；发送中禁钮
    wsSending = true;
    $("ws-send").disabled = true;
    const bubble = appendBubble("assistant", "");
    bubble.classList.add("streaming");
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
          model: chatOverride.model || state.creds.model || "",
          messages: wsMessages,
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
          if (ev.type === "delta") {
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
      // 空回复（比如一上来就报错）不入历史，免得脏了后续多轮
      if (acc) wsMessages.push({ role: "assistant", content: acc });
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
    wsMessages.forEach((m) => appendBubble(m.role, m.content));
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

  // 编译出的判定标准回显：每条 keypoint 一行「原文 → 标准」，让用户看到系统怎么理解需求。
  // 标准空的（编译失败降级）不显示箭头和标准，只显示原文
  function onKeypointsCompiled(ev) {
    const box = $("kp-std-box");
    if (!box) return;
    const rows = (ev.compiled || []).map((c) => {
      const kp = '<span class="kp-std-kp">' + esc(c.keypoint) + "</span>";
      const std = c.standard
        ? '<span class="kp-std-arrow">→</span><span class="kp-std-txt">' + esc(c.standard) + "</span>"
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
      '<td class="c-name"><span class="c-caret">▸</span>' +
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
      tr.querySelector(".c-caret").textContent = open ? "▸" : "▾";
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
          '" target="_blank" rel="noopener">github.com/' + esc(r.full_name) + " ↗</a>" +
        (r.reason ? '<div class="diss-sec">' + esc(t("rcard.skipreason")) + '</div><div class="diss-skip">' + esc(r.reason) + "</div>" : "")
      : (d.purpose ? '<div class="diss-lead">' + esc(d.purpose) + "</div>" : "") +
        '<a class="repo-link" href="https://github.com/' + esc(r.full_name) +
          '" target="_blank" rel="noopener">github.com/' + esc(r.full_name) + " ↗</a>" +
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
      '<span class="rcard-caret">▶</span>' +
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
    const label = { query_understanding: t("run.stage.qu"), skip_gate: t("run.stage.gate"),
                    content_filter: t("run.stage.content"), keypoint_judge: t("run.stage.judge"),
                    debate: t("run.stage.debate") };
    const keys = Object.keys(tbl);
    const rows = keys.map((k) => {
      const c = tbl[k];
      const hr = c.prompt ? Math.round((c.hit / c.prompt) * 100) : 0;
      return (
        "<tr><td>" + (label[k] || k) + "</td><td>" + c.calls + "</td><td>" + c.prompt +
        "</td><td>" + c.hit + "</td><td>" + c.miss + "</td><td>" + c.completion + "</td><td>" + hr + "%</td></tr>"
      );
    });
    if (keys.length) {
      const sum = (f) => keys.reduce((a, k) => a + tbl[k][f], 0);
      const P = sum("prompt");
      const H = sum("hit");
      rows.push(
        "<tr><td>" + esc(t("cost.total")) + "</td><td>" + sum("calls") + "</td><td>" + P + "</td><td>" + H +
        "</td><td>" + sum("miss") + "</td><td>" + sum("completion") + "</td><td>" +
        (P ? Math.round((H / P) * 100) : 0) + "%</td></tr>"
      );
    }
    area.innerHTML =
      '<div class="cost-cap">' + esc(t("cost.cap")) + "</div>" +
      '<table class="cost-table"><thead><tr><th>' + esc(t("cost.stage")) + "</th><th>" + esc(t("cost.calls")) +
      "</th><th>" + esc(t("cost.input")) + "</th><th>" + esc(t("cost.hit")) + "</th>" +
      "<th>" + esc(t("cost.miss")) + "</th><th>" + esc(t("cost.output")) + "</th><th>" + esc(t("cost.hitrate")) + "</th></tr></thead><tbody>" +
      rows.join("") +
      "</tbody></table>";
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

  function histCardHTML(h) {
    const top = h.top_name || t("hist.noresults");
    return (
      '<div class="hist-card" data-id="' + h.id + '">' +
      '<button class="hist-del" data-id="' + h.id + '" title="' + esc(t("confirm.delquery.title")) + '">×</button>' +
      '<div class="hist-card-head">' +
      '<span class="hist-time">' + fmtTime(h.ts) + "</span>" +
      '<span class="hist-count">' + h.total + " " + esc(t("hist.repos")) + "</span>" +
      "</div>" +
      '<div class="hist-query">' +
        esc(h.keypoints && h.keypoints.length ? h.keypoints.join(" · ") : (h.query || t("hist.empty"))) +
      "</div>" +
      '<div class="hist-meta"><span class="hist-top">' + esc(t("hist.top")) + esc(top) + "</span></div>" +
      "</div>"
    );
  }

  // 最近一次加载的历史列表，切换语言时拿它原地重渲染，不重新请求
  let lastHistoryItems = [];

  // 把历史卡渲染进列表并绑事件，openHistory 和切换语言时都调
  function renderHistoryList() {
    const items = lastHistoryItems;
    const list = $("history-list");
    $("btn-history-clear").hidden = items.length === 0;
    if (!items.length) {
      list.innerHTML = '<div class="history-empty">' + esc(t("hist.none")) + "</div>";
      return;
    }
    list.innerHTML = items.map(histCardHTML).join("");
    Array.from(list.querySelectorAll(".hist-card")).forEach((c) => {
      c.addEventListener("click", () => openHistoryDetail(parseInt(c.dataset.id, 10)));
    });
    Array.from(list.querySelectorAll(".hist-del")).forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteWholeQuery(parseInt(b.dataset.id, 10));
      });
    });
  }

  async function openHistory() {
    lastHistoryItems = await loadHistory();
    renderHistoryList();
    showView("history");
  }

  async function deleteWholeQuery(id) {
    // 删整条必须确认，没有不再提示
    const r = await openConfirm({
      title: t("confirm.delquery.title"),
      message: t("confirm.delquery.msg"),
      confirmLabel: t("confirm.delete"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/history/" + id, { method: "DELETE" });
    await openHistory();
    refreshHistoryCount();
  }

  async function clearAllHistory() {
    // 清空全部也必须确认
    const r = await openConfirm({
      title: t("confirm.clear.title"),
      message: t("confirm.clear.msg"),
      confirmLabel: t("confirm.clear.ok"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/history", { method: "DELETE" });
    await openHistory();
    refreshHistoryCount();
  }

  // ── 项目记忆 ──────────────────────────────────
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

  // 最近一次加载的记忆列表，切换语言时拿它原地重渲染，不重新请求
  let lastMemoryItems = [];

  function renderMemoryList() {
    const items = lastMemoryItems;
    const list = $("memory-list");
    $("btn-memory-clear").hidden = items.length === 0;
    if (!items.length) {
      list.innerHTML = '<div class="history-empty">' + esc(t("mem.none")) + "</div>";
      return;
    }
    list.innerHTML = items.map(memCardHTML).join("");
    Array.from(list.querySelectorAll(".hist-card")).forEach((c) => {
      c.addEventListener("click", () => openMemDetail(c.dataset.fn));
    });
    Array.from(list.querySelectorAll(".hist-del")).forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteMemory(b.dataset.fn);
      });
    });
  }

  async function openMemory() {
    lastMemoryItems = await loadMemory();
    renderMemoryList();
    showView("memory");
  }

  async function openMemDetail(fullName) {
    try {
      const res = await fetch("/memory/repo?full_name=" + encodeURIComponent(fullName));
      if (!res.ok) return;
      memEntry = await res.json();
    } catch (e) {
      return;
    }
    renderMemDetail();
    showView("memory-detail");
  }

  function renderMemDetail() {
    const m = memEntry;
    // seen_queries 是一条条需求清单，每条清单摊平成 chip，让人看这仓库被哪些需求搜到过
    const seen = (m.seen_queries || [])
      .map((q) => (Array.isArray(q) ? q : [q]))
      .flat()
      .map((x) => '<span class="qu-chip kp">' + esc(x) + "</span>")
      .join("");
    $("md-sub").innerHTML =
      '<div class="hd-meta-label">' +
        '<span class="hd-badge">' + esc(t("mem.lastseen")) + fmtTimeFull(m.last_seen) + "</span>" +
        '<span class="hd-count">🌟 ' + fmtStars(m.stars) + " · " + toMB(m.size) + "MB · " + esc(m.language || "") + "</span>" +
      "</div>" +
      '<div class="hd-query-box">' + esc(m.full_name) + "</div>" +
      (seen ? '<div class="diss-sec">' + esc(t("mem.seenq")) + '</div><div class="proc-chips">' + seen + "</div>" : "");
    // 复用结果卡渲染：把记忆行拼成 rcardHTML 认识的形状。没拆解的当跳过卡处理
    const skipped = !m.dissection || !Object.keys(m.dissection).length;
    const asRepo = {
      full_name: m.full_name, dissection: m.dissection || {}, stars: m.stars, size: m.size,
      read_files: m.read_files || [], skipped: skipped, reason: m.skip_note || "",
      keypoint_hits: 0, keypoint_total: 0, detail: { keypoints: [] },
    };
    // onDelete 传 null，记忆详情里的卡不带单删按钮（删整条走列表页的 ×）
    renderRanked($("md-list"), [asRepo], null);
  }

  async function deleteMemory(fullName) {
    const r = await openConfirm({
      title: t("confirm.delmem.title"),
      message: t("confirm.delmem.msg.pre") + fullName + t("confirm.delmem.msg.post"),
      confirmLabel: t("confirm.delete"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/memory/repo?full_name=" + encodeURIComponent(fullName), { method: "DELETE" });
    await openMemory();
    refreshMemoryCount();
  }

  async function clearAllMemory() {
    const r = await openConfirm({
      title: t("confirm.clearmem.title"),
      message: t("confirm.clearmem.msg"),
      confirmLabel: t("confirm.clearmem.ok"),
      showDontAsk: false,
    });
    if (!r.ok) return;
    await fetch("/memory", { method: "DELETE" });
    await openMemory();
    refreshMemoryCount();
  }

  async function openHistoryDetail(id) {
    try {
      const res = await fetch("/history/" + id);
      if (!res.ok) return;
      detailEntry = await res.json();
    } catch (e) {
      return;
    }
    renderDetail();
    showView("history-detail");
  }

  // 结果页/历史详情共用的表头：时间 + 仓库数 + keypoint 子弹清单 + 查询词 chips
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

  function renderDetail() {
    const h = detailEntry;
    const p = h.process || {};
    // keypoints 用独立列 h.keypoints（始终有），别用 p.keypoints（process 为 null 时会丢）；查询词只在 process 里
    $("hd-sub").innerHTML = headerMetaHTML(h.ts, h.total, h.keypoints, p.queries);
    renderRanked($("hd-list"), h.ranked, deleteRepoFromDetail);
    renderCost($("hd-cost"), h.cost);
  }

  async function deleteRepoFromDetail(fullName) {
    // 单删一个 repo，除非用户勾过「下次不再提示」，否则先弹确认
    const skip = localStorage.getItem("rh_skip_repo_confirm") === "1";
    if (!skip) {
      const r = await openConfirm({
        title: t("confirm.remrepo.title"),
        message: t("confirm.remrepo.msg.pre") + fullName + t("confirm.remrepo.msg.post"),
        confirmLabel: t("confirm.remove"),
        showDontAsk: true,
      });
      if (!r.ok) return;
      if (r.dontAsk) localStorage.setItem("rh_skip_repo_confirm", "1");
    }
    await fetch("/history/" + detailEntry.id + "/repo?full_name=" + encodeURIComponent(fullName),
      { method: "DELETE" });
    // 本地同步去掉这个 repo 再重渲染，跟后端改写后的状态一致
    detailEntry.ranked = (detailEntry.ranked || []).filter((r) => r.full_name !== fullName);
    detailEntry.total = detailEntry.ranked.length;
    renderDetail();
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

  $("btn-history").addEventListener("click", openHistory);
  $("btn-history-back").addEventListener("click", () => history.back());
  $("btn-hd-back").addEventListener("click", () => history.back());
  $("btn-history-clear").addEventListener("click", clearAllHistory);
  $("btn-memory").addEventListener("click", openMemory);
  $("btn-memory-back").addEventListener("click", () => history.back());
  $("btn-md-back").addEventListener("click", () => history.back());
  $("btn-memory-clear").addEventListener("click", clearAllMemory);
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
        hard_truncate_circuit: "熔断硬截断", hard_truncate_failed: "摘要失败·硬截断" }[rec.path] || rec.path;
      html += '<div class="dev-meta">压缩：<b>' + devEsc(path) + "</b> · " + (rec.before || []).length +
        " → " + (rec.after || []).length + " 条 · 熔断计数 " + (rec.fail_count || 0) + "</div>";
      html += devFold("压缩前", devJson(rec.before));
      html += devFold("压缩后", devJson(rec.after));
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
    $("dev-audit-back").textContent = "← 返回实时";
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
    $("dev-audit-back").textContent = "← 返回列表";
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
