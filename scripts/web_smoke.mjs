/**
 * 前端启动冒烟：用一个"什么都有"的 Proxy 顶替 DOM，把 src/web/js 的模块图真跑一遍。
 *
 * `node --check` 只查语法 —— 引用了已经被删掉的变量（改名时漏掉的 `socket.connect()` 这类）
 * 它看不出来，而浏览器里的表现就是"页面空白、会话列表不出现"。
 *
 * 用法：node scripts/web_smoke.mjs
 */

// 任何属性都拿得到、调得动、还能当构造器用；拿来顶 DOM。
function anything() {
  const target = function () {};
  target.textContent = "";
  target.innerHTML = "";
  target.value = "";
  target.hidden = false;
  target.dataset = {};
  target.scrollTop = 0;
  target.scrollHeight = 0;
  target.clientHeight = 0;
  target.open = false;
  target.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  return new Proxy(target, {
    get(object, key) {
      if (key === Symbol.iterator) return function* () {};
      if (key === Symbol.toPrimitive) return () => "";
      if (key === "then") return undefined;
      if (key in object) return object[key];
      return anything();
    },
    set(object, key, value) {
      try {
        object[key] = value;
      } catch {
        // 函数自带的只读属性（name / length / caller…）写不进去。真 DOM 里能写，
        // 假件不该比浏览器更严 —— 跳过就行。
      }
      return true;
    },
    apply() {
      return anything();
    },
    construct() {
      return anything();
    },
  });
}

const failures = [];
const realError = console.error;
console.error = (...args) => failures.push(args.map(String).join(" "));

globalThis.document = anything();
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.location = { protocol: "http:", host: "127.0.0.1:8000" };
globalThis.localStorage = { getItem: () => "user_smoke", setItem() {} };
globalThis.EventSource = class {
  addEventListener() {}
  close() {}
};

// 按 URL 编假响应：够 boot 走完"列会话 → 打开第一个 → 开会话的流"这条路。
globalThis.fetch = async (url) => {
  const path = String(url).split("?")[0];
  let body = { approvals: [], questions: [] };
  if (path === "/sessions") {
    body = { sessions: [{ session_id: "sess_smoke" }], session_id: "sess_smoke" };
  } else if (path !== "/approvals" && path !== "/questions" && path.startsWith("/sessions/")) {
    body = {
      session_id: "sess_smoke",
      messages: [],
      charts: [],
      turns: [],
      is_running: false,
      stats: { traces: 0, turns: 0, context_tokens: 0, max_tokens: 1000000 },
    };
  }
  return { ok: true, status: 200, json: async () => body };
};

try {
  await import("../src/web/js/main.js");
  // 让 boot() 那几个 await 跑完。
  await new Promise((resolve) => setTimeout(resolve, 100));
} catch (error) {
  failures.push(String(error));
}

// ---- store 契约：三个记号各管一种走法，视图的增量渲染全靠它们 ----------------
try {
  const { createStore } = await import("../src/web/js/store.js");
  const store = createStore("user_smoke");
  const check = (ok, message) => {
    if (!ok) failures.push("store: " + message);
  };
  const message = (id) => ({ entry_id: id, entry_seq: Number(id.slice(1)), role: "user", content: [] });

  store.setMessages([message("e1")], { hasMore: true });
  check(store.state.messagesVersion === 1, "setMessages 应该换版本");
  check(store.state.hasMore === true, "hasMore 应该跟着来");

  check(store.applyEvent("assistant_message", {}) === "tail", "assistant_message 只补尾巴");
  check(store.applyEvent("compaction", {}) === "reload", "compaction 要整份重来");
  check(store.applyEvent("assistant_delta", { text: "x" }) === null, "增量不该触发拉取");

  store.applyEvent("question_required", { request_id: "q1" });
  check(store.state.questions.length === 1, "提问事件要进待回答列表");

  const version = store.state.messagesVersion;
  store.appendMessages([message("e2")]);
  check(store.state.messagesVersion === version, "追加不动版本");
  check(store.state.messages.length === 2, "追加要真的进去");

  // 补拉是按"游标之后"取的，两次请求撞上同一段时同一条会进来两遍。
  store.appendMessages([message("e2")]);
  check(store.state.messages.length === 2, "重复的 entry_id 必须被丢掉");

  const page = store.state.messagesPage;
  store.prependMessages([message("e0")], { hasMore: false });
  check(store.state.messagesPage === page + 1, "往前翻要换页号");
  check(store.state.hasMore === false, "hasMore 跟着这一页走");
  check(store.state.messages[0].entry_id === "e0", "更早的排在前面");
  check(store.state.messages.length === 3, "往前翻不该丢东西");
} catch (error) {
  failures.push("store: " + String(error));
}

// ---- 提问卡片：有待答的提问时重绘不能崩（document 是假件，只验代码路径） ----
try {
  const view = await import("../src/web/js/view.js");
  const { createStore } = await import("../src/web/js/store.js");
  const store = createStore("user_smoke");
  store.applyEvent("question_required", {
    request_id: "q1",
    tool_call_id: "c1",
    tool_name: "ask_user",
    questions: [
      {
        id: "go",
        header: "确认",
        question: "继续吗？",
        options: [
          { label: "继续", description: "往下做" },
          { label: "先停", description: null },
        ],
        multi_select: false,
      },
      { id: "why", header: null, question: "为什么？", options: [], multi_select: false },
    ],
  });
  view.render(store.state);
} catch (error) {
  failures.push("view: 提问卡片渲染崩了 " + String(error));
}

// ---- Markdown：常见块能拆对，危险的链接要退化成文本 ------------------------
try {
  const { parseMarkdown } = await import("../src/web/js/markdown.js");
  const check = (ok, message) => {
    if (!ok) failures.push("markdown: " + message);
  };

  const [heading] = parseMarkdown("## 标题");
  check(heading.tag === "h3", "标题要降一级（气泡里从 h2 起）");

  const [fence] = parseMarkdown(["```python", "print(1)", "```"].join("\n"));
  check(fence.tag === "pre", "围栏代码块要变成 pre");
  check(fence.children[0].tag === "code", "pre 里面是 code");
  check(fence.children[0].children[0].text === "print(1)", "代码原样保留");

  const [paragraph] = parseMarkdown("先看 **这里**");
  check(paragraph.tag === "p", "普通文字是段落");
  check(paragraph.children.some((n) => n.tag === "strong"), "粗体要认出来");

  const [danger] = parseMarkdown("[点我](javascript:alert)");
  check(!JSON.stringify(danger).includes("\"a\""), "javascript: 链接必须退化成文本");

  const [safe] = parseMarkdown("[文档](https://example.com)");
  check(safe.children[0].tag === "a", "https 链接要生成 <a>");

  // 流式渲染时围栏还没闭合，代码也得当场就是代码块。
  const fenceMark = "\u0060".repeat(3);
  const [open] = parseMarkdown([fenceMark + "js", "const a = 1"].join("\n"));
  check(open.tag === "pre", "没闭合的围栏也要按代码块渲染");

  // 模型给统计结论时经常直接甩一张管道表格。
  const [table] = parseMarkdown(
    ["| 组织 | 申报 |", "|---|---:|", "| 电解一厂 | 170 |", "| 合计 | 958 |"].join("\n"),
  );
  check(table.tag === "div" && table.className === "md-table", "表格要包一层可横向滚动");
  const grid = table.children[0];
  check(grid.tag === "table", "里面是真的 table");
  check(grid.children[0].tag === "thead" && grid.children[1].tag === "tbody", "表头表体分开");
  check(grid.children[1].children.length === 2, "两行数据都要在");
  check(grid.children[0].children[0].children[1].className === "md-right", "---: 要右对齐");
  check(
    grid.children[1].children[0].children[0].children[0].text === "电解一厂",
    "单元格文字原样保留",
  );

  // 流式输出时经常只有表头、分隔行还没来：这一行必须当普通段落吃掉，
  // 绝不能空转到标签页 OOM。
  const partial = parseMarkdown(["| 组织 | 申报 |", "| 电解一厂 |"].join("\n"));
  check(partial.length === 2 && partial[0].tag === "p", "残缺表格要退化成段落，不能死循环");
  check(JSON.stringify(partial).includes("电解一厂"), "残缺表格的文字不能丢");

  const headerOnly = parseMarkdown("| 只写了一行表头");
  check(headerOnly.length === 1 && headerOnly[0].tag === "p", "单行竖线文本按段落渲染");
} catch (error) {
  failures.push("markdown: " + String(error));
}

// ---- 待办面板：有清单能画、没变化不重画、清空后整块藏起来 ----------------
try {
  const view = await import("../src/web/js/view.js");
  const { createStore } = await import("../src/web/js/store.js");
  const store = createStore("user_smoke");
  view.mount({
    newSession() {},
    openSession() {},
    deleteSession() {},
    send() {},
    interrupt() {},
    loadEarlier() {},
    approve() {},
    reload() {},
  });
  store.setTodos([
    { content: "甲", status: "pending" },
    { content: "乙", status: "completed" },
  ]);
  view.render(store.state);
  view.render(store.state);
  store.setTodos([]);
  view.render(store.state);
} catch (error) {
  failures.push("todos: " + String(error));
}


// ---- 图表与轮次导航：真攒一棵 DOM，验顺序、刻度、去重 ----------------------
//
// 前面那些用例用的是"什么都有"的万能 Proxy —— 它只能证明代码路径不炸，
// 看不到 append 的先后。这一节换一棵会记账的假 DOM，把时间线的顺序确实钉住。
function makeClassList(node) {
  const set = new Set();
  return {
    add: (...names) => names.forEach((name) => set.add(name)),
    remove: (...names) => names.forEach((name) => set.delete(name)),
    toggle: (name, force) => {
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name);
      else set.delete(name);
      return on;
    },
    contains: (name) => set.has(name) || String(node.className).split(" ").includes(name),
  };
}

function makeNode(tag, namespace, nodeType) {
  const node = {
    tagName: String(tag).toUpperCase(),
    namespace: namespace || null,
    // 3 = 文本节点，1 = 元素。搜索靠它区分。
    nodeType: nodeType ?? 1,
    children: [],
    parent: null,
    parentNode: null,
    dataset: {},
    style: {},
    attributes: {},
    textContent: "",
    className: "",
    hidden: false,
    open: false,
    disabled: false,
    value: "",
    title: "",
    placeholder: "",
    type: "",
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    clientWidth: 0,
  };
  // 真 DOM 里文本节点只在 childNodes 里；这里让两个名字指同一份数组。
  Object.defineProperty(node, "childNodes", { get: () => node.children });
  // 元素上写 textContent = "..." 在真 DOM 里会**造一个文本节点**；搜索要按文本节点切，
  // 所以这里也照做（文本节点自己仍是普通字段）。
  if ((nodeType ?? 1) === 1) {
    Object.defineProperty(node, "textContent", {
      get: () => node.children.map((kid) => String(kid.textContent ?? "")).join(""),
      set: (value) => {
        node.children = [];
        const text = String(value ?? "");
        if (text) node.append(makeText(text));
      },
    });
  }
  node.classList = makeClassList(node);
  const adopt = (kid) => {
    kid.parent = node;
    kid.parentNode = node;
    return kid;
  };
  // 真 DOM 里 append 一个 fragment 是把它**里面的孩子**搬进来，fragment 自己不落地。
  const flatten = (kids) => {
    const out = [];
    for (const kid of kids) {
      if (kid.tagName === "#FRAGMENT") out.push(...kid.children);
      else out.push(kid);
    }
    return out;
  };
  node.append = (...kids) => {
    for (const kid of flatten(kids)) node.children.push(adopt(kid));
  };
  node.prepend = (...kids) => {
    const list = flatten(kids);
    for (const kid of list) adopt(kid);
    node.children.unshift(...list);
  };
  node.appendChild = (kid) => {
    node.append(kid);
    return kid;
  };
  node.replaceChildren = (...kids) => {
    node.children = [];
    node.append(...kids);
  };
  node.replaceChild = (next, old) => {
    const at = node.children.indexOf(old);
    if (at >= 0) node.children[at] = adopt(next);
    old.parent = null;
    old.parentNode = null;
    return old;
  };
  node.remove = () => {
    if (!node.parentNode) return;
    node.parentNode.children = node.parentNode.children.filter((child) => child !== node);
    node.parentNode = null;
    node.parent = null;
  };
  // 文本节点用它切开自己：搜索要在一段文字中间挖出 <mark>。
  node.splitText = (offset) => {
    const text = String(node.textContent ?? "");
    const tail = makeText(String(text.slice(offset)));
    node.textContent = text.slice(0, offset);
    const parent = node.parentNode;
    if (parent) {
      const at = parent.children.indexOf(node);
      parent.children.splice(at + 1, 0, tail);
      // 注意：这里不能复用 adopt —— 它闭包的是**文本节点自己**，会把尾巴挂错父节点。
      tail.parent = parent;
      tail.parentNode = parent;
    }
    return tail;
  };
  node.setAttribute = (key, value) => {
    const text = String(value);
    node.attributes[key] = text;
    // 真 DOM 里 setAttribute("class"/"data-*") 会同步到 className / dataset，
    // 假件不跟着做的话，SVG 上的 class 用选择器就永远找不到。
    if (key === "class") node.className = text;
    else if (key.startsWith("data-")) {
      node.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = text;
    }
  };
  node.getAttribute = (key) => node.attributes[key] ?? null;
  node.removeAttribute = (key) => {
    delete node.attributes[key];
  };
  const listeners = new Map();
  node.addEventListener = (type, handler) => {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(handler);
  };
  node.fire = (type, event) => {
    for (const handler of listeners.get(type) ?? []) {
      handler({ target: node, preventDefault() {}, ...event });
    }
  };
  node.focus = () => {};
  node.blur = () => {};
  node.select = () => {};
  node.scrollIntoView = () => {};
  node.getBoundingClientRect = () => ({ top: 0, left: 0, right: 0, bottom: 0 });
  node.closest = (selector) => {
    const test = selectorMatcher(selector);
    let current = node;
    while (current) {
      if (test(current)) return current;
      current = current.parentNode;
    }
    return null;
  };
  node.querySelector = (selector) => node.querySelectorAll(selector)[0] ?? null;
  node.querySelectorAll = (selector) => {
    const test = selectorMatcher(selector);
    const found = [];
    const walk = (current) => {
      for (const child of current.children) {
        if (test(child)) found.push(child);
        walk(child);
      }
    };
    walk(node);
    return found;
  };
  return node;
}

function makeText(text) {
  const node = makeNode("#text", null, 3);
  node.textContent = String(text);
  return node;
}

// 只认这个小前端实际用到的几种选择器：属性、类、逗号并列、标签。
function selectorMatcher(selector) {
  const text = String(selector).trim();
  if (text.includes(",")) {
    const tests = text.split(",").map((part) => selectorMatcher(part));
    return (node) => tests.some((test) => test(node));
  }
  const attribute = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(text);
  if (attribute) {
    // data-tool-call 在 DOM 里是 dataset.toolCall —— 先摘掉 data- 前缀再驼峰化。
    const key = attribute[1]
      .replace(/^data-/, "")
      .replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    return (node) =>
      key in node.dataset &&
      (attribute[2] === undefined || String(node.dataset[key]) === attribute[2]);
  }
  if (text.startsWith(".")) {
    const name = text.slice(1);
    return (node) => node.classList.contains(name);
  }
  return (node) => node.tagName === text.toUpperCase();
}

function installRecordingDom() {
  const byId = new Map();
  const doc = makeNode("#document");
  globalThis.document = Object.assign(doc, {
    createElement: (tag) => makeNode(tag),
    createElementNS: (namespace, tag) => makeNode(tag, namespace),
    createTextNode: (text) => makeText(text),
    createDocumentFragment: () => makeNode("#fragment"),
    getElementById: (id) => {
      if (!byId.has(id)) byId.set(id, makeNode("div"));
      return byId.get(id);
    },
  });
  return byId;
}

try {
  const byId = installRecordingDom();
  const view = await import("../src/web/js/view.js");
  const { createStore } = await import("../src/web/js/store.js");
  const check = (ok, message) => {
    if (!ok) failures.push("图表: " + message);
  };
  const checkTraces = (ok, message) => {
    if (!ok) failures.push("轨迹: " + message);
  };

  // 导出走的是 actions.exportDocx —— 这里换成收件箱，好断言前端拆出来的块。
  const exported = [];
  // 分叉键同理：只验"点了之后把哪一轮的 seq 交出去"。
  const forked = [];
  view.mount({
    jumpToTurn() {},
    loadEarlier() {},
    answer() {},
    answerQuestion() {},
    exportDocx(payload) {
      exported.push(payload);
    },
    forkAt(turnSeq) {
      forked.push(turnSeq);
    },
  });

  const message = (seq, role) => ({
    entry_id: "e" + seq,
    entry_seq: seq,
    timestamp: "2025-01-01T00:00:00+00:00",
    role,
    content: [{ type: "text", text: "第" + seq + "条" }],
  });
  const chart = (seq) => ({
    entry_id: "e" + seq,
    entry_seq: seq,
    chart_id: "c_call_" + seq,
    tool_call_id: "call_" + seq,
    kind: "line",
    title: "压力",
    x: ["a", "b", "c"],
    series: [{ name: "1#", values: [1, 2.5, 4] }],
    x_title: null,
    y_title: "MPa",
  });

  const store = createStore("user_smoke");
  const seqs = () => byId.get("messages").children.map((node) => Number(node.dataset.seq));
  // 在一棵节点里找图 —— 图现在挂在回答那条消息里面，不再是列表里的兄弟节点。
  const figureIn = (node) => {
    let hit = null;
    const walk = (current) => {
      if (hit) return;
      if (String(current.className).includes("chart")) {
        hit = current;
        return;
      }
      for (const child of current.children) walk(child);
    };
    if (node) walk(node);
    return hit;
  };

  store.setMessages([message(1, "user"), message(3, "assistant")], { hasMore: false });
  store.setCharts([chart(2)]);
  store.setTurns([{ turn: 1, seq: 1, prompt: "画个图", response: "画好了" }]);
  view.render(store.state);

  check(
    JSON.stringify(seqs()) === JSON.stringify([1, 3]),
    "图不该单独占一条消息，实际 " + JSON.stringify(seqs()),
  );
  const figure = figureIn(byId.get("messages").children[1]);
  check(Boolean(figure), "图要挂在模型回答的那条消息里");
  const body = figure && figure.children.find((node) => node.className === "chart-body");
  check(Boolean(body && body.children[0].tagName === "SVG"), "figure 里要有 svg");
  check(
    Boolean(body && body.children[0].children.length > 5),
    "svg 里要有网格、刻度与折线（不能只画一个空框）",
  );
  check(
    Boolean(figure && figure.children.some((node) => node.className === "chart-data")),
    "图下面要有可展开的数据表",
  );

  const ticks = byId.get("turns").children;
  check(ticks.length === 1, "一轮一个刻度，实际 " + ticks.length + " 个");
  check(ticks[0].dataset.turnSeq === "1", "刻度的 seq 要来自大纲");
  check(byId.get("turns").hidden === false, "有轮次时刻度栏要显示");
  check(byId.get("transcript").dataset.turns === "on", "有轮次时对话区要留出刻度栏");

  // 图先到、回答后到：图先单独摆着，回答一到就并进去（整份重建一次）。
  const late = createStore("user_smoke");
  late.setMessages([message(1, "user")], { hasMore: false });
  late.setCharts([chart(3)]);
  view.render(late.state);
  check(
    JSON.stringify(late.state.charts.length) === "1",
    "回答还没来时图要先进状态，别等",
  );
  check(
    JSON.stringify(late.state.messages.map((row) => row.entry_seq)) === JSON.stringify([1]),
    "状态里的消息不受图影响",
  );
  late.appendMessages([message(2, "assistant")]);
  view.render(late.state);
  const lateSeqs = byId.get("messages").children.map((node) => Number(node.dataset.seq));
  check(
    JSON.stringify(lateSeqs) === JSON.stringify([1, 2]),
    "后到的回答要把图收进去，实际 " + JSON.stringify(lateSeqs),
  );
  check(
    Boolean(figureIn(byId.get("messages").children[1])),
    "后到的回答也要能收下前面画的图（重建一次）",
  );


  // 工具调用：派发点亮、结果摘掉。卡片是增量画的，状态必须靠每次 render 对齐。
  // 用一组没出现过的 entry_id：视图是挂在模块上的单例，撞了 id 它不会重建。
  const runs = createStore("user_smoke");
  runs.setMessages(
    [
      {
        entry_id: "e51",
        entry_seq: 51,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "user",
        content: [{ type: "text", text: "读个文件" }],
      },
      {
        entry_id: "e52",
        entry_seq: 52,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "assistant",
        content: [{ type: "tool_call", tool_call_id: "call_1", name: "read", arguments: {} }],
      },
    ],
    { hasMore: false },
  );
  check(
    runs.applyEvent("tool_call_start", { tool_call_id: "call_1" }) === null,
    "派发事件不该触发拉取",
  );
  view.render(runs.state);
  const cards = byId.get("messages").querySelectorAll("[data-tool-call]");
  const running = cards.find((node) => node.dataset.toolCall === "call:call_1");
  check(Boolean(running), "工具调用卡片要带 data-tool-call");
  check(
    Boolean(running) && running.dataset.state === "running",
    "派发之后卡片要是 running，实际 " + (running && running.dataset.state),
  );
  const status = running && running.querySelector(".tool-status");
  check(
    Boolean(status && /^运行中 · \d+s$/.test(status.textContent)),
    "运行中要读出已跑几秒，实际 " + (status && status.textContent),
  );
  check(
    runs.applyEvent("tool_result", { tool_call_id: "call_1", ok: true }) === "tail",
    "结果回来要补一次尾巴",
  );
  view.render(runs.state);
  check(
    Boolean(running) && running.dataset.state === "ok",
    "结果回来之后要摘掉 running，实际 " + (running && running.dataset.state),
  );
  check(
    Boolean(status && status.textContent === ""),
    "结果回来之后要把读秒清掉，实际 " + (status && status.textContent),
  );




  // ---- 分叉：答案底下那颗键，点它把这一轮起点的 seq 交出去 ----
  //
  // 分叉的落点是"这一轮结束"，前端只需要交出这一轮**起点**那条用户消息的 seq。
  const forkCase = createStore("user_smoke");
  forkCase.setMessages(
    [
      { entry_id: "e61", entry_seq: 61, timestamp: "2025-01-01T00:00:00+00:00", role: "user", content: [{ type: "text", text: "第一种问法" }] },
      { entry_id: "e62", entry_seq: 62, timestamp: "2025-01-01T00:00:00+00:00", role: "assistant", content: [{ type: "text", text: "第一种问法的回答" }] },
      { entry_id: "e63", entry_seq: 63, timestamp: "2025-01-01T00:00:00+00:00", role: "user", content: [{ type: "text", text: "第二种问法" }] },
      { entry_id: "e64", entry_seq: 64, timestamp: "2025-01-01T00:00:00+00:00", role: "assistant", content: [{ type: "text", text: "第二种问法的回答" }] },
    ],
    { hasMore: false },
  );
  forkCase.setTurns([
    { turn: 1, seq: 61, prompt: "第一种问法", response: "第一种问法的回答" },
    { turn: 2, seq: 63, prompt: "第二种问法", response: "第二种问法的回答" },
  ]);
  view.render(forkCase.state);

  const forkButtons = byId.get("messages").querySelectorAll("[data-fork]");
  check(forkButtons.length === 2, "每一轮的最终回答上要有一颗分叉键，实际 " + forkButtons.length + " 颗");
  check(
    forkButtons[0].dataset.fork === "61" && forkButtons[1].dataset.fork === "63",
    "分叉键带的是这一轮起点的 seq，实际 " + forkButtons.map((node) => node.dataset.fork).join(","),
  );
  check(Boolean(forkButtons[0].title), "分叉键要有 title 说明它做什么");

  byId.get("messages").fire("click", { target: forkButtons[1] });
  check(
    JSON.stringify(forked) === JSON.stringify([63]),
    "点第二颗分叉键要交出 63，实际 " + JSON.stringify(forked),
  );

// ---- 轨迹瀑布：一条 trace 一列横条，位置与长度按耗时比例摆 ----
  const traceCase = createStore("user_smoke");
  traceCase.setTraces([
    {
      trace_id: "t2",
      started_at: "2025-01-01T12:00:10+00:00",
      ended_at: "2025-01-01T12:00:12+00:00",
      status: "completed",
      reason: null,
      turns: 2,
      context_tokens: 1234,
      duration_ms: 2000,
      spans: [
        { kind: "site", name: "llm:before", turn: 1, start_ms: 0, elapsed_ms: 20, detail: "" },
        { kind: "llm", name: "llm", turn: 1, start_ms: 25, elapsed_ms: 1900, detail: "" },
        { kind: "site", name: "tool:during", turn: 1, start_ms: 1930, elapsed_ms: 60, detail: "read" },
      ],
    },
    {
      trace_id: "t1",
      started_at: "2025-01-01T12:00:00+00:00",
      ended_at: "2025-01-01T12:00:00+00:00",
      status: "failed",
      reason: "模型超时",
      turns: 1,
      context_tokens: 0,
      duration_ms: 500,
      spans: [{ kind: "site", name: "turn:before", turn: 1, start_ms: 0, elapsed_ms: 2, detail: "" }],
    },
  ]);
  traceCase.setTracesOpen(true);
  view.render(traceCase.state);

  // 假 DOM 不解析 HTML：#traces-body 不会自己挂到 #traces 下面，所以从 body 里找。
  checkTraces(byId.get("traces").hidden === false, "打开之后面板要露出来");
  checkTraces(byId.get("traces-open").dataset.open === "true", "抬头那颗键要标成打开");
  const traces = byId.get("traces-body").querySelectorAll(".trace");
  checkTraces(traces.length === 2, "两条 trace 要画两段，实际 " + traces.length);
  const spanRows = traces[0].querySelectorAll(".trace-row");
  checkTraces(spanRows.length === 3, "第一段三格，实际 " + spanRows.length);
  checkTraces(
    JSON.stringify(spanRows.map((row) => row.querySelector(".trace-label").textContent)) ===
      JSON.stringify(["取材", "模型调用", "工具执行"]),
    "位点名要翻成人话，实际 " + JSON.stringify(spanRows.map((row) => row.querySelector(".trace-label").textContent)),
  );
  const bars = spanRows.map((row) => row.querySelector(".trace-bar"));
  checkTraces(bars[1].dataset.kind === "llm", "模型调用那一格要有自己的 kind");
  checkTraces(bars[1].style.left === "1.25%", "起点按比例摆，实际 " + bars[1].style.left);
  checkTraces(bars[1].style.width === "95%", "长度按比例摆，实际 " + bars[1].style.width);
  checkTraces(bars[0].style.width === "1%", "20ms / 2000ms 是 1%，实际 " + bars[0].style.width);
  checkTraces(spanRows[2].title.includes("read"), "工具名要进 title，实际 " + spanRows[2].title);
  checkTraces(
    spanRows[1].querySelector(".trace-ms").textContent === "1.9s",
    "毫秒要换成秒，实际 " + spanRows[1].querySelector(".trace-ms").textContent,
  );
  checkTraces(
    traces[1].querySelectorAll(".trace-row").length === 0,
    "旧的那条默认折起来：明细不进 DOM",
  );
  checkTraces(
    traces[0].querySelector("[data-trace]").dataset.open === "1" &&
      traces[0].querySelector(".trace-caret").textContent === "▾",
    "最新那条默认展开，折角朝下",
  );
  checkTraces(
    traces[1].querySelector("[data-trace]").getAttribute("aria-expanded") === "false",
    "折起来的那条要让读屏知道",
  );

  // 点抬头展开 / 折回。每次都会重画，所以每一步都得重新取节点。
  const clickHead = (id) => {
    const head = byId.get("traces-body").querySelector('[data-trace="' + id + '"]');
    checkTraces(Boolean(head), "找不到 trace 抬头：" + id);
    byId.get("traces-body").fire("click", { target: head });
  };
  clickHead("t1");
  checkTraces(
    byId.get("traces-body").querySelectorAll(".trace")[1].querySelectorAll(".trace-row").length === 1,
    "点开之后要画出明细",
  );
  checkTraces(
    byId.get("traces-body").querySelector('[data-trace="t1"]').dataset.open === "1",
    "展开状态要写在 data-open 上",
  );
  clickHead("t1");
  checkTraces(
    byId.get("traces-body").querySelectorAll(".trace-row").length === 3,
    "折回去之后只剩最新那条的明细，实际 " +
      byId.get("traces-body").querySelectorAll(".trace-row").length,
  );
  checkTraces(
    byId.get("traces-body").querySelector('[data-trace="t1"]').getAttribute("aria-expanded") ===
      "false",
    "折回去之后 aria-expanded 要跟着变",
  );
  clickHead("t2");
  checkTraces(
    byId.get("traces-body").querySelectorAll(".trace-row").length === 0,
    "最新那条也能折，两条都折起来时一行明细都不该留，实际 " +
      byId.get("traces-body").querySelectorAll(".trace-row").length,
  );
  checkTraces(
    traces[0].querySelector(".trace-total").textContent === "2.0s",
    "总耗时要有，实际 " + traces[0].querySelector(".trace-total").textContent,
  );
  checkTraces(
    traces[1].querySelector(".trace-status").dataset.status === "failed",
    "失败的那条要标成失败",
  );

// 还没取到的那一瞬间要跟"确实没有"分开：一个说读取中，一个说还没有记录。
  const loadingTraces = createStore("user_smoke");
  loadingTraces.setTracesOpen(true);
  loadingTraces.setTraces(null);
  view.render(loadingTraces.state);
  checkTraces(
    byId.get("traces-body").children.length === 1 &&
      byId.get("traces-body").children[0].className === "traces-loading",
    "没取到时要说读取中，实际 " + JSON.stringify(byId.get("traces-body").children.map((n) => n.className)),
  );
  loadingTraces.setTraces([]);
  view.render(loadingTraces.state);
  checkTraces(
    byId.get("traces-body").children[0].className === "traces-empty",
    "取到了但是空的，要说还没有记录",
  );

  // 一条都还没有：给一句说明，别留空白。
  const emptyTraces = createStore("user_smoke");
  emptyTraces.setTraces([]);
  emptyTraces.setTracesOpen(true);
  view.render(emptyTraces.state);
  checkTraces(
    byId.get("traces-body").children.length === 1 &&
      byId.get("traces-body").children[0].className === "traces-empty",
    "没有数据时要有一句说明",
  );

  // 关掉之后面板要收起来。
  emptyTraces.setTracesOpen(false);
  view.render(emptyTraces.state);
  checkTraces(byId.get("traces").hidden === true, "关掉之后要收起来");

  // 侧栏：分叉出来的会话在标题下面标一句来源。
  const originCase = createStore("user_smoke");
  originCase.state.sessions = [
    { session_id: "sess_origin", title: "原会话", parent_session_id: null },
    { session_id: "sess_fork", title: "原会话", parent_session_id: "sess_origin" },
    { session_id: "sess_lost", title: "孤零零", parent_session_id: "sess_gone" },
  ];
  view.render(originCase.state);
  const rows = byId.get("sessions").children;
  const parents = rows.map((row) => {
    const label = row.querySelector(".parent");
    return label ? label.textContent : "";
  });
  check(parents[0] === "", "顶层会话不该有来源那行，实际 " + JSON.stringify(parents[0]));
  check(parents[1] === "分叉自 原会话", "分叉会话要标出父会话标题，实际 " + JSON.stringify(parents[1]));
  check(
    parents[2] === "分叉自 已删除的会话",
    "父会话不在列表里要说一声，实际 " + JSON.stringify(parents[2]),
  );

  // ---- 图表交互：准星、图例、放大 ----
  const checkChart = (ok, message) => {
    if (!ok) failures.push("图表交互: " + message);
  };
  const { renderChartNode, chartTsv } = await import("../src/web/js/chart.js");

  checkChart(
    chartTsv({
      x: ["a", "b"],
      series: [
        { name: "甲", values: [1, 2.5] },
        { name: "乙", values: [3, 4] },
      ],
    }) === "类别\t甲\t乙\na\t1\t3\nb\t2.5\t4",
    "TSV 形状不对：" +
      JSON.stringify(
        chartTsv({
          x: ["a", "b"],
          series: [
            { name: "甲", values: [1, 2.5] },
            { name: "乙", values: [3, 4] },
          ],
        }),
      ),
  );

  const two = renderChartNode({
    chart_id: "c_two",
    kind: "line",
    title: "两条",
    x: ["a", "b", "c"],
    series: [
      { name: "甲", values: [1, 2, 3] },
      { name: "乙", values: [3, 2, 1] },
    ],
    x_title: null,
    y_title: null,
  });

  checkChart(
    Boolean(two.querySelector(".chart-crosshair")),
    "折线图要有准星（悬停读数用）",
  );
  checkChart(Boolean(two.querySelector(".chart-band")), "折线图要有高亮带");
  checkChart(
    two.querySelectorAll(".chart-series").length === 2,
    "一条系列一个分组，实际 " + two.querySelectorAll(".chart-series").length,
  );

  const legendButtons = two.querySelectorAll("[data-legend]");
  checkChart(legendButtons.length === 2, "两条系列要给两个可点的图例，实际 " + legendButtons.length);

  // 真 DOM 里点击会冒泡到 figure 上的委托处理器；假件不冒泡，直接以它为事件源。
  two.fire("click", { target: legendButtons[0] });
  const groups = two.querySelectorAll(".chart-series");
  checkChart(groups[0].style.display === "none", "点图例要把那条系列藏起来");
  checkChart(legendButtons[0].dataset.off === "true", "藏起来的图例要有视觉标记");
  checkChart(groups[1].style.display !== "none", "另一条系列不能跟着消失");

  two.fire("click", { target: legendButtons[0] });
  checkChart(groups[0].style.display === "", "再点一下要放回来");
  checkChart(legendButtons[0].dataset.off === "false", "放回来之后标记要清掉");

  const copyButton = two.querySelector("[data-chart-copy]");
  checkChart(Boolean(copyButton), "数据表旁边要有复制按钮");
  checkChart(
    Boolean(copyButton) && copyButton.textContent === "复制 TSV",
    "复制按钮的文案不对：" + (copyButton && copyButton.textContent),
  );

  const zoomButton = two.querySelector("[data-chart-action]");
  checkChart(
    Boolean(zoomButton) && zoomButton.dataset.chartAction === "zoom",
    "标题要是「放大」的入口",
  );
  two.fire("click", { target: zoomButton });
  const layers = document.children.filter((kid) =>
    String(kid.className).includes("chart-zoom"),
  );
  checkChart(layers.length === 1, "点标题要弹出一层全屏，实际 " + layers.length + " 层");
  checkChart(layers.length === 1 && layers[0].hidden === false, "全屏层要显示出来");
  checkChart(
    Boolean(layers[0] && layers[0].querySelector(".chart-title")),
    "全屏层里要重新画一份图",
  );

  document.fire("keydown", { key: "Escape" });
  checkChart(layers[0].hidden === true, "Esc 要收起全屏");

  // ---- 箱线图：五数概括画成箱体 + 须线 + 中位线 ----
  const box = renderChartNode({
    chart_id: "c_box",
    kind: "box",
    title: "修复时长分布",
    x: ["1#", "2#"],
    series: [{ name: "修复时长", values: [1, 1.75, 2.5, 3.25, 4, 2, 4, 6, 8, 10] }],
    x_title: null,
    y_title: null,
  });
  checkChart(box.querySelectorAll(".chart-series").length === 1, "箱线图一条系列一个分组");
  checkChart(box.querySelectorAll(".chart-box").length === 2, "两格要画两个箱体");
  checkChart(box.querySelectorAll(".chart-median").length === 2, "每格一条中位线");
  checkChart(
    box.querySelectorAll(".chart-whisker").length === 6,
    "每格须线 1 条 + 上下横帽 2 条，两格共 6 条",
  );
  checkChart(Boolean(box.querySelector(".chart-bar")) === false, "箱线图不该画柱子");
  checkChart(
    chartTsv({
      kind: "box",
      x: ["1#"],
      series: [{ name: "修复时长", values: [1, 1.75, 2.5, 3.25, 4] }],
    }) === "类别\t系列\t最小值\tP25\t中位数\tP75\t最大值\n1#\t修复时长\t1\t1.75\t2.5\t3.25\t4",
    "箱线图 TSV 形状不对",
  );

  // ---- 回放：点一下从头放，退出后回到正常视图 ----
  const checkReplay = (ok, message) => {
    if (!ok) failures.push("回放: " + message);
  };
  const film = createStore("user_smoke");
  film.setMessages(
    [
      {
        entry_id: "p81",
        entry_seq: 81,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "user",
        content: [{ type: "text", text: "第一问" }],
      },
      {
        entry_id: "p82",
        entry_seq: 82,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "assistant",
        content: [{ type: "text", text: "第一答" }],
      },
    ],
    { hasMore: false },
  );
  view.render(film.state);
  checkReplay(byId.get("messages").children.length === 2, "正常视图里要有两条");

  // 每条模型回答底下要有「导出」。点它在这里不会真下载（假 DOM 序列化不出 HTML），
  // 但必须走到那条分支而不抛错。
  const exportKeys = byId.get("messages").querySelectorAll("[data-export]");
  checkReplay(exportKeys.length === 1, "模型回答要有一个导出键，实际 " + exportKeys.length);
  checkReplay(
    Boolean(exportKeys[0]) && exportKeys[0].textContent === "导出",
    "导出键的文案不对：" + (exportKeys[0] && exportKeys[0].textContent),
  );
  byId.get("messages").fire("click", { target: exportKeys[0] });
  await new Promise((resolve) => setTimeout(resolve, 0));
  checkReplay(exported.length === 1, "点导出要发出一次请求，实际 " + exported.length);
  const payload = exported[0] ?? {};
  checkReplay(payload.title === "第一问", "导出的标题要取这一轮的问题，实际 " + payload.title);
  checkReplay(
    Array.isArray(payload.blocks) && payload.blocks.length === 1,
    "一段文字要拆成一个段落块，实际 " + (payload.blocks ?? []).length,
  );
  checkReplay(
    payload.blocks?.[0]?.type === "paragraph" && payload.blocks[0].text === "第一答",
    "段落块的内容不对：" + JSON.stringify(payload.blocks?.[0]),
  );
  checkReplay(
    typeof payload.fileName === "string" && payload.fileName.endsWith(".docx"),
    "文件名要以 .docx 结尾，实际 " + payload.fileName,
  );

  document.getElementById("replay-open").fire("click", {});
  checkReplay(byId.get("replay").hidden === false, "回放条要出现");
  checkReplay(
    byId.get("replay-count").textContent === "1 / 2",
    "一开始只放第一条，实际 " + byId.get("replay-count").textContent,
  );
  checkReplay(
    byId.get("messages").children.length === 1,
    "列表里只该有第一条，实际 " + byId.get("messages").children.length,
  );

  // 假 DOM 不解析 HTML：把回放条里那两个键手动建出来，对齐真页面上的标记。
  const replayBar = byId.get("replay");
  const pauseButton = makeNode("button");
  pauseButton.dataset.replay = "pause";
  const stopButton = makeNode("button");
  stopButton.dataset.replay = "stop";
  replayBar.append(pauseButton, stopButton);
  checkReplay(Boolean(stopButton), "回放条要有退出键");
  byId.get("replay").fire("click", { target: stopButton });
  checkReplay(byId.get("replay").hidden === true, "退出之后回放条要收起");
  checkReplay(
    byId.get("messages").children.length === 2,
    "退出之后要恢复成两条，实际 " + byId.get("messages").children.length,
  );

  // 一轮里有多条 assistant 消息时，导出键只给最后那条（最终回答）。
  const conv = createStore("user_smoke");
  conv.setMessages(
    [
      {
        entry_id: "c71",
        entry_seq: 71,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "user",
        content: [{ type: "text", text: "问一" }],
      },
      {
        entry_id: "c72",
        entry_seq: 72,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "assistant",
        content: [{ type: "text", text: "先查一下" }],
      },
      {
        entry_id: "c73",
        entry_seq: 73,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "assistant",
        content: [{ type: "text", text: "答一" }],
      },
    ],
    { hasMore: false },
  );
  view.render(conv.state);
  const convKeys = byId.get("messages").querySelectorAll("[data-export]");
  checkReplay(convKeys.length === 1, "只有最终回答带导出键，实际 " + convKeys.length);
  byId.get("messages").fire("click", { target: convKeys[0] });
  await new Promise((resolve) => setTimeout(resolve, 0));
  checkReplay(exported.length === 2, "第二次导出没发出去，实际 " + exported.length);
  checkReplay(exported[1].title === "问一", "标题还是这一轮的问题，实际 " + exported[1].title);
  checkReplay(
    exported[1].blocks.length === 1 && exported[1].blocks[0].text === "答一",
    "正文只放最终回答，实际 " + JSON.stringify(exported[1].blocks),
  );

  // ---- 语音：浏览器没有这些 API 时，按钮必须藏起来 ----
  const checkSpeech = (ok, message) => {
    if (!ok) failures.push("语音: " + message);
  };
  const speechApi = await import("../src/web/js/speech.js");
  checkSpeech(speechApi.canListen() === false, "Node 里不该报「能听写」");
  checkSpeech(speechApi.canSpeak() === false, "Node 里不该报「能朗读」");
  checkSpeech(byId.get("mic").hidden === true, "不能听写时话筒要藏起来");
  checkSpeech(
    byId.get("messages").querySelectorAll("[data-speak]").length === 0,
    "不能朗读时不该出现朗读键",
  );

  // ---- 会话内搜索 ----
  const checkSearch = (ok, message) => {
    if (!ok) failures.push("搜索: " + message);
  };

  const found = createStore("user_smoke");
  found.setMessages(
    [
      {
        entry_id: "s81",
        entry_seq: 81,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "user",
        content: [{ type: "text", text: "压力是多少" }],
      },
      {
        entry_id: "s82",
        entry_seq: 82,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "assistant",
        content: [{ type: "text", text: "压力 0.62，压力单位 MPa" }],
      },
      {
        entry_id: "s83",
        entry_seq: 83,
        timestamp: "2025-01-01T00:00:00+00:00",
        role: "tool",
        tool_call_id: "call_9",
        content: [{ type: "text", text: "压力传感器量程 0-1 MPa" }],
      },
    ],
    { hasMore: false },
  );
  view.render(found.state);

  const bar = byId.get("search");
  const searchInput = byId.get("search-input");
  const searchCount = byId.get("search-count");
  const hits = () => byId.get("messages").querySelectorAll(".search-hit");

  // 真页面里它是 <div ... hidden>；假 DOM 不解析 HTML，这里手动对上初始状态。
  bar.hidden = true;
  document.fire("keydown", { key: "f", ctrlKey: true });
  checkSearch(bar.hidden === false, "⌘F / Ctrl+F 要打开搜索条");

  searchInput.value = "压力";
  searchInput.fire("input");
  checkSearch(searchCount.textContent === "1 / 3", "命中计数不对：" + searchCount.textContent);
  checkSearch(hits().length === 3, "三处命中都要标出来，实际 " + hits().length);
  checkSearch(hits()[0].textContent === "压力", "标出来的正是命中的那一段");
  checkSearch(
    hits()[0].classList.contains("search-hit--active"),
    "第一处要是 active",
  );

  searchInput.fire("keydown", { key: "Enter" });
  checkSearch(searchCount.textContent === "2 / 3", "Enter 跳到下一处，实际 " + searchCount.textContent);
  checkSearch(hits()[1].classList.contains("search-hit--active"), "第二处要变成 active");

  searchInput.fire("keydown", { key: "Enter", shiftKey: true });
  checkSearch(searchCount.textContent === "1 / 3", "Shift+Enter 回到上一处");

  searchInput.fire("keydown", { key: "Enter", shiftKey: true });
  checkSearch(searchCount.textContent === "3 / 3", "到头要绕回最后一处");

  // 默认只搜正文：工具结果里的同名文字不该被标出来。
  const toolCard = byId.get("messages").querySelector(".tool-card");
  checkSearch(Boolean(toolCard), "工具结果卡片要画出来");
  checkSearch(
    toolCard.querySelectorAll(".search-hit").length === 0,
    "默认不搜工具输出，实际标了 " + toolCard.querySelectorAll(".search-hit").length,
  );

  // 勾上「含工具输出」：同一句话里的命中要算进来。
  const tools = byId.get("search-tools");
  tools.checked = true;
  tools.fire("change");
  checkSearch(
    searchCount.textContent === "1 / 4",
    "勾上含工具输出后计数要变成 4 处，实际 " + searchCount.textContent,
  );
  checkSearch(
    toolCard.querySelectorAll(".search-hit").length === 1,
    "勾上之后工具结果里的命中也要标出来",
  );

  tools.checked = false;
  tools.fire("change");
  checkSearch(searchCount.textContent === "1 / 3", "取消勾选要回到 3 处");

  document.fire("keydown", { key: "Escape" });
  checkSearch(bar.hidden === true, "Esc 要收起搜索条");
  checkSearch(hits().length === 0, "收起之后高亮要拆干净，实际还剩 " + hits().length);
  checkSearch(
    byId.get("messages").textContent.includes("压力 0.62，压力单位 MPa"),
    "拆高亮不能把正文弄丢",
  );


  // 事件：与消息同一条去重规则。
  const piped = createStore("user_smoke");
  const frame = chart(2);
  piped.applyEvent("chart_rendered", frame);
  piped.applyEvent("chart_rendered", frame);
  check(piped.state.charts.length === 1, "同一个 entry_id 的图只能进一次");
  piped.setTurns([{ turn: 1, seq: 1, prompt: "问", response: "" }]);
  piped.applyEvent("chart_rendered", { ...frame, entry_id: "e9", entry_seq: 9 });
  check(piped.state.charts.length === 2, "不同的图要各自进画面");
} catch (error) {
  failures.push("图表: " + String(error));
}

console.error = realError;
if (failures.length > 0) {
  console.error("前端启动失败：");
  for (const line of failures) console.error("  " + line);
  process.exitCode = 1;
} else {
  console.log("前端启动通过");
}