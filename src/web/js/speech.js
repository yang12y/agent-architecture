/**
 * 语音：把说的话写进输入框（话筒），把回答念出来（喇叭）。
 *
 * 两样都是浏览器自带能力，零后端：
 *   - 识别：SpeechRecognition / webkitSpeechRecognition（Chrome / Edge 有，Firefox 没有）
 *   - 朗读：speechSynthesis
 *
 * 不支持时 canListen() / canSpeak() 返回 false，界面据此把按钮藏掉 ——
 * 不留一个点了没反应的键。
 */

// 识别器的构造函数；不支持时是 undefined。只在模块加载时取一次。
//
// 举例：SpeechRecognitionCtor === window.webkitSpeechRecognition
const SpeechRecognitionCtor =
  globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition || null;

/** 这台浏览器能不能听写。
 *
 * 举例：canListen() -> true
 */
export function canListen() {
  return typeof SpeechRecognitionCtor === "function";
}

/** 这台浏览器能不能朗读。
 *
 * 举例：canSpeak() -> true
 */
export function canSpeak() {
  return (
    typeof SpeechSynthesisUtterance === "function" &&
    typeof globalThis.speechSynthesis?.speak === "function"
  );
}

/**
 * 开始听写，返回一个能停的句柄；不支持就返回 null。
 *
 * onText 拿到的是**这一次听写的全文**（已定稿 + 正在识别的半截），
 * 调用方把它接在原有文字后面就行 —— 不用自己拼片段。
 *
 * 举例：const session = listen({ onText, onState });
 */
export function listen(options) {
  if (!canListen()) return null;
  const opts = options || {};
  const recognition = new SpeechRecognitionCtor();
  recognition.lang = opts.lang || "zh-CN";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.maxAlternatives = 1;

  let finalText = "";
  recognition.onresult = (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const transcript = (result[0] && result[0].transcript) || "";
      if (result.isFinal) finalText += transcript;
      else interim += transcript;
    }
    if (opts.onText) opts.onText(finalText + interim);
  };
  recognition.onerror = (event) => {
    if (opts.onState) opts.onState("error", (event && event.error) || "");
  };
  recognition.onend = () => {
    if (opts.onState) opts.onState("end");
  };
  recognition.start();
  return {
    stop() {
      recognition.stop();
    },
  };
}

// 正在念的那一条。同一时刻只念一条，换一条就掐掉上一条。
//
// 举例：{ utterance: SpeechSynthesisUtterance, onEnd: [Function] }
let speaking = null;

/**
 * 念一段文字。返回有没有念起来（不支持、或没文字时返回 false）。
 *
 * 念完或出错都会回调 onEnd，调用方据此把按钮复原。
 *
 * 举例：speak("图已生成", { onEnd }) -> true
 */
export function speak(text, options) {
  if (!canSpeak() || !text) return false;
  stopSpeak();
  const opts = options || {};
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-CN";
  const done = () => finish(utterance);
  utterance.onend = done;
  utterance.onerror = done;
  speaking = { utterance, onEnd: opts.onEnd };
  globalThis.speechSynthesis.speak(utterance);
  return true;
}

/** 停掉正在念的。手动停也会回调 onEnd，跟念完走同一条路。 */
export function stopSpeak() {
  if (!speaking) return;
  const current = speaking;
  speaking = null;
  if (globalThis.speechSynthesis) globalThis.speechSynthesis.cancel();
  if (current.onEnd) current.onEnd();
}

/** 念完（或出错、或被停）之后收尾：只认当前那一条，避免 cancel 之后再回调一次。 */
function finish(utterance) {
  if (!speaking || speaking.utterance !== utterance) return;
  const onEnd = speaking.onEnd;
  speaking = null;
  if (onEnd) onEnd();
}

/** 现在有没有在念。
 *
 * 举例：isSpeaking() -> false
 */
export function isSpeaking() {
  return speaking !== null;
}
