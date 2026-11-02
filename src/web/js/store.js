/**
 * 唯一的状态。事件落在它上面，视图从它渲染。
 *
 * **不发请求、不碰 DOM**：需要重新拉历史的场合由返回值告诉调用方。
 */

/** 挑出 existing 里还没有的那些，按 entry_id 认。**两边都容忍空值** —— 老服务端不带某个字段时不该炸。 */
function unseen(existing, incoming) {
  const seen = new Set((existing ?? []).map((message) => message.entry_id));
  return (incoming ?? []).filter((message) => !seen.has(message.entry_id));
}

export function createStore(userId) {
  const state = {
    // 身份与连接
    userId,
    connection: "connecting",

    // 会话列表与当前会话
    sessions: [],
    currentId: null,
    meta: null,

    // 当前会话的消息（只含已加载的那些，默认是最后一页）。
    // messagesVersion 一变，视图才整份重建 —— 流式增量只改 draft，不该让整个列表跟着重排。
    messages: [],
    messagesVersion: 0,
    // 往前翻了一页：头部又插进来一批，视图要保住滚动位置。
    messagesPage: 0,
    // 再往前还有没有。翻历史只看它，别拿"这一页满了"去猜。
    hasMore: false,
    loadingMore: false,

    draft: "",
    // 这一轮正在流出来的思考过程。定稿时会进那条 assistant 消息的 thinking 块，
    // 所以这里只是"还热着的那一段"。
    thought: "",
    running: false,

    // 会话统计。**服务端现数的**（api/stats.py），这里只是缓存。
    stats: { traces: 0, turns: 0, context_tokens: 0, max_tokens: 0 },

    // 手上这一份数据对应日志的末尾 seq（服务端给的）。SSE 回执拿它判断要不要补拉。
    latestSeq: 0,

    // 当前这次 trace 跑到第几轮。只在跑的时候顶上去 ——
    // 轮数要等收尾那一刻才进日志，服务端的 turns 不含进行中的这次。
    liveTurn: 0,

    // 本页窗口里的图。**与 messages 合成一条时间线**（view.js 的 timeline），
    // 两者各按 entry_id 去重 —— 和消息用的是同一条规则。
    charts: [],
    // 轨迹：null 表示**还没取**（面板上显示"读取中"），[] 表示取过了、确实没有。
    traces: null,
    tracesOpen: false,

    // 整份轮次大纲（最多 200 行），右侧导航栏用。**整值替换**，不做增量合并。
    turns: [],

    // 工具调用跑到哪一步了：tool_call_id -> { startedAt, ok }。
    // ok 为 undefined 表示还在跑，卡片据此点亮流光并读秒。
    // 只是界面状态，不是业务数据 —— 刷新之后从头记，不影响别的东西。
    toolRuns: {},

    // 待办清单。**整份替换**：事件带的就是完整列表，不做增量合并。
    // 它不在 messages 里，所以刷新要靠会话详情单独带一份回来。
    todos: [],

    // 等着的审批；最多同时一条，但按列表存着不丢信息。
    approvals: [],

    // 等着的提问。和审批一样是"内存里的东西"，刷新只能靠接口补拉。
    questions: [],

    // 一次性的错误提示，几秒后自己清掉。
    error: "",
  };

  return {
    state,

    /** 整份换掉待办清单：落在事件上，或从会话详情里带回来。 */
    setTodos(todos) {
      state.todos = todos ?? [];
    },

    /** 整份换掉图：换会话、刚连上、压缩过之后。 */
    setCharts(charts) {
      state.charts = charts ?? [];
    },

    /** 尾巴上添图。**按 entry_id 去重** —— 补拉会重复带回同一段窗口里的图。 */
    appendCharts(more) {
      const fresh = unseen(state.charts, more);
      if (fresh.length === 0) return;
      state.charts = state.charts.concat(fresh);
    },

    /** 头部插更早的图。翻页和增量可能重叠，同样按 entry_id 去重。 */
    prependCharts(older) {
      const fresh = unseen(state.charts, older);
      if (fresh.length === 0) return;
      state.charts = fresh.concat(state.charts);
    },

    /** 整份换掉轮次大纲：服务端每次都给全量，直接替换。 */
    setTurns(turns) {
      state.turns = turns ?? [];
    },

    /** 整份换掉轨迹：服务端每次给最新的一批，直接替换。 */
    setTraces(traces) {
      state.traces = traces;
    },

    /** 开关轨迹面板。关的时候不清数据 —— 再打开时先画旧的，同时去取新的。 */
    setTracesOpen(open) {
      state.tracesOpen = Boolean(open);
    },

    /** 整份换掉：换会话、刚连上、压缩过之后。 */
    setMessages(messages, { hasMore = false } = {}) {
      state.messages = messages;
      state.hasMore = hasMore;
      state.messagesVersion += 1;
    },

    /**
     * 尾巴上添新的：增量刷新只走这条，不重建列表。
     *
     * **按 entry_id 去重**：补拉取的是"游标之后"的一段，两次请求撞上同一段时，
     * 同一条消息就会进来两遍 —— 界面上看起来就是一句话重复出现。
     */
    appendMessages(more) {
      const fresh = unseen(state.messages, more);
      if (fresh.length === 0) return;
      state.messages = state.messages.concat(fresh);
    },

    /** 头部插更早的一页。翻页和增量可能重叠，同样去重。 */
    prependMessages(older, { hasMore = false } = {}) {
      state.hasMore = hasMore;
      const fresh = unseen(state.messages, older);
      if (fresh.length === 0) return;
      state.messages = fresh.concat(state.messages);
      state.messagesPage += 1;
    },

    /**
     * 把一个事件落到状态上。
     *
     * 返回 "tail" 表示「去补最新的那几条」，返回 "reload" 表示「上下文被整份换过，重拉」，
     * 返回 null 表示什么都不用做。
     *
     * 工具结果没有对应事件，只能等位点前进之后补拉；而压缩把整段历史换成了摘要，
     * 那时候除了重拉没有别的办法（与断线重连同一条思路）。
     */
    applyEvent(name, data) {
      switch (name) {
        case "trace_start":
          state.running = true;
          state.liveTurn = 0;
          return "tail";
        case "turn_start":
          // 上一轮的工具结果这时已经写进日志了，补一次才看得见。
          state.liveTurn = data.turn_index ?? 0;
          return "tail";
        case "assistant_delta":
          state.draft += data.text ?? "";
          return null;
        case "assistant_thinking":
          state.thought += data.text ?? "";
          return null;
        case "assistant_message":
          state.draft = "";
          state.thought = "";
          return "tail";
        case "compaction":
          return "reload";
        case "trace_end": {
          state.running = false;
          state.draft = "";
          state.thought = "";
          state.liveTurn = 0;
          // 收尾了还挂着"运行中"的，说明结果事件没到 —— 摘掉，别让它一直读秒。
          for (const [id, run] of Object.entries(state.toolRuns)) {
            if (run.ok === undefined) delete state.toolRuns[id];
          }
          return "tail";
        }
        case "tool_call_start":
          // 派发即开跑：流光与读秒都从这一刻起算。
          state.toolRuns[data.tool_call_id] = { startedAt: Date.now() };
          return null;
        case "tool_result":
          state.toolRuns[data.tool_call_id] = { ok: Boolean(data.ok) };
          // 结果消息这时已经落进日志，补一次就看得见 —— 长工具不用等下一轮。
          return "tail";
        case "todo_updated":
          state.todos = data.todos ?? [];
          return null;
        case "chart_rendered":
          // 图是内容，不是状态：按 entry_id 去重，位置由 entry_seq 决定（见 view.js）。
          if (unseen(state.charts, [data]).length > 0) {
            state.charts = state.charts.concat([data]);
          }
          return null;
        case "approval_required":
          state.approvals.push(data);
          return null;
        case "question_required":
          state.questions.push(data);
          return null;
        default:
          return null;
      }
    },
  };
}
