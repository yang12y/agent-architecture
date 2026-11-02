/**
 * HTTP 端点：只管 fetch，不碰 DOM、不碰状态。
 * 端点见 api/routes/。
 */

/**
 * 临时凭证：认证与租户由前端写死，随发消息的请求头带上去。
 * 后端不存这两个值，只把它们随这一次 trace 交给业务工具（docs/TOOLS.md §2.4）。
 */
const AUTHORIZATION = "Bearer c45695af-b69c-404c-acf8-3e23f4214823";
const TENANT = "BF58A35B-1DA6-194C-612D-35A2A02B59E9";

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

/** 发一个请求，成功时把 Response 原样交出来（JSON 与二进制都从这里走）。 */
async function send(path, options = {}) {
  const init = { method: options.method ?? "GET" };
  const headers = { ...(options.headers ?? {}) };
  if (options.body !== undefined) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  if (Object.keys(headers).length > 0) {
    init.headers = headers;
  }
  const suffix = options.params ? "?" + query(options.params) : "";

  const response = await fetch(path + suffix, init);
  if (!response.ok) {
    // 失败一律是那个统一错误形状；静态文件的 404 不是 JSON，用兜底那句。
    let error = { code: "unknown", message: "HTTP " + response.status };
    try {
      error = (await response.json()).error ?? error;
    } catch {
      /* 保留兜底 */
    }
    throw new ApiError(response.status, error.code, error.message);
  }
  return response;
}

/** 要 JSON 的常规调用。 */
async function call(path, options = {}) {
  return (await send(path, options)).json();
}

/**
 * 要文件字节的调用（现在是导出 .docx）。
 *
 * 举例：blob = await exportDocx({ title, blocks })
 */
async function callBlob(path, options = {}) {
  return (await send(path, options)).blob();
}

export function listSessions(userId) {
  return call("/sessions", { params: { user_id: userId } });
}

export function createSession(userId) {
  return call("/sessions", { method: "POST", params: { user_id: userId } });
}

/**
 * 把到 turnSeq 那一轮结束为止的对话复制成一个新会话。
 *
 * turnSeq 是那一轮用户消息的 entry_seq —— 大纲与分叉键用的都是它。
 */
export function forkSession(userId, sessionId, turnSeq) {
  return call("/sessions/" + encodeURIComponent(sessionId) + "/fork", {
    method: "POST",
    params: { user_id: userId, turn_seq: turnSeq },
  });
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

/**
 * 这个会话跑过的 trace，每条带耗时瀑布（最新的在前）。
 *
 * 只有收过尾的才有 —— 正在跑的那次还没有 trace_end。
 */
export function listTraces(userId, sessionId, limit) {
  return call("/sessions/" + encodeURIComponent(sessionId) + "/traces", {
    params: { user_id: userId, limit },
  });
}

export function postMessage(userId, sessionId, text) {
  return call("/sessions/" + encodeURIComponent(sessionId) + "/messages", {
    method: "POST",
    params: { user_id: userId },
    headers: { authorization: AUTHORIZATION, tenant: TENANT },
    body: { content: [{ type: "text", text }] },
  });
}

export function interrupt(userId, sessionId) {
  return call("/sessions/" + encodeURIComponent(sessionId) + "/interrupt", {
    method: "POST",
    params: { user_id: userId },
  });
}

/**
 * 把一段回答排成 .docx。**后端不落盘**：文件只存在于这一次响应里。
 *
 * 举例：await exportDocx({ title: "产量分析", blocks: [...] }) -> Blob
 */
export function exportDocx(payload) {
  return callBlob("/exports/docx", { method: "POST", body: payload });
}

export function listApprovals(userId, sessionId) {
  return call("/approvals", { params: { user_id: userId, session_id: sessionId } });
}

export function listQuestions(userId, sessionId) {
  return call("/questions", { params: { user_id: userId, session_id: sessionId } });
}

export function answerQuestion(userId, sessionId, requestId, answers) {
  return call("/questions/" + encodeURIComponent(requestId), {
    method: "POST",
    params: { user_id: userId, session_id: sessionId },
    body: { answers },
  });
}

export function answerApproval(userId, sessionId, requestId, decision, comment) {
  return call("/approvals/" + encodeURIComponent(requestId), {
    method: "POST",
    params: { user_id: userId, session_id: sessionId },
    body: { decision, comment: comment || null },
  });
}
