/**
 * 装配：把 api / socket / store / view 接起来。
 *
 * **只有这一层知道「哪个事件要触发哪个请求」** —— 其余四层互不相识。
 */

import * as api from "./api.js";
import { createStore } from "./store.js";
import { createStream } from "./stream.js";
import * as view from "./view.js";

// 浏览器里记一下身份，刷新之后还是同一个用户的会话。
const USER_KEY = "agent-architecture.user";

function resolveUserId() {
  const stored = localStorage.getItem(USER_KEY);
  if (stored) return stored;
  const generated = "user_" + Math.random().toString(36).slice(2, 10);
  localStorage.setItem(USER_KEY, generated);
  return generated;
}

const userId = resolveUserId();
const store = createStore(userId);
const { state } = store;

function render() {
  view.render(state);
}

let errorTimer = null;
function showError(error) {
  console.error(error);
  state.error = error?.message ?? String(error);
  render();
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => {
    state.error = "";
    render();
  }, 5000);
}

// 历史一页多少条。首屏只看最新这一屏，往上滚再要更早的。
const PAGE = 50;

async function loadSessions() {
  state.sessions = (await api.listSessions(userId)).sessions;
}

function applyDetail(detail) {
  state.meta = detail;
  state.stats = detail.stats;
  state.running = detail.is_running;
}

// 拉消息的任务**串成一条链**：事件是成串来的（trace_start / turn_start / assistant_message
// 紧挨着到），并发发出去的请求会带着同一个游标回来，同一条消息于是被追加两次。
let loading = Promise.resolve();

function scheduleLoad(work) {
  // 上一步失败也要接着跑下一步，所以失败分支挂的是同一个 work。
  loading = loading.then(work, work).catch(showError);
  return loading;
}

/** 换会话 / 刚连上 / 压缩过：整份重来，但只拉最新的一页。 */
function loadSession() {
  return scheduleLoad(loadSessionNow);
}

async function loadSessionNow() {
  const sessionId = state.currentId;
  if (!sessionId) return;
  const detail = await api.getSession(userId, sessionId, { limit: PAGE });
  const approvals = (await api.listApprovals(userId, sessionId)).approvals;
  // 期间换了会话：这份结果已经不属于眼前这个会话，丢掉。
  if (state.currentId !== sessionId) return;
  applyDetail(detail);
  state.approvals = approvals;
  store.setMessages(detail.messages, { hasMore: detail.has_more });
  render();
}

/**
 * 只补游标之后新增的几条。**列表不重建** —— 工具结果没有对应事件，
 * 所以每次位点前进都靠这一步把它们捞进来。
 */
function loadTail() {
  return scheduleLoad(loadTailNow);
}

async function loadTailNow() {
  const sessionId = state.currentId;
  if (!sessionId) return;
  const last = state.messages[state.messages.length - 1];
  if (!last) {
    await loadSessionNow();
    return;
  }
  const detail = await api.getSession(userId, sessionId, {
    afterSeq: last.entry_seq,
    limit: PAGE,
  });
  if (state.currentId !== sessionId) return;
  applyDetail(detail);
  store.appendMessages(detail.messages);
  render();
}

async function openSession(sessionId) {
  state.currentId = sessionId;
  state.draft = "";
  state.thought = "";
  await loadSession();
  stream.watch(sessionId);
}

const stream = createStream({
  userId,
  onStatus(status) {
    state.connection = status;
    render();
  },
  onFrame(frame) {
    // 一条流只带一个会话，但换会话那一刻旧会话的事件可能还在路上。
    if (frame.session_id && frame.session_id !== state.currentId) return;
    if (frame.type === "error") {
      showError({ message: frame.message });
      return;
    }
    if (frame.type === "subscribed") {
      // 回执带着"现在在不在跑"和半截草稿 —— 断线重连靠它把画面接上。
      state.running = frame.is_running;
      state.draft = frame.draft_text || "";
      loadSession().catch(showError);
      return;
    }
    if (frame.type === "event") {
      // 不是"跑完了"的那种收尾要说一声 —— 否则界面只是安静地停下来，看不出为什么。
      if (
        frame.name === "trace_end" &&
        (frame.data.status === "budget_exceeded" || frame.data.status === "failed")
      ) {
        showError({ message: "这次运行没有正常结束：" + (frame.data.reason || frame.data.status) });
      }
      const want = store.applyEvent(frame.name, frame.data);
      if (want === "reload") loadSession().catch(showError);
      else if (want === "tail") loadTail().catch(showError);
    }
    render();
  },
});

const actions = {
  async newSession() {
    try {
      const created = await api.createSession(userId);
      await loadSessions();
      await openSession(created.session_id);
    } catch (error) {
      showError(error);
    }
  },

  async selectSession(sessionId) {
    if (sessionId === state.currentId) return;
    try {
      await openSession(sessionId);
    } catch (error) {
      showError(error);
    }
  },

  async removeSession(sessionId) {
    if (!window.confirm("删除这个会话？消息记录会一起删掉，不能恢复。")) return;
    try {
      await api.deleteSession(userId, sessionId);
      await loadSessions();
      if (sessionId !== state.currentId) {
        render();
        return;
      }
      // 删的是正在看的那个：先不听它，再换到列表里的下一个；一个都不剩就新建一个。
      stream.unwatch();
      state.currentId = null;
      const next = state.sessions[0]?.session_id;
      if (next) await openSession(next);
      else await actions.newSession();
    } catch (error) {
      showError(error);
    }
  },

  async send(text) {
    if (!state.currentId) return;
    try {
      await api.postMessage(userId, state.currentId, text);
      state.running = true;
      render();
    } catch (error) {
      showError(error);
      if (error.code === "session_busy") {
        state.running = true;
        render();
      }
    }
  },

  async interrupt() {
    if (!state.currentId) return;
    try {
      await api.interrupt(userId, state.currentId);
    } catch (error) {
      showError(error);
    }
  },

  /** 往上翻历史。滚到顶上会自动叫它，列表短到不用滚时还有顶上的按钮。 */
  async loadEarlier() {
    if (!state.currentId || !state.hasMore || state.loadingMore) return;
    const first = state.messages[0];
    if (!first) return;
    state.loadingMore = true;
    try {
      const page = await api.getSession(userId, state.currentId, {
        beforeSeq: first.entry_seq,
        limit: PAGE,
      });
      store.prependMessages(page.messages, { hasMore: page.has_more });
      render();
    } catch (error) {
      showError(error);
    } finally {
      state.loadingMore = false;
    }
  },

  async answer(requestId, decision, comment) {
    try {
      await api.answerApproval(userId, state.currentId, requestId, decision, comment);
      state.approvals = state.approvals.filter((item) => item.request_id !== requestId);
      render();
    } catch (error) {
      showError(error);
    }
  },
};

async function boot() {
  // 启动这一段整个包在 try 里：出错要看得见，不能只留一个空白页面。
  try {
    view.mount(actions);
    render();
    // 没有单独的"连接"步骤：选到哪个会话，openSession 才开哪条流。
    await loadSessions();
    let sessionId = state.sessions[0]?.session_id;
    if (!sessionId) {
      sessionId = (await api.createSession(userId)).session_id;
      await loadSessions();
    }
    await openSession(sessionId);
  } catch (error) {
    showError(error);
  }
}

window.addEventListener("beforeunload", () => stream.close());

boot().catch(showError);
