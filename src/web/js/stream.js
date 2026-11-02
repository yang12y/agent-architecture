/**
 * SSE：连上、断了重连、重连之后照 wanted 再连一次。
 * 不碰 DOM、不碰状态 —— 帧原样交给 onFrame。
 *
 * 一条流听着哪些会话是**连接时定死**的，所以换会话 = 重开一条流。
 */

export function createStream({ userId, onFrame, onStatus }) {
  // 想听哪个会话；null 表示还没选。服务端不记谁订过谁，重连要照它再连一次。
  let wanted = null;
  let source = null;
  let attempts = 0;
  let stopped = false;
  let timer = null;

  function endpoint(sessionId) {
    return (
      "/events?user_id=" +
      encodeURIComponent(userId) +
      "&session_id=" +
      encodeURIComponent(sessionId)
    );
  }

  function drop() {
    if (!source) return;
    source.close();
    source = null;
  }

  function schedule() {
    if (stopped || !wanted) return;
    // 500ms 起，指数退避到 8s 封顶。
    const delay = Math.min(500 * 2 ** Math.min(attempts, 4), 8000);
    attempts += 1;
    timer = setTimeout(connect, delay);
  }

  function connect() {
    if (stopped || !wanted) return;
    onStatus("connecting");
    source = new EventSource(endpoint(wanted));

    source.addEventListener("open", () => {
      attempts = 0;
    });

    source.addEventListener("message", (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      if (frame.type === "ready") {
        onStatus("ready");
        return;
      }
      onFrame(frame);
    });

    source.addEventListener("error", () => {
      // 断开与服务端收尾都会走到这里。**不用 EventSource 自带的重连** ——
      // 它不告诉我们退避到哪了，这里也没有机会把状态画对。
      onStatus("offline");
      drop();
      schedule();
    });
  }

  return {
    /** 改听这个会话；本来就在听它就不动。 */
    watch(sessionId) {
      if (sessionId === wanted && source) return;
      wanted = sessionId;
      attempts = 0;
      clearTimeout(timer);
      drop();
      connect();
    },
    /** 谁都不听了，但连接还能再开。 */
    unwatch() {
      wanted = null;
      clearTimeout(timer);
      drop();
    },
    close() {
      stopped = true;
      clearTimeout(timer);
      drop();
    },
  };
}
