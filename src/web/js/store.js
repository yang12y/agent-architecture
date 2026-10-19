/**
 * 唯一的状态。事件落在它上面，视图从它渲染。
 *
 * **不发请求、不碰 DOM**：需要重新拉历史的场合由返回值告诉调用方。
 */

/** 挑出 existing 里还没有的那些，按 entry_id 认。 */
function unseen(existing, incoming) {
  const seen = new Set(existing.map((message) => message.entry_id));
  return incoming.filter((message) => !seen.has(message.entry_id));
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

    // 当前这次 trace 跑到第几轮。只在跑的时候顶上去 ——
    // 轮数要等收尾那一刻才进日志，服务端的 turns 不含进行中的这次。
    liveTurn: 0,

    // 等着的审批；最多同时一条，但按列表存着不丢信息。
    approvals: [],

    // 一次性的错误提示，几秒后自己清掉。
    error: "",
  };

  return {
    state,

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
     * 那时候除了重拉没有别的办法（与 §7.6 的重连同一条思路）。
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
        case "trace_end":
          state.running = false;
          state.draft = "";
          state.thought = "";
          state.liveTurn = 0;
          return "tail";
        case "approval_required":
          state.approvals.push(data);
          return null;
        default:
          return null;
      }
    },
  };
}
