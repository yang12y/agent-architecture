/**
 * HTTP 端点：只管 fetch，不碰 DOM、不碰状态。
 * 七个端点见 ARCHITECTURE.md §7.3。
 */

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      search.set(key, value);
    }
  }
  return search.toString();
}

async function call(path, options = {}) {
  const init = { method: options.method ?? "GET" };
  if (options.body !== undefined) {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  const suffix = options.params ? "?" + query(options.params) : "";

  const response = await fetch(path + suffix, init);
  if (!response.ok) {
    // 失败一律是 §7.8 那个形状；静态文件的 404 不是 JSON，用兜底那句。
    let error = { code: "unknown", message: "HTTP " + response.status };
    try {
      error = (await response.json()).error ?? error;
    } catch {
      /* 保留兜底 */
    }
    throw new ApiError(response.status, error.code, error.message);
  }
  return response.json();
}

export function listSessions(userId) {
  return call("/sessions", { params: { user_id: userId } });
}

export function createSession(userId) {
  return call("/sessions", { method: "POST", params: { user_id: userId } });
}

export function deleteSession(userId, sessionId) {
  return call("/sessions/" + encodeURIComponent(sessionId), {
    method: "DELETE",
    params: { user_id: userId },
  });
}

/**
 * 会话详情 + 一页消息。
 *
 * 不给游标就是最后一页；beforeSeq 往前翻，afterSeq 往后补（增量刷新用）。
 * 服务端回 has_more 告诉你还有没有更多。
 */
export function getSession(userId, sessionId, { limit, beforeSeq, afterSeq } = {}) {
  return call("/sessions/" + encodeURIComponent(sessionId), {
    params: { user_id: userId, limit, before_seq: beforeSeq, after_seq: afterSeq },
  });
}

export function postMessage(userId, sessionId, text) {
  return call("/sessions/" + encodeURIComponent(sessionId) + "/messages", {
    method: "POST",
    params: { user_id: userId },
    body: { content: [{ type: "text", text }] },
  });
}

export function interrupt(userId, sessionId) {
  return call("/sessions/" + encodeURIComponent(sessionId) + "/interrupt", {
    method: "POST",
    params: { user_id: userId },
  });
}

export function listApprovals(userId, sessionId) {
  return call("/approvals", { params: { user_id: userId, session_id: sessionId } });
}

export function answerApproval(userId, sessionId, requestId, decision, comment) {
  return call("/approvals/" + encodeURIComponent(requestId), {
    method: "POST",
    params: { user_id: userId, session_id: sessionId },
    body: { decision, comment: comment || null },
  });
}
