/**
 * 装配：把 api / socket / store / view 接起来。
 *
 * **只有这一层知道「哪个事件要触发哪个请求」** —— 其余四层互不相识。
 */

import * as api from "./api.js";
import { createStore } from "./store.js";
import { createStream } from "./stream.js";
import * as view from "./view.js";

// 本地单用户部署：用户名**写死**，与 api.js 里写死的授权与租户同一个道理。
//
// 以前是"首次访问随机生成一个、存 localStorage"：清一次缓存、换一个浏览器就是另一个身份，
// 于是磁盘里堆出一批谁也看不见的会话，而每个新身份第一次打开还会被自动建一个空会话。
// 后端按 user_id 隔离（那是给多用户留的），前端这个入口只该有一个稳定的用户。
//
// 举例：userId == "user_local"
const userId = "user_local";
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

/**
 * 取这个会话的轨迹，取回来自家重画一次。
 *
 * **必须自己 render**：这个请求的发起方是"打开面板"与"一次执行收尾"，它们之后不会再有
 * 别的重画 —— 不画的话面板就停在"读取中"或者上一次的旧数据上，等下一次事件才补上。
 */
async function loadTraces() {
  const sessionId = state.currentId;
  if (!sessionId) {
    store.setTraces([]);
    render();
    return;
  }
  let traces = [];
  try {
    traces = (await api.listTraces(userId, sessionId)).traces;
  } catch (error) {
    showError(error);
  }
  // 期间换了会话：这份结果已经不属于眼前这个会话，丢掉。
  if (state.currentId !== sessionId) return;
  store.setTraces(traces);
  render();
}

function applyDetail(detail) {
  state.meta = detail;
  state.stats = detail.stats;
  state.running = detail.is_running;
  // 这一份数据已经跟到日志的哪儿 —— 连上的回执拿它跟自己对，一样就不必再拉一遍。
  state.latestSeq = detail.latest_entry_seq ?? 0;
  // 轮次大纲是整份的：每次都给全量，直接替换。
  store.setTurns(detail.turns);
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
  const questions = (await api.listQuestions(userId, sessionId)).questions;
  // 期间换了会话：这份结果已经不属于眼前这个会话，丢掉。
  if (state.currentId !== sessionId) return;
  applyDetail(detail);
  state.approvals = approvals;
  state.questions = questions;
  store.setTodos(detail.todos);
  store.setCharts(detail.charts);
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
  // 图不带消息，会先于下一条消息出现 —— 这里是它第一次进画面的地方。
  store.appendCharts(detail.charts);
  store.appendMessages(detail.messages);
  render();
}

async function openSession(sessionId) {
  state.currentId = sessionId;
  state.draft = "";
  state.thought = "";
  // 轨迹属于上一个会话，先清成"还没取"；面板开着就顺手取新的。
  store.setTraces(null);
  await loadSession();
  stream.watch(sessionId);
  if (state.tracesOpen) loadTraces().catch(showError);
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
      // 回执带着"现在在不在跑"、半截草稿与日志末尾的 seq —— 断线重连靠它把画面接上。
      state.running = frame.is_running;
      state.draft = frame.draft_text || "";
      // 已经跟到末尾就不重拉：刚 openSession 过一遍就属于这种，否则启动时这一组
      // 详情 + 审批 + 提问的请求会白打第二遍。
      // 落后了就整份重来（不是只补消息）：断线期间挂出来的审批与提问只有那一组带得回来。
      if ((frame.latest_entry_seq ?? 0) > state.latestSeq) loadSession().catch(showError);
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
      // 刚收尾的那条 trace 这时才进日志 —— 面板开着就顺手刷一次。
      if (frame.name === "trace_end" && state.tracesOpen) loadTraces().catch(showError);
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

  /**
   * 从某一轮分叉：新会话直接打开，原会话一个字都不动。
   *
   * 打开之后**输入框是空的** —— 分叉保留了这一轮的问与答，接下来要问的是下一个问题。
   */
  async forkAt(turnSeq) {
    if (!state.currentId) return;
    try {
      const created = await api.forkSession(userId, state.currentId, turnSeq);
      await loadSessions();
      await openSession(created.session_id);
    } catch (error) {
      showError(error);
    }
  },

  /**
   * 开关轨迹面板。每次打开都重新取一份 —— 上一次执行可能是刚刚才收尾的。
   */
  async toggleTraces(open) {
    store.setTracesOpen(open);
    render();
    if (!open) return;
    try {
      await loadTraces();
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

  /**
   * 补拉游标之后新增的几条。点"回到最新"时叫它 —— 滚到底不等于数据已经到本地
   * （断线重连、漏了一拍事件都会让尾巴缺一截）。
   */
  loadTail() {
    return scheduleLoad(loadTailNow);
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
      store.prependCharts(page.charts);
      store.prependMessages(page.messages, { hasMore: page.has_more });
      render();
    } catch (error) {
      showError(error);
    } finally {
      state.loadingMore = false;
    }
  },

  /**
   * 导出这一段回答：后端排成 .docx，回来直接存盘。
   *
   * 内容由前端给（view 已经把回答拆成了块），后端只负责排版、不在磁盘上留东西。
   */
  async exportDocx(payload) {
    try {
      const blob = await api.exportDocx(payload);
      view.saveBlob(blob, payload.fileName);
    } catch (error) {
      showError(error);
    }
  },

  /**
   * 导航栏点了第 seq 轮那一条：先把那一轮翻进来，再滚过去。
   *
   * 翻页与滚动分两步 —— 没加载的轮次在 DOM 里没有节点，翻到了才滚得动。
   */
  async jumpToTurn(seq) {
    try {
      // 一轮最多翻几屏就停：库里没有这一轮的条目时，别在这里转圈。
      for (let page = 0; page < 40; page += 1) {
        if (!state.hasMore || state.messages.length === 0) break;
        if (state.messages[0].entry_seq <= seq) break;
        const before = state.messages[0].entry_seq;
        await actions.loadEarlier();
        if (state.messages[0].entry_seq === before) break;
      }
      view.scrollToSeq(seq);
    } catch (error) {
      showError(error);
    }
  },

  async answerQuestion(requestId, answers) {
    try {
      await api.answerQuestion(userId, state.currentId, requestId, answers);
      state.questions = state.questions.filter((item) => item.request_id !== requestId);
      render();
    } catch (error) {
      showError(error);
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
