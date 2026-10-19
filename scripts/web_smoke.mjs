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
      object[key] = value;
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
  let body = { approvals: [] };
  if (path === "/sessions") {
    body = { sessions: [{ session_id: "sess_smoke" }], session_id: "sess_smoke" };
  } else if (path !== "/approvals" && path.startsWith("/sessions/")) {
    body = {
      session_id: "sess_smoke",
      messages: [],
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
} catch (error) {
  failures.push("markdown: " + String(error));
}

console.error = realError;
if (failures.length > 0) {
  console.error("前端启动失败：");
  for (const line of failures) console.error("  " + line);
  process.exitCode = 1;
} else {
  console.log("前端启动通过");
}
