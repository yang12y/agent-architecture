/**
 * state -> DOM。**只读状态**：不读网络、不改状态。
 *
 * 事件全用委托绑在容器上，渲染过程里不挂新闭包 —— 重建多少次都不会泄漏。
 */

import { renderMarkdown } from "./markdown.js";

const ROLE_LABEL = { user: "你", assistant: "模型", tool: "工具结果" };
const CONNECTION_LABEL = { connecting: "连接中…", ready: "已连接", offline: "已断开，重连中…" };

const nodes = {};
let actions = {};
let renderedVersion = -1;
let renderedApprovalKey = "";

// 已经画进 DOM 的消息条数，以及"往前翻页"的代数 —— 增量渲染靠这两个判断该追加、该插头、
// 还是该整份重建。
let renderedCount = 0;
let renderedPage = 0;

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

export function mount(nextActions) {
  actions = nextActions;
  nodes.sessions = document.getElementById("sessions");
  nodes.messages = document.getElementById("messages");
  nodes.approval = document.getElementById("approval-layer");
  nodes.toast = document.getElementById("toast");
  nodes.title = document.getElementById("session-title");
  nodes.meta = document.getElementById("session-meta");
  nodes.connection = document.getElementById("connection");
  nodes.stats = document.getElementById("stats");
  nodes.composer = document.getElementById("composer");
  nodes.input = document.getElementById("input");
  nodes.send = document.getElementById("send");
  nodes.userId = document.getElementById("user-id");

  document.getElementById("new-session").addEventListener("click", () => actions.newSession());

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
  nodes.messages.addEventListener("scroll", () => {
    if (nodes.messages.scrollTop > 40) return;
    actions.loadEarlier();
  });
  nodes.messages.addEventListener("click", (event) => {
    if (event.target.closest("[data-earlier]")) {
      actions.loadEarlier();
      return;
    }
    const copy = event.target.closest("[data-copy]");
    if (copy) copyMessage(copy);
  });

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
  nodes.input.value = "";
  autosize();
  actions.send(text);
}

export function render(state) {
  const stick = isNearBottom(nodes.messages);
  renderHeader(state);
  renderSessions(state);
  renderMessages(state);
  renderThought(state);
  renderDraft(state, stick);
  renderStats(state);
  renderApprovals(state);
  renderToast(state);
  if (stick) nodes.messages.scrollTop = nodes.messages.scrollHeight;
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

function renderOne(message, names) {
  return message.role === "tool" ? renderToolResult(message, names) : renderMessage(message);
}

/** 流式那条草稿和思考是挂在列表尾巴上的临时节点，插新消息之前得先撤掉，回头会重新挂。 */
function dropTransient(list) {
  for (const node of list.querySelectorAll(".draft, .thought--live")) {
    node.closest(".message")?.remove();
  }
}

/** 顶上那个"加载更早"的按钮跟着 hasMore 走，永远排在最前面。 */
function syncEarlier(list, state) {
  if (!state.hasMore) {
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
  const total = state.messages.length;

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
    for (const message of state.messages) list.append(renderOne(message, names));
    renderedCount = total;
    syncEarlier(list, state);
    return;
  }

  if (state.messagesPage !== renderedPage) {
    renderedPage = state.messagesPage;
    const older = state.messages.slice(0, total - renderedCount);
    if (renderedCount === 0) list.replaceChildren();
    else dropTransient(list);
    const names = toolNames(state.messages);
    const keep = list.scrollHeight - list.scrollTop;
    list.prepend(...older.map((message) => renderOne(message, names)));
    list.scrollTop = list.scrollHeight - keep;
    renderedCount = total;
    syncEarlier(list, state);
    return;
  }

  if (total === renderedCount) return;

  if (total < renderedCount) {
    // 对不上了就整份重建，别猜。
    renderedVersion = -1;
    renderMessages(state);
    return;
  }

  if (renderedCount === 0) list.replaceChildren();
  else dropTransient(list);
  const names = toolNames(state.messages);
  for (const message of state.messages.slice(renderedCount)) list.append(renderOne(message, names));
  renderedCount = total;
  syncEarlier(list, state);
}

function renderMessage(message) {
  const wrapper = el("article", "message message--" + message.role);
  wrapper.append(el("div", "role", ROLE_LABEL[message.role] ?? message.role));

  // 这条消息的正文攒起来，末尾那个复制键要的就是它。
  const body = [];
  for (const part of message.content) {
    if (part.type === "thinking") {
      wrapper.append(renderThinking(part));
    } else if (part.type === "text") {
      body.push(part.text);
      // 模型的回答按 Markdown 画；用户自己的话原样显示 —— 别把人打的字当格式解读。
      if (message.role === "assistant") {
        const bubble = el("div", "bubble bubble--md");
        bubble.append(renderMarkdown(part.text));
        wrapper.append(bubble);
      } else {
        wrapper.append(el("div", "bubble", part.text));
      }
    } else if (part.type === "tool_call") {
      wrapper.append(renderToolCall(part));
    }
  }
  if (body.length > 0) wrapper.append(copyButton(body.join("\n\n")));
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
  wrapper.append(card);
  return wrapper;
}

function renderThought(state) {
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
