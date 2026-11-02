/**
 * state -> DOM。**只读状态**：不读网络、不改状态。
 *
 * 事件全用委托绑在容器上，渲染过程里不挂新闭包 —— 重建多少次都不会泄漏。
 */

import { chartPngBase64, renderChartNode } from "./chart.js";
import { renderMarkdown } from "./markdown.js";
import { canListen, canSpeak, listen, speak, stopSpeak } from "./speech.js";

const ROLE_LABEL = { user: "你", assistant: "模型", tool: "工具结果" };
const CONNECTION_LABEL = { connecting: "连接中…", ready: "已连接", offline: "已断开，重连中…" };

const nodes = {};
let actions = {};
let renderedVersion = -1;
let renderedApprovalKey = "";
let renderedQuestionKey = "";

// 最近一次 render 拿到的状态。导航栏的悬浮/点击处理器只装一次，只能从这里回头取数据。
let lastState = null;

// 上一次渲染对应哪个会话。变了就把搜索条收掉。
let renderedSessionId = null;

// 已经画出来的刻度（换了才重画）、正在读的那一轮、标过"有图"的集合签名。
let renderedTurnsKey = "";
let activeTurnSeq = -1;
let markedChartsKey = "";

// "读到哪一轮"的那条判定线：消息区顶部往下四分之一。跳转也按这条线落点 ——
// 两边用同一个数，刻度亮起的那一轮才和跳到的位置对得上。
const ACTIVE_LINE_RATIO = 0.25;

// 跳转的落点比判定线再高这么多像素。正好压在线上时，浏览器把 scrollTop 取整
// 可能让它差半像素掉到线下面，指示器就又回到上一轮了。
const JUMP_AHEAD = 12;

// 离底这么近就当成"贴到底"。这时最后一轮可能短到起点落在判定线下面，
// 按判定线算会亮在上一轮 —— 人都到底了，他看的就是最新一轮。
const BOTTOM_SLACK = 8;

// 滚动时算"读到哪一轮"要读版面。攒到下一帧再算 —— 滚动事件一秒能来几十次。
const nextFrame = globalThis.requestAnimationFrame
  ? (work) => globalThis.requestAnimationFrame(work)
  : (work) => setTimeout(work, 16);

// 已经画进 DOM 的消息条数，以及"往前翻页"的代数 —— 增量渲染靠这两个判断该追加、该插头、
// 还是该整份重建。
let renderedCount = 0;
let renderedPage = 0;

// 已经把哪一段画进 DOM 了（entry key 用竖线连起来）。只记条数不够：图会被并进回答、
// 后到的消息会插进已画的中间 —— 条数不变而内容换了位置时，只有它看得见。
let renderedSignature = "";

// 已经画出来的那半截草稿。文本没变就不重画，省掉一整轮解析。
let renderedDraft = "";

// 顶上那个"加载更早"的按钮。列表重建时它会被一起清掉，所以节点留在外面，用一次插一次。
let earlierButton = null;

// 复制键对应的原文。挂在节点上而不是属性里：一条长回答几千字，塞 data-* 等于再存一份。
const copyText = new WeakMap();

// 展开着的工具块（调用与结果各一份），按块上那个 data-tool-call 记。**必须记在外面** ——
// 消息列表重建、增量追加都会重造节点，只靠 DOM 上的 open 属性，一重建就全折回去了。
//
// 举例：new Set(["call:call_1", "call_1"])
const openedBlocks = new Set();

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function isNearBottom(container) {
  return container.scrollHeight - container.scrollTop - container.clientHeight < 80;
}

/**
 * 右下角那颗"回到最新"的显隐：贴底就藏，离开底部就露。
 *
 * 回放里一律藏起来 —— 回放是照着拍子重演，滚到底等于跳过正在讲的位置。
 * 举例：syncToLatest() -> 用户在往上翻时按钮出现，翻回底部时消失
 */
function syncToLatest() {
  if (!nodes.toLatest) return;
  nodes.toLatest.hidden = replay.active || isNearBottom(nodes.messages);
}

/**
 * 跳到最新一条：直接跳，不做平滑滚动。
 *
 * 往上翻几屏之后平滑滚动要走好几秒，中途还会被新的 scroll 事件打断 —— 长历史里那样更像卡住。
 * 举例：scrollToLatest() -> 列表停在最底下那条消息
 */
function scrollToLatest() {
  const list = nodes.messages;
  list.scrollTop = list.scrollHeight;
  syncToLatest();
}

export function mount(nextActions) {
  actions = nextActions;
  nodes.sessions = document.getElementById("sessions");
  nodes.transcript = document.getElementById("transcript");
  nodes.messages = document.getElementById("messages");
  nodes.toLatest = document.getElementById("to-latest");
  nodes.turns = document.getElementById("turns");
  nodes.turnPreview = document.getElementById("turn-preview");
  nodes.replay = document.getElementById("replay");
  nodes.replayCount = document.getElementById("replay-count");
  nodes.traces = document.getElementById("traces");
  nodes.tracesOpen = document.getElementById("traces-open");
  nodes.tracesBody = document.getElementById("traces-body");
  nodes.tracesNote = document.getElementById("traces-note");
  nodes.search = document.getElementById("search");
  nodes.searchInput = document.getElementById("search-input");
  nodes.searchCount = document.getElementById("search-count");
  nodes.searchTools = document.getElementById("search-tools");
  nodes.approval = document.getElementById("approval-layer");
  nodes.question = document.getElementById("question-layer");
  nodes.toast = document.getElementById("toast");
  nodes.title = document.getElementById("session-title");
  nodes.meta = document.getElementById("session-meta");
  nodes.connection = document.getElementById("connection");
  nodes.stats = document.getElementById("stats");
  nodes.todos = document.getElementById("todos");
  nodes.composer = document.getElementById("composer");
  nodes.input = document.getElementById("input");
  nodes.send = document.getElementById("send");
  nodes.mic = document.getElementById("mic");
  // 浏览器不支持听写就把话筒藏掉，别留一个点了没反应的键。
  nodes.mic.hidden = !canListen();
  nodes.userId = document.getElementById("user-id");

  document.getElementById("new-session").addEventListener("click", () => actions.newSession());
  nodes.tracesOpen.addEventListener("click", () => {
    actions.toggleTraces(!lastState?.tracesOpen);
  });
  nodes.traces.addEventListener("click", (event) => {
    if (event.target.closest('[data-traces="close"]')) actions.toggleTraces(false);
  });
  // 折叠：点抬头那一下。挂在 body 上而不是 panel 上 —— 明细都在 body 里。
  nodes.tracesBody.addEventListener("click", (event) => {
    const head = event.target.closest("[data-trace]");
    if (!head) return;
    toggleTrace(head.dataset.trace, head.dataset.open !== "1");
  });
  document.getElementById("search-open").addEventListener("click", () => openSearch());
  document.getElementById("replay-open").addEventListener("click", () => replayStart(lastState));
  nodes.replay.addEventListener("click", (event) => {
    const button = event.target.closest("[data-replay]");
    if (!button) return;
    if (button.dataset.replay === "pause") replayTogglePause();
    else if (button.dataset.replay === "stop") replayStop();
  });

  // toggle 事件不冒泡，只能在捕获阶段接。
  nodes.messages.addEventListener(
    "toggle",
    (event) => {
      const block = event.target.closest("[data-tool-call]");
      if (!block) return;
      if (block.open) openedBlocks.add(block.dataset.toolCall);
      else openedBlocks.delete(block.dataset.toolCall);
    },
    true
  );

  nodes.sessions.addEventListener("click", (event) => {
    // 删除键在条目里面：先判它，否则会被当成一次"点开"。
    const remove = event.target.closest("[data-delete]");
    if (remove) {
      actions.removeSession(remove.dataset.delete);
      return;
    }
    const item = event.target.closest("[data-session]");
    if (item) actions.selectSession(item.dataset.session);
  });

  // 滚到顶上就往前翻一页；列表短到不用滚时，还有顶上那个按钮兜着。
  // 顺手同步"回到最新"的显隐：滚动位置一变，它就该跟着出现或消失。
  nodes.messages.addEventListener("scroll", () => {
    nextFrame(() => {
      syncActiveTurn(lastState);
      syncToLatest();
    });
    if (nodes.messages.scrollTop > 40) return;
    actions.loadEarlier();
  });

  // 回到最新：先跳到底，再补拉一次尾巴 —— 滚到底不等于数据已经到本地。
  nodes.toLatest.addEventListener("click", () => {
    scrollToLatest();
    Promise.resolve(actions.loadTail?.()).then(() => {
      // 补拉会让列表变长：还差着就再贴一次底。
      if (!isNearBottom(nodes.messages)) scrollToLatest();
    });
  });

  // 刻度：点一下跳过去，悬浮/聚焦看这一轮的提示词与回答。
  nodes.turns.addEventListener("click", (event) => {
    const tick = event.target.closest("[data-turn-seq]");
    if (tick) actions.jumpToTurn(Number(tick.dataset.turnSeq));
  });
  nodes.turns.addEventListener("mouseover", (event) => {
    const tick = event.target.closest("[data-turn-seq]");
    if (tick) showTurnPreview(Number(tick.dataset.turnSeq));
  });
  nodes.turns.addEventListener("mouseleave", hideTurnPreview);
  nodes.turns.addEventListener("focusin", (event) => {
    const tick = event.target.closest("[data-turn-seq]");
    if (tick) showTurnPreview(Number(tick.dataset.turnSeq));
  });
  nodes.turns.addEventListener("focusout", hideTurnPreview);

  // ⌘F / Ctrl+F 打开会话内搜索，Esc 收起来。浏览器自带的查找在这里让给搜索条。
  document.addEventListener("keydown", (event) => {
    const accel = event.metaKey || event.ctrlKey;
    if (accel && String(event.key).toLowerCase() === "f") {
      event.preventDefault();
      openSearch();
      return;
    }
    if (event.key === "Escape" && !nodes.search.hidden) {
      event.preventDefault();
      closeSearch();
    }
  });

  nodes.searchInput.addEventListener("input", () => runSearch(nodes.searchInput.value));
  nodes.searchTools.addEventListener("change", () => {
    search.includeTools = Boolean(nodes.searchTools.checked);
    if (nodes.searchInput.value) runSearch(nodes.searchInput.value);
  });
  nodes.searchInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    stepSearch(event.shiftKey ? -1 : 1);
  });
  nodes.search.addEventListener("click", (event) => {
    const button = event.target.closest("[data-search]");
    if (!button) return;
    const action = button.dataset.search;
    if (action === "next") stepSearch(1);
    else if (action === "prev") stepSearch(-1);
    else if (action === "close") closeSearch();
  });

  nodes.messages.addEventListener("click", (event) => {
    if (event.target.closest("[data-earlier]")) {
      actions.loadEarlier();
      return;
    }
    const exportKey = event.target.closest("[data-export]");
    if (exportKey) {
      exportAnswer(exportKey);
      return;
    }
    const forkButton = event.target.closest("[data-fork]");
    if (forkButton) {
      actions.forkAt(Number(forkButton.dataset.fork));
      return;
    }
    const speakButton = event.target.closest("[data-speak]");
    if (speakButton) {
      toggleSpeak(speakButton);
      return;
    }
    const copy = event.target.closest("[data-copy]");
    if (copy) copyMessage(copy);
  });

  nodes.mic.addEventListener("click", () => toggleListening());

  nodes.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    // 跑着的时候这颗按钮是「停止」，不是「再发一条」。
    if (nodes.send.dataset.mode === "stop") {
      actions.interrupt();
      return;
    }
    submit();
  });

  nodes.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });

  // 输入框随内容长高，超过 200px 就自己滚。
  nodes.input.addEventListener("input", () => autosize());

  nodes.question.addEventListener("click", (event) => {
    const button = event.target.closest("[data-submit-answers]");
    if (!button) return;
    const card = button.closest("[data-request-id]");
    actions.answerQuestion(card.dataset.requestId, collectAnswers(card));
  });

  nodes.approval.addEventListener("click", (event) => {
    const button = event.target.closest("[data-decision]");
    if (!button) return;
    const card = button.closest("[data-request-id]");
    const comment = nodes.approval.querySelector("[data-comment]");
    actions.answer(card.dataset.requestId, button.dataset.decision, comment ? comment.value : "");
  });
}

function autosize() {
  nodes.input.style.height = "auto";
  nodes.input.style.height = Math.min(nodes.input.scrollHeight, 200) + "px";
}

function submit() {
  // 跑着的时候回车不该再发一条 —— 那颗按钮此刻是「停止」。
  if (nodes.send.dataset.mode === "stop") return;
  const text = nodes.input.value.trim();
  if (!text) return;
  // 回放中发消息：先退出回放，否则新消息会被录屏挡在后面看不见。
  if (replay.active) replayStop();
  nodes.input.value = "";
  autosize();
  actions.send(text);
}

export function render(state) {
  lastState = state;
  // 换了会话就把搜索收掉：上一条会话里的词在新会话里接着标，只会让人困惑。
  if (state.currentId !== renderedSessionId) {
    renderedSessionId = state.currentId;
    if (nodes.search && !nodes.search.hidden) closeSearch({ focusComposer: false });
    replayReset();
  }
  const stick = isNearBottom(nodes.messages);
  renderHeader(state);
  renderSessions(state);
  renderMessages(state);
  renderTraces(state);
  syncSearch();
  syncToolRuns(state);
  renderTurns(state);
  renderThought(state);
  renderDraft(state, stick);
  renderTodos(state);
  renderStats(state);
  renderQuestions(state);
  renderApprovals(state);
  renderToast(state);
  if (stick) nodes.messages.scrollTop = nodes.messages.scrollHeight;
  // 每次重画都重算一次：新消息可能把列表撑长了，按钮该露面或该收起来。
  syncToLatest();
}

function renderHeader(state) {
  nodes.userId.textContent = state.userId;
  nodes.title.textContent = state.meta?.title || "新会话";
  nodes.meta.textContent = state.meta
    ? [
        state.meta.model,
        state.meta.permission,
        state.messages.length + (state.hasMore ? "+" : "") + " 条消息",
      ].join(" · ")
    : "";
  nodes.connection.textContent = CONNECTION_LABEL[state.connection] ?? state.connection;
  nodes.connection.dataset.state = state.connection;
  // 发送键在跑着的时候变停止键 —— 一个位置管两件事，不用满界面找第二个按钮。
  nodes.send.dataset.mode = state.running ? "stop" : "send";
  nodes.send.textContent = state.running ? "停止" : "发送";
  nodes.send.disabled = !state.currentId;
}

function renderSessions(state) {
  const list = nodes.sessions;
  list.replaceChildren();
  for (const session of state.sessions) {
    const title = session.title || "新会话";
    // 条目本身不是按钮：里面还有一个删除按钮，按钮不能套按钮。
    const item = el("div", "session-item");
    if (session.session_id === state.currentId) item.setAttribute("aria-current", "true");

    const open = el("button", "session-open");
    open.type = "button";
    open.dataset.session = session.session_id;
    open.append(el("span", "name", title));
    const origin = parentLabel(state, session);
    if (origin) open.append(el("span", "parent", origin));
    open.append(el("span", "time", session.session_id.slice(-6)));

    const remove = el("button", "session-delete", "×");
    remove.type = "button";
    remove.dataset.delete = session.session_id;
    remove.title = "删除会话";
    remove.setAttribute("aria-label", "删除会话：" + title);

    item.append(open, remove);
    list.append(item);
  }
}

/**
 * 分叉来源那一行：父会话还在就用它的标题，已经删了就说一声。
 *
 * 举例：parentLabel(state, session) -> "分叉自 重构 session 模块"
 */
function parentLabel(state, session) {
  if (!session.parent_session_id) return "";
  const parent = state.sessions.find((row) => row.session_id === session.parent_session_id);
  return "分叉自 " + (parent ? parent.title || "无标题会话" : "已删除的会话");
}

function toolNames(messages) {
  // tool_call_id -> 工具名：工具结果的正文里没有工具名，得回头查。
  const names = new Map();
  for (const message of messages) {
    for (const part of message.content) {
      if (part.type === "tool_call") names.set(part.tool_call_id, part.name);
    }
  }
  return names;
}

/**
 * 时间线：消息与图合成一条，按 entry_seq 排，再把图并进它那一轮的回答里。
 *
 * **图是模型回答的一部分，不是一条独立的消息**。它挂到本轮最后一条有正文的 assistant
 * 消息下面，跟那条气泡一起画。本轮还没写出结论时（图刚画完、正文还在路上），先按自己的
 * seq 单独摆着 —— 正文一到就整份重建，落进回答里。
 *
 * 两者各自按 entry_id 去重（见 store.js），这里只负责摆位置。
 */
function timeline(state) {
  // 回放中只放快照里已经露出来的那些，直播的数据一律不看。
  if (replay.active) return replayTimeline();

  const items = [];
  for (const message of state.messages) {
    items.push({ key: message.entry_id, seq: message.entry_seq, kind: "message", item: message });
  }
  for (const chart of state.charts ?? []) {
    items.push({ key: chart.entry_id, seq: chart.entry_seq, kind: "chart", item: chart });
  }
  items.sort((left, right) => left.seq - right.seq);
  return attachCharts(items);
}

/**
 * 按"一条用户消息到下一提用户消息之前"切轮。
 *
 * 每一轮顺手记下两件事，给这一轮的那条回答用：
 *   - 这一轮的问题原文（导出文件拿它当标题）；
 *   - 哪一条是"最终回答"（图的落点、导出键的落点）。
 */
function attachCharts(items) {
  const timeline = [];
  let group = [];

  const flush = () => {
    if (group.length === 0) return;
    const charts = group.filter((entry) => entry.kind === "chart");
    const answer = lastAnswerOf(group);
    // 内容一样的图只留一张（模型偶尔把同一张图连画两遍，日志里是两条不同条目）。
    // 优先留被正文点名的那张，否则未点名的那张会跑到回答末尾去，看着像"图又出现一次"。
    const unique = uniqueCharts(charts.map((entry) => entry.item), answer ? textOf(answer.item) : "");
    const keptIds = new Set(unique.map((chart) => chart.chart_id));
    if (answer) {
      if (unique.length > 0) answer.charts = unique;
      answer.question = questionOf(group);
      // 这一轮起点（用户消息）的 seq：分叉按钮要拿它当分叉点。
      answer.turnSeq = turnSeqOf(group);
      answer.isAnswer = true;
    }
    for (const entry of group) {
      // 挂不上的（这一轮根本没写出正文）按它自己的位置单摆着，总比丢了强。
      if (entry.kind === "chart" && answer) continue;
      // 没正文时同样只摆去重后留下的那张，没留下的不重复出现。
      if (entry.kind === "chart" && !keptIds.has(entry.item.chart_id)) continue;
      timeline.push(entry);
    }
    group = [];
  };

  for (const entry of items) {
    if (entry.kind === "message" && entry.item.role === "user") flush();
    group.push(entry);
  }
  flush();
  return timeline;
}

/**
 * 一轮里的图去重：**同种类 + 同标题**的只留一张。
 *
 * 为什么会有"同一张出现两次"：模型把同一个图表工具连调了两次。两次的条目 entry_id
 * 不同（服务端按 chart_id 去重拦不住），数据往往还差一点 —— 一次是初稿、一次是
 * 补查数据后的修正版。两条都画出来，就是"同一张图，数字还不一样"，比不画更糟。
 *
 * **留哪张**：
 *   - 正文里正好点名了其中一张 → 留那张（模型自己摆的就是它）；
 *   - 一张都没点名、或者点名了不止一张 → 留最后那张（重画通常在后，是修正版）。
 *
 * 举例：uniqueCharts([柱状, 饼图初稿, 饼图修正], "…[[chart:饼图修正]]…") -> [柱状, 饼图修正]
 */
function uniqueCharts(charts, source) {
  const text = String(source ?? "");
  const groups = new Map();
  for (const chart of charts) {
    const key = chart.kind + "|" + chart.title;
    groups.set(key, [...(groups.get(key) ?? []), chart]);
  }

  const survivors = new Set();
  for (const list of groups.values()) {
    const referenced = list.filter((chart) => text.includes(chart.chart_id));
    survivors.add(referenced.length === 1 ? referenced[0] : list[list.length - 1]);
  }
  return charts.filter((chart) => survivors.has(chart));
}

/** 这一轮起点那条用户消息的 seq；没有就是 0。 */
function turnSeqOf(group) {
  for (const entry of group) {
    if (entry.kind === "message" && entry.item.role === "user") return entry.seq;
  }
  return 0;
}

/** 这一轮的问题原文；没有用户消息就是空串。 */
function questionOf(group) {
  for (const entry of group) {
    if (entry.kind !== "message" || entry.item.role !== "user") continue;
    const text = textOf(entry.item);
    if (text) return text;
  }
  return "";
}

/** 本轮最后一条有正文的 assistant 消息 —— 结论都写在那儿。 */
function lastAnswerOf(group) {
  for (let index = group.length - 1; index >= 0; index -= 1) {
    const entry = group[index];
    if (entry.kind !== "message" || entry.item.role !== "assistant") continue;
    const hasText = entry.item.content.some(
      (part) => part.type === "text" && String(part.text || "").trim() !== ""
    );
    if (hasText) return entry;
  }
  return null;
}

function renderItem(entry, names) {
  // 一条坏消息不该把整段对话画成空白：出错就摆一张"这条渲染失败"的卡，其余照常画。
  try {
    if (entry.kind === "chart") return renderChart(entry.item);
    if (entry.item.role === "tool") return renderToolResult(entry.item, names);
    return renderMessage(
      entry.item,
      entry.charts,
      entry.typingChars,
      entry.question,
      entry.isAnswer,
      entry.turnSeq
    );
  } catch (error) {
    console.error("render failed", error);
    const wrapper = el("article", "message message--error");
    wrapper.append(el("div", "role", "渲染失败"));
    wrapper.append(el("div", "bubble", String(error && error.message ? error.message : error)));
    return wrapper;
  }
}

/** 流式那条草稿和思考是挂在列表尾巴上的临时节点，插新消息之前得先撤掉，回头会重新挂。 */
function dropTransient(list) {
  for (const node of list.querySelectorAll(".draft, .thought--live")) {
    node.closest(".message")?.remove();
  }
}

/**
 * 工具调用卡片的状态：正在跑的画一道流光、标题行右端读秒，跑完了都摘掉。
 *
 * 不重建 DOM —— 卡片可能在事件到达之前就已经画好了（图/消息的补拉是异步的），
 * 所以每次 render 都按 store 里的记录对一遍，两个方向都跟得上。
 *
 * 读秒靠一个只在有人跑着时才存在的定时器：一停就关，不留空转的 interval。
 */
function syncToolRuns(state) {
  const runs = state.toolRuns ?? {};
  const now = Date.now();
  let ticking = false;

  for (const node of nodes.messages.querySelectorAll("[data-tool-call]")) {
    const key = String(node.dataset.toolCall ?? "");
    // 结果卡片也带 data-tool-call，但它没有"运行中"这回事。
    if (!key.startsWith("call:")) continue;
    const run = runs[key.slice("call:".length)];
    const next = run ? (run.ok === undefined ? "running" : run.ok ? "ok" : "failed") : "";
    if (node.dataset.state !== next) node.dataset.state = next;

    const label = node.querySelector(".tool-status");
    if (!label) continue;
    if (next === "running") {
      ticking = true;
      label.textContent = "运行中 · " + elapsedSince(run.startedAt, now);
    } else if (label.textContent) {
      label.textContent = "";
    }
  }

  if (ticking) startTicking();
  else stopTicking();
}

// 读秒定时器的句柄；0 表示没在跑。
//
// 举例：12
let tickHandle = 0;

/** 有人跑着才起定时器：每秒重算一遍卡片上的读秒。 */
function startTicking() {
  if (tickHandle) return;
  tickHandle = setInterval(() => {
    if (lastState) syncToolRuns(lastState);
  }, 1000);
}

/** 没人跑着就关掉定时器 —— 空转的 interval 会一直占着事件循环。 */
function stopTicking() {
  if (!tickHandle) return;
  clearInterval(tickHandle);
  tickHandle = 0;
}

/**
 * 一次调用已经跑了多久。60 秒以内念 "12s"，往后念 "1:05"。
 *
 * 举例：elapsedSince(now - 12_000, now) -> "12s"；elapsedSince(now - 65_000, now) -> "1:05"
 */
function elapsedSince(startedAt, now) {
  if (!Number.isFinite(startedAt)) return "0s";
  // 向下取整：59.6 秒时还念 "59s"，过了 60 才跳 "1:00"，不会提前进位。
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  if (seconds < 60) return seconds + "s";
  const minutes = Math.floor(seconds / 60);
  return minutes + ":" + String(seconds % 60).padStart(2, "0");
}

/** 一条时间线的指纹：entries 的 key 按顺序连起来。 */
function signatureOf(items) {
  return items.map((entry) => entry.key).join("|");
}

/** 顶上那个"加载更早"的按钮跟着 hasMore 走，永远排在最前面。 */
function syncEarlier(list, state) {
  // 回放只放已经加载的这一段，"加载更早"在那儿是多余且会打断录屏的。
  if (replay.active || !state.hasMore) {
    if (earlierButton) {
      earlierButton.remove();
      earlierButton = null;
    }
    return;
  }
  if (!earlierButton) {
    earlierButton = el("button", "load-earlier", "加载更早的消息");
    earlierButton.type = "button";
    earlierButton.dataset.earlier = "true";
  }
  list.prepend(earlierButton);
}

/**
 * 消息列表。三种走法：
 *
 * - `messagesVersion` 变了 → 整份重建（换会话、压缩过、刚连上）
 * - `messagesPage` 变了 → 只在头部插一页更早的，并把滚动位置钉住
 * - 都没变、只是多了几条 → 只追加新的那几条，别整份重建
 */
function renderMessages(state) {
  const list = nodes.messages;
  const items = timeline(state);
  const total = items.length;

  if (state.messagesVersion !== renderedVersion) {
    renderedVersion = state.messagesVersion;
    renderedPage = state.messagesPage;
    renderedCount = 0;
    list.replaceChildren();
    if (total === 0) {
      list.append(el("p", "empty", "还没有消息，先问一句吧。"));
      syncEarlier(list, state);
      return;
    }
    const names = toolNames(state.messages);
    for (const entry of items) list.append(renderItem(entry, names));
    renderedCount = total;
    renderedSignature = signatureOf(items);
    syncEarlier(list, state);
    return;
  }

  if (state.messagesPage !== renderedPage) {
    renderedPage = state.messagesPage;
    const older = items.slice(0, total - renderedCount);
    if (renderedCount === 0) list.replaceChildren();
    else dropTransient(list);
    const names = toolNames(state.messages);
    const keep = list.scrollHeight - list.scrollTop;
    list.prepend(...older.map((entry) => renderItem(entry, names)));
    list.scrollTop = list.scrollHeight - keep;
    renderedCount = total;
    renderedSignature = signatureOf(items);
    syncEarlier(list, state);
    return;
  }

  // 已画进去的那一段必须还是原来那些条目。追加一下不够 —— 图会被并进回答、
  // 后到的消息会插到已画的中间，条数可能一点没变，内容却换了位置。对不上就整份重建。
  if (signatureOf(items.slice(0, renderedCount)) !== renderedSignature) {
    renderedVersion = -1;
    renderMessages(state);
    return;
  }

  if (total === renderedCount) return;

  if (renderedCount === 0) list.replaceChildren();
  else dropTransient(list);
  const names = toolNames(state.messages);
  for (const entry of items.slice(renderedCount)) list.append(renderItem(entry, names));
  renderedCount = total;
  renderedSignature = signatureOf(items);
  syncEarlier(list, state);
}

/**
 * 正文里的图位标记：模型在要插图的地方**单独写一行** `[[chart]]`，按顺序对上本轮画的图；
 * 也可以写 `[[chart:c_call_7f3a91]]` 点名单张 —— 标识认不出来就退回按顺序取。
 *
 * **必须独占一行**：写在句子中间时图会插进半句话里，读起来像排版事故。
 * 行内提到这个标记就原样留着，当普通文字。
 *
 * 举例：chartSlotsOf("甲\n[[chart]]\n乙", [图一]) ->
 *       { segments: [{text:"甲\n"}, {chart:图一}, {text:"\n乙"}], leftover: [] }
 */
const CHART_SLOT = /^[ \t]*\[\[\s*chart\s*(?::\s*([A-Za-z0-9_-]+)\s*)?\]\][ \t]*$/gim;

/** 把一段正文按标记切成"文字块 / 图位"的顺序，返回剩下没被用掉的图（它们不画）。 */
function chartSlotsOf(source, charts) {
  const pool = Array.isArray(charts) ? charts.slice() : [];
  const segments = [];
  const text = String(source ?? "");
  let cursor = 0;
  CHART_SLOT.lastIndex = 0;
  for (let match = CHART_SLOT.exec(text); match; match = CHART_SLOT.exec(text)) {
    const before = text.slice(cursor, match.index);
    if (before.trim() !== "") segments.push({ text: before });
    // 没写图号就取下一张；写了但对不上，这一格留空（那张图就此不显示），不乱塞。
    const wanted = match[1];
    const found = wanted
      ? pool.findIndex((chart) => chart.chart_id === wanted)
      : pool.length > 0
        ? 0
        : -1;
    const chart = found >= 0 ? pool.splice(found, 1)[0] : null;
    if (chart) segments.push({ chart });
    cursor = match.index + match[0].length;
  }
  const rest = text.slice(cursor);
  if (rest.trim() !== "") segments.push({ text: rest });
  return { segments, leftover: pool };
}

/** 去掉图位标记后的正文：复制、念稿要的是人话，标记是给界面看的。 */
function withoutChartSlots(text) {
  return String(text ?? "").replace(CHART_SLOT, "");
}

function renderMessage(message, charts, chars, question, isAnswer, turnSeq) {
  const wrapper = el("article", "message message--" + message.role);
  // 导航栏点一行就找 data-seq —— 一条消息在日志里的位置，就是它在页面上的位置。
  wrapper.dataset.seq = String(message.entry_seq);
  wrapper.append(el("div", "role", ROLE_LABEL[message.role] ?? message.role));

  // 回放时这条要一个字一个字打：先建一个空气泡，之后每拍由 paintReplayTyping 换内容。
  const typing = chars !== undefined;
  if (typing) wrapper.dataset.replayTyping = "1";
  const typedText = typing ? textOf(message).slice(0, Math.max(0, chars)) : "";
  let bubbleBuilt = !typing;

  // 这条消息的正文攒起来，末尾那个复制键要的就是它。**始终是全文**。
  const body = [];
  // 还没摆出去的图：正文里的标记按顺序领走，剩下的不画（见下面那段说明）。
  let pending = Array.isArray(charts) ? charts.slice() : [];
  for (const part of message.content) {
    if (part.type === "thinking") {
      wrapper.append(renderThinking(part));
    } else if (part.type === "text") {
      body.push(part.text);
      if (typing) {
        if (bubbleBuilt) continue;
        bubbleBuilt = true;
        const bubble = el("div", "bubble bubble--md");
        if (typedText) bubble.append(renderMarkdown(withoutChartSlots(typedText)));
        wrapper.append(bubble);
        continue;
      }
      // 模型的回答按 Markdown 画；用户自己的话原样显示 —— 别把人打的字当格式解读。
      if (message.role === "assistant") {
        const split = chartSlotsOf(part.text, pending);
        pending = split.leftover;
        const bubble = el("div", "bubble bubble--md");
        for (const segment of split.segments) {
          // 图就插在标记所在的那一段后面：正文、图、图题按模型写的顺序排。
          if (segment.chart) bubble.append(renderChartNode(segment.chart));
          else bubble.append(renderMarkdown(segment.text));
        }
        wrapper.append(bubble);
      } else {
        wrapper.append(el("div", "bubble", part.text));
      }
    } else if (part.type === "tool_call") {
      wrapper.append(renderToolCall(part));
    }
  }
  // 没被标记用到的图**不画**：图只出现在模型点名的地方。
  // 全量摊在末尾会造成"已经在正文里的图又出现一次"（模型重画过一版时尤其像 bug），
  // 而看图的人真正想看的是正文讲到的那一张。

  const text = withoutChartSlots(body.join("\n\n"));
  const attach = charts ?? [];
  if (text || attach.length > 0) {
    const tools = el("div", "message-tools");
    if (text) {
      tools.append(copyButton(text));
      // 只有模型的回答值得念 —— 用户自己刚打的字不用念给他听。
      if (message.role === "assistant" && canSpeak()) tools.append(speakButton(text));
    }
    // 导出与分叉只挂在**这一轮的最终回答**上：一轮只有一个终点。
    if (message.role === "assistant" && isAnswer) {
      tools.append(exportButton(message, charts, question ?? ""));
      if (turnSeq > 0) tools.append(forkButton(turnSeq));
    }
    if (tools.children.length > 0) wrapper.append(tools);
  }
  return wrapper;
}

/**
 * 复制键。要复制的原文挂在 WeakMap 上，不塞进 data-* —— 一条长回答几千字，
 * 塞属性里等于把整段文本复制进 DOM。节点没了 Map 自己就回收了。
 */
function copyButton(text) {
  const button = el("button", "copy", "复制");
  button.type = "button";
  button.dataset.copy = "true";
  copyText.set(button, text);
  return button;
}

async function copyMessage(button) {
  const text = copyText.get(button) ?? "";
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "已复制";
  } catch {
    button.textContent = "复制失败";
  }
  setTimeout(() => {
    button.textContent = "复制";
  }, 1500);
}

function renderThinking(part) {
  // 折叠着放：思考是过程，不是结论。想看就点开，不用就当一个标记。
  const block = el("details", "thought");
  block.append(el("summary", null, "思考过程（" + part.text.length + " 字）"));
  block.append(el("div", "thought-body", part.text));
  return block;
}




// ---------- 导出 ----------

// 导出键对应的这条回答：消息本体 + 挂在它下面的图。
//
// 举例：exportDoc.get(button) -> { message: {...}, charts: [...] }
const exportDoc = new WeakMap();

/** 导出键：一条回答一个 .docx 文件。问题原文一起带上，文档拿它当标题。 */
function exportButton(message, charts, question) {
  const button = el("button", "copy", "导出");
  button.type = "button";
  button.dataset.export = "1";
  button.title = "把这一轮的问题与回答导出成 Word 文档（.docx）";
  exportDoc.set(button, { message, charts, question });
  return button;
}

/**
 * 分叉键：把到这一轮结束为止的对话复制成一个新会话，接着往下问。
 *
 * seq 是个小整数，直接放 data-* —— 不用像正文那样挂 WeakMap。
 *
 * 举例：forkButton(42) -> <button class="copy" data-fork="42">分叉</button>
 */
function forkButton(turnSeq) {
  const button = el("button", "copy", "分叉");
  button.type = "button";
  button.dataset.fork = String(turnSeq);
  button.title = "复制到这一轮为止，开一个新会话接着问（原会话不动）";
  return button;
}

/**
 * 点导出：把这条回答拆成块发给后端，后端排成 .docx 再下载。
 *
 * **只导模型的最终回答**：思考过程、工具调用、工具结果都不进文档 ——
 * 那是过程，给看的人只会造成干扰。图跟着回答一起进去。
 */
async function exportAnswer(button) {
  const spec = exportDoc.get(button);
  if (!spec) return;

  const blocks = [];
  // 图跟着标记走：正文里标记在哪，图就落进哪个位置。没被标记用到的图**不进文档** ——
  // 与界面同一条规则，否则下载下来的文档里会多出界面上根本没显示的图。
  let pending = Array.isArray(spec.charts) ? spec.charts.slice() : [];
  for (const part of spec.message.content) {
    if (part.type !== "text") continue;
    const split = chartSlotsOf(part.text, pending);
    pending = split.leftover;
    for (const segment of split.segments) {
      if (segment.chart) {
        await pushChartBlocks(segment.chart, blocks);
        continue;
      }
      const holder = document.createElement("div");
      holder.append(renderMarkdown(segment.text));
      collectBlocks(holder, blocks);
    }
  }
  if (blocks.length === 0) return;

  // 标题是**用户的问题**（没有就退回回答首行）；正文是回答本身。
  const title = documentTitle(spec);
  actions.exportDocx({
    title,
    subtitle: "导出自 agent-frame · " + todayText(),
    blocks,
    fileName: fileNameOf(title, "docx"),
  });
}

/**
 * 一幅图进 Word：一行图题 + 一张位图。
 *
 * python-docx 不支持 SVG，所以先把图在浏览器里画成 PNG；画不出来就跳过这一幅。
 */
async function pushChartBlocks(chart, blocks) {
  const png = await chartPngBase64(chart);
  if (!png) return;
  blocks.push({ type: "paragraph", text: "图：" + chart.title });
  blocks.push({ type: "image", png_base64: png, width_cm: 16 });
}

/**
 * 文档标题：这一轮的问题原文，压成一行、太长截断。
 *
 * 举例：documentTitle(spec) -> "这批改动的范围是什么"
 */
function documentTitle(spec) {
  const question = String(spec.question ?? "")
    .replace(/s+/g, " ")
    .trim();
  if (!question) return titleOfAnswer(spec.message);
  const chars = Array.from(question);
  return chars.length > 80 ? chars.slice(0, 79).join("") + "…" : question;
}

/**
 * 把渲染好的 DOM 摊成后端认的块。
 *
 * 只认 Markdown 会产出的那几种标签：标题 / 段落 / 列表 / 代码 / 表格 / 引用。
 * 别的（比如外层的 div 包装）递归下去接着找。
 *
 * 举例：collectBlocks(holder, blocks) -> blocks 里多出若干 {type: "paragraph", ...}
 */
function collectBlocks(container, out) {
  for (const node of container.childNodes ?? []) {
    if (node.nodeType !== 1) continue;
    const tag = String(node.tagName ?? "").toUpperCase();
    if (tag === "H1" || tag === "H2" || tag === "H3" || tag === "H4") {
      out.push({ type: "heading", level: Number(tag.slice(1)), text: textOfNode(node) });
    } else if (tag === "P") {
      out.push({ type: "paragraph", text: textOfNode(node) });
    } else if (tag === "UL" || tag === "OL") {
      const items = [];
      for (const item of node.children ?? []) {
        const text = textOfNode(item);
        if (text) items.push(text);
      }
      if (items.length > 0) out.push({ type: "list", ordered: tag === "OL", items });
    } else if (tag === "PRE") {
      out.push({ type: "code", text: textOfNode(node) });
    } else if (tag === "TABLE") {
      out.push(tableBlock(node));
    } else if (tag === "BLOCKQUOTE") {
      out.push({ type: "paragraph", text: textOfNode(node) });
    } else if (tag === "DIV" || tag === "SECTION") {
      collectBlocks(node, out);
    }
  }
  return out;
}

/** 一张表：thead 那一行当表头，其余当数据。 */
function tableBlock(table) {
  const headers = [];
  const rows = [];
  for (const row of table.querySelectorAll("tr")) {
    const cells = [];
    for (const cell of row.children ?? []) cells.push(textOfNode(cell));
    const isHeader = String(row.parentNode?.tagName ?? "").toUpperCase() === "THEAD";
    if (isHeader) headers.push(...cells);
    else rows.push(cells);
  }
  return { type: "table", headers, rows };
}

/** 一个节点的纯文本。 */
function textOfNode(node) {
  return String(node.textContent ?? "").trim();
}

/** 标题取回答的第一行，太长截断；没有文字就退回「模型回答」。 */
function titleOfAnswer(message) {
  const text = message.content
    .filter((part) => part.type === "text")
    .map((part) => String(part.text ?? ""))
    .join("\n")
    .trim();
  const firstLine = text.split("\n").map((line) => line.trim()).find(Boolean) ?? "";
  if (!firstLine) return "模型回答";
  const chars = Array.from(firstLine);
  return chars.length > 40 ? chars.slice(0, 39).join("") + "…" : firstLine;
}

/** 文件名：标题去掉路径与引号这类字符，再加日期与扩展名。 */
function fileNameOf(title, suffix) {
  const safe = String(title ?? "")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 40);
  const day = new Date().toISOString().slice(0, 10);
  return (safe || "模型回答") + "-" + day + "." + suffix;
}

/** 今天的日期，写进文档抬头。 */
function todayText() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
}

/** 把一个 Blob 存成文件。拿不到 Blob / URL 就安静地什么都不做。 */
export function saveBlob(blob, name) {
  if (!blob || typeof URL === "undefined") return;
  if (typeof URL.createObjectURL !== "function") return;
  const url = URL.createObjectURL(blob);
  const link = el("a", null);
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
// ---------- 语音 ----------

// 朗读键对应的原文。和复制键一样挂 WeakMap 上，不塞进 data-*。
const speechText = new WeakMap();

// 这一次听写的句柄；null 表示没在听。
let listening = null;

/** 话筒：点一下开始、再点一下停。听到的话直接写进输入框，不自动发送。 */
function toggleListening() {
  if (listening) {
    stopListening();
    return;
  }
  if (!canListen()) return;

  // 原有文字保留，听到的话接在后面。
  const existing = nodes.input.value;
  const prefix = existing.trim() ? existing.replace(/\s+$/, "") + " " : "";

  let session = null;
  try {
    session = listen({
      onText(text) {
        nodes.input.value = prefix + text;
        autosize();
      },
      onState(state) {
        if (state !== "end" && state !== "error") return;
        listening = null;
        setMicState(false);
      },
    });
  } catch {
    session = null;
  }
  if (!session) return;

  listening = session;
  setMicState(true);
  nodes.input.focus();
}

/** 停掉听写。 */
function stopListening() {
  if (!listening) return;
  const session = listening;
  listening = null;
  setMicState(false);
  session.stop();
}

/** 话筒的两种样子：安静 / 收音中（红框呼吸）。 */
function setMicState(on) {
  if (!nodes.mic) return;
  nodes.mic.dataset.state = on ? "listening" : "idle";
  nodes.mic.setAttribute("aria-pressed", on ? "true" : "false");
  nodes.mic.title = on ? "正在听……点一下停" : "语音输入（点一下开始）";
}

/** 朗读键：点一下念，再点一下停。换一条会自动掐掉上一条。 */
function speakButton(text) {
  const button = el("button", "copy", "朗读");
  button.type = "button";
  button.dataset.speak = "1";
  button.dataset.speaking = "false";
  button.setAttribute("aria-pressed", "false");
  speechText.set(button, text);
  return button;
}

function toggleSpeak(button) {
  if (button.dataset.speaking === "true") {
    // 停：stopSpeak 会回调 onEnd，按钮由 setSpeakState 复原。
    stopSpeak();
    return;
  }
  const text = speechText.get(button) ?? "";
  if (!text) return;
  if (!speak(text, { onEnd: () => setSpeakState(button, false) })) return;
  setSpeakState(button, true);
}

/** 按钮在「朗读 / 停止」之间切。 */
function setSpeakState(button, on) {
  button.dataset.speaking = on ? "true" : "false";
  button.setAttribute("aria-pressed", on ? "true" : "false");
  button.textContent = on ? "停止" : "朗读";
}

function renderToolCall(part) {
  // 跟工具结果一样默认折起来：参数里可能塞着一整份文件正文，摊开会把对话淹掉。
  // 键前缀 "call:" 是为了和同一次调用的结果卡片分开 —— 两张卡片的开合状态互不相干。
  const key = "call:" + part.tool_call_id;
  const card = el("details", "tool-call");
  card.dataset.toolCall = key;
  card.open = openedBlocks.has(key);

  const summary = el("summary");
  summary.append(el("span", null, "调用工具"));
  summary.append(el("strong", null, part.name));
  // 运行中才显示：状态 + 已跑多久。文本由 syncToolRuns 每秒刷新。
  summary.append(el("span", "tool-status"));
  card.append(summary);
  card.append(el("code", null, JSON.stringify(part.arguments)));
  return card;
}

function renderToolResult(message, names) {
  const text = message.content.find((part) => part.type === "text");
  const body = text ? text.text : "（没有正文）";

  // 默认折叠：工具结果可能几千字（文件正文、grep 命中），摊开会把对话淹掉。
  const card = el("details", "tool-card");
  card.dataset.toolCall = message.tool_call_id ?? message.entry_id;
  card.open = openedBlocks.has(card.dataset.toolCall);

  const summary = el("summary");
  summary.append(el("span", null, "工具结果"));
  const name = names.get(message.tool_call_id);
  if (name) summary.append(el("strong", null, name));
  summary.append(el("span", "size", body.split("\n").length + " 行 · " + body.length + " 字"));
  card.append(summary);
  card.append(el("pre", null, body));

  // 跟别的消息一样包一层 .message —— 宽度上限、左对齐、间距都由它统一管，
  // 否则这张卡片会撑满整个消息区，比上面的调用卡片和气泡宽出一截。
  const wrapper = el("article", "message message--tool");
  wrapper.dataset.seq = String(message.entry_seq);
  wrapper.append(card);
  return wrapper;
}

/**
 * 一幅图。它是一等的内容，不折进工具结果卡片里 —— 折起来了等于没画。
 *
 * 外面那层 .message 照旧管宽度与间距，图本身自己带边框（见 style.css 的 .chart）。
 */
function renderChart(chart) {
  const wrapper = el("article", "message message--chart");
  wrapper.dataset.seq = String(chart.entry_seq);
  wrapper.append(renderChartNode(chart));
  return wrapper;
}

function renderThought(state) {
  // 回放时把直播的思考收掉，别和录屏混在一起。
  if (replay.active) {
    const live = nodes.messages.querySelector(".thought--live");
    if (live) live.closest(".message")?.remove();
    return;
  }
  const existing = nodes.messages.querySelector(".thought--live");
  if (!state.thought) {
    if (existing) existing.closest(".message").remove();
    return;
  }

  let block = existing;
  if (!block) {
    const wrapper = el("article", "message message--assistant");
    wrapper.append(el("div", "role", ROLE_LABEL.assistant));
    // 流的过程中是展开的 —— 折起来了用户就不知道它在想什么了。
    block = el("details", "thought thought--live");
    block.open = true;
    block.append(el("summary"));
    block.append(el("div", "thought-body"));
    wrapper.append(block);
    nodes.messages.append(wrapper);
  }
  block.querySelector(".thought-body").textContent = state.thought;
  block.querySelector("summary").textContent = "思考中…（" + state.thought.length + " 字）";
}


/** 光标跟在最后一块的末尾，而不是另起一行。 */
function attachCaret(container) {
  const caret = el("span", "caret");
  let node = container.lastElementChild;
  // 列表的话钻到最后一个 li 里，别把 span 塞进 ul（那不是合法的孩子）。
  while (node && (node.tagName === "UL" || node.tagName === "OL")) node = node.lastElementChild;
  if (node && node.tagName !== "PRE" && node.tagName !== "HR") node.append(caret);
  else container.append(caret);
}

/**
 * 流式输出中那一条。**边收边按 Markdown 画** —— 等整段定稿才渲染的话，
 * 用户要盯着纯文本的星号和竖线看完整个回答。
 *
 * 每片增量都重画一遍整段：一段几千字的解析不到 0.1ms，比一次布局便宜得多。
 */
function renderDraft(state, stick) {
  // 回放时也不显示直播的草稿：那半截属于"现在"，不属于录屏。
  if (replay.active) {
    const liveDraft = nodes.messages.querySelector(".draft");
    if (liveDraft) liveDraft.closest(".message")?.remove();
    renderedDraft = "";
    return;
  }
  const existing = nodes.messages.querySelector(".draft");
  if (!state.draft) {
    if (existing) existing.closest(".message")?.remove();
    renderedDraft = "";
    return;
  }
  let bubble = existing;
  if (!bubble) {
    const wrapper = el("article", "message message--assistant");
    wrapper.append(el("div", "role", ROLE_LABEL.assistant));
    bubble = el("div", "bubble bubble--md draft");
    wrapper.append(bubble);
    nodes.messages.append(wrapper);
    renderedDraft = "";
  }
  if (state.draft !== renderedDraft) {
    renderedDraft = state.draft;
    bubble.replaceChildren(renderMarkdown(state.draft));
    attachCaret(bubble);
  }
  if (stick) nodes.messages.scrollTop = nodes.messages.scrollHeight;
}

// 半径 15 的圈周长；dashoffset 从 0 到它，就是一个满圈到空圈。
const RING_LENGTH = 2 * Math.PI * 15;

// 待办面板：展开还是收起，是**查看状态**、不是会话状态，所以不进 store。
let todosOpen = true;
// 上次画出来的东西。一样就不重画 —— 重画会丢滚动位置。
let renderedTodos = "";

// 待办面板。整份重画：条目不多，省得为"哪条变了"写增量。
// 收起用原生 <details>：键盘、无障碍、焦点都现成，不必自己写开关。
// --------------------------------------------------------------------------
// 轨迹瀑布
// --------------------------------------------------------------------------

// 位点取值 -> 人看得懂的名字。下标就是日志里那个 name，别的都是空。
const SPAN_LABEL = {
  "trace:before": "开跑",
  "turn:before": "轮开始",
  "llm:before": "取材",
  "llm:during": "模型入口",
  "llm:after": "定稿",
  "tool:before": "检查链",
  "tool:during": "工具执行",
  "tool:after": "结果规范化",
  "turn:end": "轮收尾",
  "trace:end": "收尾",
  llm: "模型调用",
};

// 一条 trace 的状态 -> 面板上那个小标。
const TRACE_STATUS_LABEL = {
  completed: "完成",
  aborted: "中断",
  failed: "失败",
  budget_exceeded: "超预算",
};

// 上一次画出来的是哪一批 trace。事件一秒能来好几次，没变就不重画。
let renderedTracesKey = "";

// 哪几条被手动折起来 / 展开过。默认只展开最新那条 —— 一次执行十几格，全铺开就是一面墙。
// 这两份是**视图状态**：不进 store，换会话也不清（id 对不上自然失效）。
const collapsedTraces = new Set();
const expandedTraces = new Set();

/**
 * 这条 trace 现在是展开的吗：手动开过就开，手动折过就折，否则只有最新那条开着。
 *
 * 举例：traceOpen(0, "trace_9c8b7a") -> true；traceOpen(1, "trace_9c8b7a") -> false
 */
function traceOpen(index, traceId) {
  if (expandedTraces.has(traceId)) return true;
  if (collapsedTraces.has(traceId)) return false;
  return index === 0;
}

/**
 * 点抬头：折起来 / 展开。
 *
 * 折的是**视图**，不碰数据 —— 重画一遍就够，不用回后端。
 *
 * 举例：toggleTrace("trace_9c8b7a", false)
 */
function toggleTrace(traceId, open) {
  if (open) {
    expandedTraces.add(traceId);
    collapsedTraces.delete(traceId);
  } else {
    collapsedTraces.add(traceId);
    expandedTraces.delete(traceId);
  }
  if (lastState) render(lastState);
}

/**
 * 轨迹面板：每条 trace 一段瀑布，横条按耗时比例摆。
 *
 * 数据是收尾时写进日志的，所以关掉面板、刷新、换机器都还在 —— 不靠任何事件重放。
 */
function renderTraces(state) {
  const panel = nodes.traces;
  if (!panel) return;
  panel.hidden = !state.tracesOpen;
  if (nodes.tracesOpen) nodes.tracesOpen.dataset.open = state.tracesOpen ? "true" : "false";
  if (!state.tracesOpen) return;

  const traces = state.traces;
  // 签名里带上"哪几条是展开的"：只折不开也要重画。null 是"还没取"，单独一格。
  const key =
    traces === null
      ? "loading"
      : traces
          .map(
            (trace, index) =>
              trace.trace_id +
              ":" +
              Math.round(trace.duration_ms) +
              ":" +
              trace.spans.length +
              ":" +
              (traceOpen(index, trace.trace_id) ? "1" : "0")
          )
          .join("|");
  if (key === renderedTracesKey) return;
  renderedTracesKey = key;

  nodes.tracesNote.textContent =
    traces === null || traces.length === 0 ? "" : "最近 " + traces.length + " 次执行";
  nodes.tracesBody.replaceChildren();
  if (traces === null) {
    nodes.tracesBody.append(el("p", "traces-loading", "读取中…"));
    return;
  }
  if (traces.length === 0) {
    nodes.tracesBody.append(
      el("p", "traces-empty", "还没有记录：一次执行收尾时才会把耗时写进日志。")
    );
    return;
  }
  traces.forEach((trace, index) => nodes.tracesBody.append(traceNode(trace, index)));
}

/**
 * 一条 trace：抬头四件事（时刻/状态/轮数/总耗时）+ 下面一列横条。
 *
 * 抬头就是折叠键 —— 一条十几格，折起来才扫得动。折起来时**明细不进 DOM**，
 * 不是靠 CSS 藏：三百格的节点留着，滚动和重画都白费。
 */
function traceNode(trace, index) {
  const open = traceOpen(index, trace.trace_id);
  const box = el("article", "trace");

  const head = el("button", "trace-head");
  head.type = "button";
  head.dataset.trace = trace.trace_id;
  // 点的那一下要知道现在开着没：写在 data-open 上，省得再算一遍。
  head.dataset.open = open ? "1" : "0";
  head.setAttribute("aria-expanded", open ? "true" : "false");
  head.title = open ? "折起这次执行" : "展开这次执行的耗时明细";

  head.append(el("span", "trace-caret", open ? "▾" : "▸"));
  head.append(el("span", "trace-when", clockOf(new Date(trace.started_at))));
  const status = el("span", "trace-status", TRACE_STATUS_LABEL[trace.status] ?? trace.status);
  status.dataset.status = trace.status;
  head.append(status);
  head.append(el("span", "trace-turns", trace.turns + " 轮"));
  if (trace.reason) head.append(el("span", "trace-reason", trace.reason));
  head.append(el("span", "trace-total", formatMs(trace.duration_ms)));
  box.append(head);

  if (!open) return box;

  const rows = el("div", "trace-rows");
  // 瀑布的总刻度：以总耗时为准；明细里万一超出（收尾那一格不在里面），按明细收尾。
  let total = trace.duration_ms;
  for (const span of trace.spans) {
    total = Math.max(total, span.start_ms + span.elapsed_ms);
  }
  const spans = [...trace.spans].sort((left, right) => left.start_ms - right.start_ms);
  for (const span of spans) rows.append(spanNode(span, total));
  box.append(rows);
  return box;
}

/** 一格横条：名字 / 轨道上的位置 / 毫秒数。 */
function spanNode(span, total) {
  const wide = Math.max(total, 1);
  const row = el("div", "trace-row");
  row.title =
    span.name +
    (span.detail ? " · " + span.detail : "") +
    "（第 " + span.turn + " 轮，起 " + formatMs(span.start_ms) + "，耗时 " + formatMs(span.elapsed_ms) + "）";

  row.append(el("span", "trace-turn", "T" + span.turn));
  row.append(el("span", "trace-label", SPAN_LABEL[span.name] ?? span.name));

  const track = el("span", "trace-track");
  const bar = el("span", "trace-bar");
  bar.dataset.kind = span.kind;
  bar.style.left = (span.start_ms / wide) * 100 + "%";
  // 最少给 0.4%：1 毫秒的格子也得看得见，不然一列横条里会凭空缺几行。
  bar.style.width = Math.max(0.4, (span.elapsed_ms / wide) * 100) + "%";
  track.append(bar);
  row.append(track);

  row.append(el("span", "trace-ms", formatMs(span.elapsed_ms)));
  return row;
}

/**
 * 毫秒数给人看：一秒以内说毫秒，超过就说秒。
 *
 * 举例：formatMs(3.2) -> "3ms"；formatMs(8421.5) -> "8.4s"
 */
function formatMs(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value)) return "0ms";
  if (value < 1000) return Math.round(value) + "ms";
  return (value / 1000).toFixed(1) + "s";
}

/**
 * 一个时刻只留时分秒：这一列看的是先后，日期在会话抬头里。
 *
 * 举例：clockOf(new Date(...)) -> "14:32:07"
 */
function clockOf(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
}


function renderTodos(state) {
  const box = nodes.todos;
  const todos = state.todos ?? [];

  // 空清单整块藏起来，连那一行计数也不占位置。
  if (todos.length === 0) {
    if (renderedTodos !== "") {
      box.replaceChildren();
      renderedTodos = "";
    }
    box.hidden = true;
    return;
  }

  // 整份重画会把 <details> 的 open 抹掉 —— 重画之前先把用户这次的选择记下来。
  const panel = box.querySelector("details");
  if (panel) todosOpen = panel.open;

  const signature =
    (todosOpen ? "1" : "0") +
    "|" +
    todos.map((todo) => todo.content + ":" + todo.status).join("\n");
  if (signature === renderedTodos) {
    box.hidden = false;
    return;
  }

  renderedTodos = signature;
  box.hidden = false;
  box.replaceChildren();

  const done = todos.filter((todo) => todo.status === "completed").length;
  const details = el("details", "todos-box");
  details.open = todosOpen;

  const head = el("summary", "todos-head");
  head.append(el("span", "todos-title", "待办"));
  head.append(el("span", "todos-count", done + " / " + todos.length));
  details.append(head);

  const list = el("ol", "todos-list");
  for (const todo of todos) {
    const item = el("li", "todo", todo.content);
    item.dataset.status = todo.status;
    list.append(item);
  }
  details.append(list);
  box.append(details);
}

function renderStats(state) {
  const stats = state.stats ?? {};
  const maxTokens = stats.max_tokens || 0;
  const used = stats.context_tokens || 0;
  // 进行中的那次 trace 还没进统计，用事件带来的轮次把数字顶上去。
  const turns = (stats.turns || 0) + (state.running ? state.liveTurn || 0 : 0);
  const ratio = maxTokens ? Math.min(1, used / maxTokens) : 0;

  const bar = nodes.stats;
  bar.replaceChildren();

  const ring = el("span", "ring");
  ring.title = "上下文 " + used + " / " + maxTokens + " token";
  ring.dataset.tone = ratio >= 0.95 ? "danger" : ratio >= 0.8 ? "warn" : "calm";
  ring.innerHTML =
    '<svg viewBox="0 0 36 36" aria-hidden="true">' +
    '<circle class="track" cx="18" cy="18" r="15"></circle>' +
    '<circle class="value" cx="18" cy="18" r="15" style="stroke-dasharray:' +
    RING_LENGTH.toFixed(1) +
    ";stroke-dashoffset:" +
    (RING_LENGTH * (1 - ratio)).toFixed(1) +
    '"></circle></svg>';

  bar.append(ring);
  bar.append(el("span", "stat", Math.round(ratio * 100) + "% 上下文"));
  bar.append(el("span", "sep", "·"));
  bar.append(el("span", "stat", "trace " + (stats.traces || 0)));
  bar.append(el("span", "sep", "·"));
  bar.append(el("span", "stat", "turn " + turns));
}

/**
 * 把卡片上填的东西收成 answers：选项按选中的顺序读，自定义文字去空白。
 *
 * 一个都没选、也没填字的那种，服务端会整份拒掉（400）—— 前端不拦，
 * 让回执上的错误照实显示出来，比自己再写一套规则可靠。
 */
function collectAnswers(card) {
  const answers = [];
  for (const block of card.querySelectorAll("[data-question]")) {
    const selected = [];
    for (const input of block.querySelectorAll("input[type=radio], input[type=checkbox]")) {
      if (input.checked) selected.push(input.value);
    }
    const custom = block.querySelector("[data-custom]");
    const text = custom ? custom.value.trim() : "";
    answers.push({ id: block.dataset.question, selected, custom: text || null });
  }
  return answers;
}

function renderQuestions(state) {
  // 和审批同一个判据：只按 request_id 集合决定要不要重建 ——
  // 否则卡上正打到一半的字会被一次无关的重绘冲掉。
  const key = state.questions.map((item) => item.request_id).join(",");
  if (key === renderedQuestionKey) return;
  renderedQuestionKey = key;

  const layer = nodes.question;
  layer.replaceChildren();
  layer.hidden = state.questions.length === 0;
  if (state.questions.length === 0) return;

  const request = state.questions[0];
  const card = el("div", "approval-card question-card");
  card.dataset.requestId = request.request_id;

  for (const question of request.questions) {
    const block = el("div", "question-block");
    block.dataset.question = question.id;
    if (question.header) block.append(el("h2", null, question.header));
    block.append(el("p", "question-text", question.question));

    if (question.options.length > 0) {
      const list = el("div", "question-options");
      // 多选给复选框，单选给单选钮。**单选也要能一个都不选** —— 同一题共用一个 name，
      // 一开始谁都没选中，用户可以直接在下面的输入框里自己说。
      const type = question.multi_select ? "checkbox" : "radio";
      for (const option of question.options) {
        const label = el("label", "question-option");
        const input = document.createElement("input");
        input.type = type;
        input.name = "q-" + question.id;
        input.value = option.label;
        label.append(input);
        label.append(el("span", "question-label", option.label));
        if (option.description) label.append(el("span", "question-hint", option.description));
        list.append(label);
      }
      block.append(list);
    }

    const custom = document.createElement("input");
    custom.dataset.custom = "1";
    custom.placeholder = question.options.length > 0 ? "都不合适？自己说一句" : "直接填";
    block.append(custom);
    card.append(block);
  }

  const row = el("div", "approval-actions");
  const submit = el("button", "approve", "提交");
  submit.type = "button";
  submit.dataset.submitAnswers = "1";
  row.append(submit);
  card.append(row);
  layer.append(card);
}

function renderApprovals(state) {
  // 只按 request_id 集合判断要不要重建：备注框里可能正打着一半的话。
  const key = state.approvals.map((approval) => approval.request_id).join(",");
  if (key === renderedApprovalKey) return;
  renderedApprovalKey = key;

  const layer = nodes.approval;
  layer.replaceChildren();
  layer.hidden = state.approvals.length === 0;
  if (state.approvals.length === 0) return;

  const request = state.approvals[0];
  const card = el("div", "approval-card");
  card.dataset.requestId = request.request_id;
  card.append(el("h2", null, "需要你确认：" + request.tool_name));
  card.append(el("p", "reason", request.reason));
  card.append(el("pre", null, JSON.stringify(request.arguments, null, 2)));

  const comment = document.createElement("input");
  comment.placeholder = "备注（可选）";
  comment.dataset.comment = "1";
  card.append(comment);

  const row = el("div", "approval-actions");
  const deny = el("button", "deny", "拒绝");
  deny.type = "button";
  deny.dataset.decision = "deny";
  const approve = el("button", "approve", "同意");
  approve.type = "button";
  approve.dataset.decision = "approve";
  row.append(deny, approve);
  card.append(row);
  layer.append(card);
}

function renderToast(state) {
  nodes.toast.textContent = state.error;
  nodes.toast.hidden = !state.error;
}

// ---------- 回放 ----------

// 回放是**视图状态**：拿一段快照，按节奏一条一条露出来。不进 store、不碰后端。
//
// 举例：{ active: true, items: [...], index: 3, typed: 12, typing: true }
const replay = {
  active: false,
  items: [],
  index: 0,
  typed: 0,
  typing: false,
  paused: false,
  timer: 0,
};

// 打字的节奏：每 24 毫秒一笔，长消息加速，单条最多约 4 秒打完。
const REPLAY_STEP_MS = 24;
const REPLAY_GAP_MS = 420;
const REPLAY_TICKS_PER_MESSAGE = 170;

/** 开始回放：快照当前会话，立刻露出第一条，然后按节奏往下走。 */
function replayStart(state) {
  if (!state || replay.active) return;
  const items = replayItems(state);
  if (items.length === 0) return;
  if (!nodes.search.hidden) closeSearch({ focusComposer: false });

  replay.active = true;
  replay.items = items;
  replay.index = 1;
  replay.typed = 0;
  replay.typing = isTypingItem(items[0]);
  replay.paused = false;
  renderedVersion = -1; // 从空列表重建，别留下直播时的那一份
  render(state);
  updateReplayBar();
  replaySchedule(replay.typing ? REPLAY_STEP_MS : REPLAY_GAP_MS);
}

/** 退出回放：把列表恢复成正常视图。 */
function replayStop() {
  if (!replay.active) return;
  replayReset();
  renderedVersion = -1;
  if (lastState) render(lastState);
}

/** 只清状态、不动 DOM（换会话那条路要用）。 */
function replayReset() {
  clearTimeout(replay.timer);
  replay.timer = 0;
  replay.active = false;
  replay.items = [];
  replay.index = 0;
  replay.typed = 0;
  replay.typing = false;
  replay.paused = false;
  if (nodes.replay) nodes.replay.hidden = true;
}

/** 暂停 / 继续。 */
function replayTogglePause() {
  if (!replay.active) return;
  replay.paused = !replay.paused;
  if (replay.paused) {
    clearTimeout(replay.timer);
    replay.timer = 0;
  } else {
    replaySchedule(REPLAY_STEP_MS);
  }
  updateReplayBar();
}

function replaySchedule(delay) {
  clearTimeout(replay.timer);
  replay.timer = setTimeout(replayTick, delay);
}

/** 一拍：要么把当前这条再打几个字，要么露出下一条。 */
function replayTick() {
  replay.timer = 0;
  if (!replay.active || replay.paused || !lastState) return;

  if (replay.typing) {
    const message = replay.items[replay.index - 1].item;
    const full = textOf(message);
    replay.typed = Math.min(full.length, replay.typed + typingStep(full.length));
    paintReplayTyping();
    if (replay.typed < full.length) {
      replaySchedule(REPLAY_STEP_MS);
      return;
    }
    replay.typing = false;
    const typingNode = nodes.messages.querySelector("[data-replay-typing]");
    if (typingNode) delete typingNode.dataset.replayTyping;
  }

  if (replay.index >= replay.items.length) {
    replayStop();
    return;
  }

  replay.index += 1;
  const entry = replay.items[replay.index - 1];
  replay.typing = isTypingItem(entry);
  replay.typed = 0;
  render(lastState);
  updateReplayBar();
  replaySchedule(replay.typing ? REPLAY_STEP_MS : REPLAY_GAP_MS);
}

/** 把当前这条已打出来的那一段画进气泡，并跟到底部。 */
function paintReplayTyping() {
  const article = nodes.messages.querySelector("[data-replay-typing]");
  if (!article) return;
  const bubble = article.querySelector(".bubble--md");
  if (!bubble) return;
  const message = replay.items[replay.index - 1].item;
  bubble.replaceChildren(renderMarkdown(textOf(message).slice(0, replay.typed)));
  attachCaret(bubble);
  if (isNearBottom(nodes.messages)) nodes.messages.scrollTop = nodes.messages.scrollHeight;
}

/** 这一条要不要一个字一个字打出来：模型的、带正文的消息。 */
function isTypingItem(entry) {
  return (
    entry.kind === "message" &&
    entry.item.role === "assistant" &&
    textOf(entry.item) !== ""
  );
}

/** 一条消息里所有正文拼起来 —— 气泡、复制、朗读、回放都用它。 */
function textOf(message) {
  return message.content
    .filter((part) => part.type === "text")
    .map((part) => String(part.text ?? ""))
    .join("\n\n");
}

/** 每一拍打几个字：短消息一字一字，长消息快点打完。 */
function typingStep(length) {
  return Math.max(1, Math.ceil(length / REPLAY_TICKS_PER_MESSAGE));
}

/** 顶上的回放条：进度 + 暂停/继续。 */
function updateReplayBar() {
  if (!nodes.replay) return;
  nodes.replay.hidden = !replay.active;
  nodes.replayCount.textContent = replay.index + " / " + replay.items.length;
  const pause = nodes.replay.querySelector('[data-replay="pause"]');
  if (pause) pause.textContent = replay.paused ? "继续" : "暂停";
}

/** 回放用的快照：消息与图按 seq 排，图不往回答里塞（回放按发生顺序放）。 */
function replayItems(state) {
  const items = [];
  for (const message of state.messages) {
    items.push({ key: message.entry_id, seq: message.entry_seq, kind: "message", item: message });
  }
  for (const chart of state.charts ?? []) {
    items.push({ key: chart.entry_id, seq: chart.entry_seq, kind: "chart", item: chart });
  }
  items.sort((left, right) => left.seq - right.seq);
  return items;
}

/** 回放用的时间线：只给前 index 条，最后一条在打字时带上已打的字数。 */
function replayTimeline() {
  const shown = replay.items.slice(0, replay.index);
  return shown.map((entry, position) => {
    const isLast = position === shown.length - 1;
    if (!isLast || !replay.typing) return entry;
    return { ...entry, typingChars: replay.typed };
  });
}


// ---------- 会话内搜索 ----------

// 搜索是**界面状态**，不进 store：换个会话就该清空，也不该影响任何业务数据。
//
// 举例：{ query: "压力", hits: [mark], index: 2, signature: "e1|e2" }
const search = { query: "", hits: [], index: -1, signature: "", includeTools: false };

/** 唤出搜索条并聚焦；条里已经有词就当场再标一次。 */
function openSearch() {
  nodes.search.hidden = false;
  nodes.searchTools.checked = search.includeTools;
  nodes.searchInput.focus();
  nodes.searchInput.select();
  if (nodes.searchInput.value) runSearch(nodes.searchInput.value);
}

/**
 * 收起搜索条，把高亮拆掉、焦点还给输入框。
 *
 * 换会话时也走这里，但那时候不该把焦点抢到输入框上（options.focusComposer = false）。
 */
function closeSearch(options) {
  nodes.search.hidden = true;
  resetMarks();
  search.query = "";
  search.signature = "";
  updateSearchCount();
  if (options?.focusComposer !== false && nodes.input) nodes.input.focus();
}

/**
 * 按查询串重标一遍，并跳到第一处。
 *
 * 空串只是把所有高亮拆掉，不跳。
 *
 * 举例：runSearch("压力") -> 标出全部命中，计数器显示 "1 / 7"
 */
function runSearch(raw) {
  const query = String(raw ?? "").trim();
  resetMarks();
  search.query = query;
  if (!query) {
    updateSearchCount();
    return;
  }
  search.signature = renderedSignature;
  markMatches(searchRoots(), query, search.hits);
  if (search.hits.length > 0) {
    search.index = 0;
    focusHit();
  }
  updateSearchCount();
}

/** 内容变了（新消息进来、翻了一页）而搜索条还开着：重标，但别跳、别抢滚动。 */
function syncSearch() {
  if (nodes.search.hidden || !search.query) return;
  if (search.signature === renderedSignature) return;
  search.signature = renderedSignature;
  const keep = search.index;
  resetMarks();
  markMatches(searchRoots(), search.query, search.hits);
  if (search.hits.length > 0) {
    search.index = Math.min(Math.max(keep, 0), search.hits.length - 1);
    search.hits[search.index].classList.add("search-hit--active");
  }
  updateSearchCount();
}

/** 上/下一处，到头绕回去。 */
function stepSearch(delta) {
  const total = search.hits.length;
  if (total === 0) return;
  search.index = (search.index + delta + total) % total;
  focusHit();
  updateSearchCount();
}

/** 把当前那一处滚到视野中央 —— 滚动会让右侧刻度自动跟过去。 */
function focusHit() {
  for (const hit of search.hits) hit.classList.remove("search-hit--active");
  const hit = search.hits[search.index];
  if (!hit) return;
  hit.classList.add("search-hit--active");
  revealHit(hit);
  hit.scrollIntoView({ block: "center" });
  if (lastState) nextFrame(() => syncActiveTurn(lastState));
}

/**
 * 命中在折叠块里（思考、调用参数、工具结果）就先把它展开，否则滚过去只看到一行标题。
 *
 * 命中在标题行上的不用展开 —— 那里本来就看得见，展开只会把版面撑开。
 *
 * 举例：revealHit(mark) -> 外层 <details> 的 open 变成 true
 */
function revealHit(hit) {
  if (typeof hit.closest === "function" && hit.closest("summary")) return;
  for (let node = hit.parentNode; node; node = node.parentNode) {
    if (String(node.tagName ?? "").toUpperCase() === "DETAILS" && !node.open) node.open = true;
  }
}

/** 计数器：没命中时显示 "0 / 0"。 */
function updateSearchCount() {
  const total = search.hits.length;
  const at = total > 0 ? search.index + 1 : 0;
  nodes.searchCount.textContent = at + " / " + total;
}

/** 拆掉全部高亮：每个 mark 换回它自己的文本。 */
function clearMarks() {
  for (const mark of nodes.messages.querySelectorAll(".search-hit")) {
    if (!mark.parentNode) continue;
    mark.parentNode.replaceChild(document.createTextNode(mark.textContent ?? ""), mark);
  }
}

/** 清空命中表与当前指针。 */
function resetMarks() {
  clearMarks();
  search.hits = [];
  search.index = -1;
}

/**
 * 在一棵子树里标出全部命中，按文档顺序收进 hits。
 *
 * 逐文本节点处理：先找出这个节点里所有命中区间，再从后往前切 ——
 * 从后往前切是为了让前面的下标始终有效。
 *
 * 举例：markMatches(nodes.messages, "压力", hits) -> hits 里多出若干个 <mark>
 */
function markMatches(roots, query, hits) {
  const needle = query.toLowerCase();
  const nodes = [];
  for (const root of roots) textNodesUnder(root, nodes);
  for (const node of nodes) {
    const text = String(node.textContent ?? "");
    const lower = text.toLowerCase();
    const ranges = [];
    for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, at + needle.length)) {
      ranges.push([at, at + needle.length]);
    }
    const marked = [];
    for (let index = ranges.length - 1; index >= 0; index -= 1) {
      const mark = wrapRange(node, ranges[index][0], ranges[index][1]);
      if (mark) marked.unshift(mark);
    }
    hits.push(...marked);
  }
}

/** 把文本节点里 [start, end) 那一段换成 <mark>，返回它。 */
function wrapRange(node, start, end) {
  if (typeof node.splitText !== "function" || !node.parentNode) return null;
  const tail = node.splitText(start);
  tail.splitText(end - start);
  const mark = el("mark", "search-hit");
  tail.parentNode.replaceChild(mark, tail);
  mark.appendChild(tail);
  return mark;
}

/**
 * 这一轮要搜哪些子树。
 *
 * 默认**只搜正文**（对话气泡）—— 工具结果、调用参数、思考过程都是机器产出，
 * 搜一个词可能在里面命中几十处，把真正想找的那句话淹掉。
 *
 * 勾上「含工具输出」就搜整个消息区。
 *
 * 举例：searchRoots() -> [div.bubble, div.bubble, …]
 */
function searchRoots() {
  if (search.includeTools) return [nodes.messages];
  return nodes.messages.querySelectorAll(".bubble");
}

/**
 * 收集可搜的文本节点：跳过图形与界面装饰（角色标签、复制键、SVG）。
 *
 * 举例：textNodesUnder(nodes.messages) -> [Text("帮我看看…"), Text("压力")]
 */
function textNodesUnder(root, out) {
  const found = out ?? [];
  for (const node of root.childNodes ?? root.children ?? []) {
    if (node.nodeType === 3) {
      if (String(node.textContent ?? "").trim()) found.push(node);
      continue;
    }
    if (node.nodeType !== 1) continue;
    const tag = String(node.tagName ?? "").toUpperCase();
    if (tag === "SVG" || tag === "SCRIPT" || tag === "STYLE") continue;
    if (/(^| )(role|copy)( |$)/.test(String(node.className ?? ""))) continue;
    textNodesUnder(node, found);
  }
  return found;
}


// ---------- 轮次导航 ----------

/**
 * 右侧那一列刻度：一轮一条，点一下跳过去。
 *
 * 数据是服务端算好的整份大纲（最多 200 行）。这里只在"大纲变了"的时候重画刻度，
 * 悬浮、当前读到的位置都是之后单独刷的 —— 否则每来一个增量都会把浮层抖没。
 */
function renderTurns(state) {
  const turns = state.turns ?? [];
  const key = turns.map((turn) => turn.turn + ":" + turn.seq).join(",");
  if (key !== renderedTurnsKey) {
    renderedTurnsKey = key;
    nodes.turns.replaceChildren();
    for (const turn of turns) {
      const tick = el("button", "turn-tick");
      tick.type = "button";
      tick.dataset.turnSeq = String(turn.seq);
      tick.setAttribute("aria-label", "第 " + turn.turn + " 轮：" + (turn.prompt || "无文字"));
      nodes.turns.append(tick);
    }
    activeTurnSeq = -1;
    markedChartsKey = "";
    // 刻度被重画了，正悬浮着的那张浮层已经没有对应的节点，收起来。
    hideTurnPreview();
  }
  nodes.transcript.dataset.turns = turns.length > 0 ? "on" : "off";
  nodes.turns.hidden = turns.length === 0;
  syncChartMarks(state);
  syncActiveTurn(state);
}

/** 哪一轮出过图：在它那个刻度旁边点一个小点。 */
function syncChartMarks(state) {
  const charts = state.charts ?? [];
  const key = charts.map((chart) => chart.entry_id).join(",");
  if (key === markedChartsKey) return;
  markedChartsKey = key;

  const owner = new Set();
  for (const chart of charts) {
    let seq = null;
    for (const turn of state.turns ?? []) {
      if (turn.seq <= chart.entry_seq) seq = turn.seq;
      else break;
    }
    if (seq !== null) owner.add(seq);
  }
  for (const tick of nodes.turns.querySelectorAll("[data-turn-seq]")) {
    tick.classList.toggle("has-chart", owner.has(Number(tick.dataset.turnSeq)));
  }
}

/** 当前读到的是哪一轮（消息区顶部往下四分之一那条线）。 */
function syncActiveTurn(state) {
  const turns = state.turns ?? [];
  if (turns.length === 0) return;
  const active = activeTurnOf(state);
  if (active === activeTurnSeq) return;
  activeTurnSeq = active;
  for (const tick of nodes.turns.querySelectorAll("[data-turn-seq]")) {
    tick.dataset.active = Number(tick.dataset.turnSeq) === active ? "true" : "false";
  }
}

/** 判定线的文档坐标：消息区顶部往下四分之一。跳转与"读到哪一轮"共用。 */
function activeLineOf(list) {
  return list.getBoundingClientRect().top + list.clientHeight * ACTIVE_LINE_RATIO;
}

/**
 * 阅读线落在哪一轮上：取"seq 不超过阅读线的最后一条消息"所属的那一轮。
 *
 * 版面读不出来（比如刚重建、还没布局）时退到第一轮，不要抛。
 */
function activeTurnOf(state) {
  const turns = state.turns ?? [];
  if (turns.length === 0) return -1;
  const list = nodes.messages;
  const left = list.scrollHeight - list.scrollTop - list.clientHeight;
  if (left <= BOTTOM_SLACK) return turns[turns.length - 1].seq;
  const line = activeLineOf(list);
  let seq = Number.NEGATIVE_INFINITY;
  for (const node of list.querySelectorAll("[data-seq]")) {
    const top = node.getBoundingClientRect().top;
    if (!Number.isFinite(top) || top > line) break;
    seq = Number(node.dataset.seq);
  }
  let active = turns[0].seq;
  for (const turn of turns) {
    if (turn.seq <= seq) active = turn.seq;
    else break;
  }
  return active;
}

/** 悬浮到某个刻度上：浮出这一轮的提示词与回答，锚在刻度左边。 */
function showTurnPreview(seq) {
  const turns = lastState?.turns ?? [];
  const turn = turns.find((row) => row.seq === seq);
  if (!turn) return;

  const box = nodes.turnPreview;
  box.replaceChildren();
  box.append(el("div", "turn-preview-head", "第 " + turn.turn + " 轮"));
  box.append(el("p", "turn-preview-prompt", turn.prompt || "（这条消息没有文字）"));
  if (turn.response) box.append(el("p", "turn-preview-response", turn.response));
  box.hidden = false;

  const tick = nodes.turns.querySelector('[data-turn-seq="' + seq + '"]');
  if (!tick) return;
  const frame = nodes.transcript.getBoundingClientRect();
  const at = tick.getBoundingClientRect();
  if (!Number.isFinite(at.top) || !Number.isFinite(frame.top)) return;
  box.style.top = Math.max(8, at.top - frame.top - 8) + "px";
}

function hideTurnPreview() {
  if (nodes.turnPreview) nodes.turnPreview.hidden = true;
}

/**
 * 滚到某一条消息上。目标按 seq 找：消息与图都带 data-seq，图中意的是"这一轮的开头"。
 *
 * **落在判定线上，不落在中间**：刻度亮哪一轮是按判定线算的（activeTurnOf），
 * 居中会让判定线落回上一轮，指示器就停在前一轮。落点对齐后两者才对得上。
 *
 * 找不到（还没加载）就什么都不做 —— 调用方负责先把历史翻到它。
 */
export function scrollToSeq(seq) {
  const list = nodes.messages;
  let hit = null;
  for (const node of list.querySelectorAll("[data-seq]")) {
    if (Number(node.dataset.seq) >= seq) {
      hit = node;
      break;
    }
  }
  if (!hit) return;
  // 直接把差值补进 scrollTop：贴到底或贴到顶时浏览器自己会夹住，不需要额外判断。
  list.scrollTop += hit.getBoundingClientRect().top - activeLineOf(list) + JUMP_AHEAD;
  hit.classList.add("jump-target");
  setTimeout(() => hit.classList.remove("jump-target"), 1200);
  // 滚动位置本来就在这条上时不会触发 scroll 事件，这里补一次同步。
  if (lastState) nextFrame(() => syncActiveTurn(lastState));
}

