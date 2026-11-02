/**
 * 极简 Markdown -> DOM。**不拼 HTML 字符串** —— 所有文本都走 textContent，
 * 模型输出什么都变不成可执行的标签。
 *
 * 支持：围栏代码块、行内代码、标题、有序/无序列表、引用、分隔线、粗体、斜体、链接、
 * 管道表格（含 :---: / ---: 对齐）。
 * 其余的（图片、内嵌 HTML）原样当文本。
 *
 * 分两步：parseMarkdown 只产出纯数据（没有 DOM 也能断言），renderMarkdown 才碰 DOM。
 */

// 纯文本节点用这个 tag 标记，方便和元素区分开。
//
// 举例："#text"
const TEXT = "#text";

function text(value) {
  return { tag: TEXT, text: value };
}

function element(tag, children = [], extra = {}) {
  return { tag, children, ...extra };
}

const FENCE = /^\s*```\s*([A-Za-z0-9_+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const UL_ITEM = /^\s*[-*+]\s+(.*)$/;
const OL_ITEM = /^\s*(\d+)[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const RULE = /^\s*([-*_])\s*(?:\1\s*){2,}$/;
// 表格行以竖线开头；分隔行（|---|:--:|）由每个单元格的冒号+短横线组成。
const PIPE_ROW = /^\s*\|/;
const DELIMITER_CELL = /^:?-+:?$/;

// 行内标记。顺序有讲究：行内代码最先，否则呢里面的 ** 会被当成粗体。
//
// 举例：「**粗** 和 `code`」-> strong / code
const INLINE = "`([^`\\n]+)`|\\*\\*([^*\\n]+)\\*\\*|__([^_\\n]+)__|\\*([^*\\n]+)\\*|_([^_\\n]+)_|\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)";

/** 这一行是不是某个块级结构的开头。段落就是靠它知道什么时候该停。 */
function startsBlock(line) {
  return (
    line.trim() === "" ||
    FENCE.test(line) ||
    HEADING.test(line) ||
    RULE.test(line) ||
    QUOTE.test(line) ||
    UL_ITEM.test(line) ||
    OL_ITEM.test(line) ||
    PIPE_ROW.test(line)
  );
}

/** 拆一行表格：去掉首尾竖线后按竖线切，单元格各自 trim。 */
function splitRow(line) {
  const trimmed = String(line ?? "").trim();
  if (!trimmed.includes("|")) return null;
  let body = trimmed;
  if (body.startsWith("|")) body = body.slice(1);
  if (body.endsWith("|")) body = body.slice(0, -1);
  return body.split("|").map((cell) => cell.trim());
}

/** 这一行是不是表头下面的分隔行。 */
function isDelimiterRow(cells) {
  return Boolean(cells && cells.length) && cells.every((cell) => DELIMITER_CELL.test(cell));
}

/** 分隔单元格的冒号决定对齐：两边都有居中，只有右边右对齐。 */
function alignmentOf(cell) {
  const left = cell.startsWith(":");
  const right = cell.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return null;
}

/**
 * 从第 index 行起解析一张管道表格；不是表格就返回 null。
 *
 * 举例：parseTable(["| a | b |", "|---|---:|", "| 1 | 2 |"], 0)
 *       -> {block: div>table, next: 3}
 */
function parseTable(lines, index) {
  const header = splitRow(lines[index]);
  const delimiter = splitRow(lines[index + 1]);
  if (!header || !header.length || !isDelimiterRow(delimiter)) return null;
  if (header.length !== delimiter.length) return null;

  const aligns = delimiter.map(alignmentOf);
  const rows = [];
  let cursor = index + 2;
  while (cursor < lines.length) {
    const cells = splitRow(lines[cursor]);
    if (!cells) break;
    rows.push(cells);
    cursor += 1;
  }

  const cell = (tag, value, align) =>
    element(tag, inline(value), align ? { className: "md-" + align } : {});
  const head = element(
    "tr",
    header.map((value, i) => cell("th", value, aligns[i])),
  );
  const body = rows.map((cells) =>
    element(
      "tr",
      header.map((_, i) => cell("td", cells[i] ?? "", aligns[i])),
    ),
  );
  return {
    block: element("div", [element("table", [element("thead", [head]), element("tbody", body)])], {
      className: "md-table",
    }),
    next: cursor,
  };
}

/**
 * 把一段 Markdown 拆成块级节点树。
 *
 * 举例：parseMarkdown("**粗**") -> [{tag: "p", children: [{tag: "strong", ...}]}]
 */
export function parseMarkdown(source) {
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const start = index;
    const line = lines[index];

    if (line.trim() === "") {
      index += 1;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const body = [];
      index += 1;
      while (index < lines.length && !FENCE.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1; // 跳过收尾的 ```
      const language = fence[1] ? "language-" + fence[1] : undefined;
      blocks.push(element("pre", [element("code", [text(body.join("\n"))], { className: language })]));
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      // 气泡里最大到 h4：再深的层级在对话里只会显得挤。
      blocks.push(element("h" + Math.min(heading[1].length + 1, 4), inline(heading[2])));
      index += 1;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push(element("hr"));
      index += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const body = [];
      while (index < lines.length && QUOTE.test(lines[index])) {
        body.push(QUOTE.exec(lines[index])[1]);
        index += 1;
      }
      blocks.push(element("blockquote", [element("p", inline(body.join("\n")))]));
      continue;
    }

    const ordered = OL_ITEM.test(line);
    if (ordered || UL_ITEM.test(line)) {
      const items = [];
      while (index < lines.length) {
        const match = (ordered ? OL_ITEM : UL_ITEM).exec(lines[index]);
        if (!match) break;
        items.push(element("li", inline(ordered ? match[2] : match[1])));
        index += 1;
      }
      blocks.push(element(ordered ? "ol" : "ul", items));
      continue;
    }

    const table = parseTable(lines, index);
    if (table) {
      blocks.push(table.block);
      index = table.next;
      continue;
    }

    const body = [];
    while (index < lines.length && !startsBlock(lines[index])) {
      body.push(lines[index]);
      index += 1;
    }
    if (body.length === 0) {
      // 这一行"看着像块首、却不是任何块" —— 典型是流式输出时只到了一行表头、
      // 分隔行还没来。必须原样吃进段落：否则 index 不前进，主循环原地打转，
      // 一路 push 空段落直到标签页 OOM。
      body.push(lines[index]);
      index += 1;
    }
    blocks.push(element("p", inline(body.join("\n"))));

    // 兜底：任何分支只要没推进游标就强制前进一行。死循环的代价是整个标签页崩掉，
    // 而漏渲染一行的代价只是一次性显示不完美。
    if (index === start) index += 1;
  }

  return blocks;
}

function link(label, href) {
  // 只放行三种协议：别的（javascript:、data: ...）一律退化成文本，不给可点的机会。
  const safe = /^(https?:|mailto:)/i.test(href) ? href : null;
  if (!safe) return text(label + "（" + href + "）");
  return element("a", inline(label), { href: safe });
}

/** 把一行里的行内标记拆成节点。 */
function inline(source) {
  const nodes = [];
  const pattern = new RegExp(INLINE, "g");
  let last = 0;

  for (const match of source.matchAll(pattern)) {
    if (match.index > last) nodes.push(text(source.slice(last, match.index)));
    const [, code, boldStar, boldScore, italicStar, italicScore, label, href] = match;
    if (code !== undefined) nodes.push(element("code", [text(code)]));
    else if (boldStar !== undefined || boldScore !== undefined)
      nodes.push(element("strong", inline(boldStar ?? boldScore)));
    else if (italicStar !== undefined || italicScore !== undefined)
      nodes.push(element("em", inline(italicStar ?? italicScore)));
    else nodes.push(link(label, href));
    last = match.index + match[0].length;
  }

  if (last < source.length) nodes.push(text(source.slice(last)));
  return nodes;
}

function toDom(node) {
  if (node.tag === TEXT) return document.createTextNode(node.text);
  const created = document.createElement(node.tag);
  if (node.className) created.className = node.className;
  if (node.href) {
    created.href = node.href;
    created.target = "_blank";
    created.rel = "noopener noreferrer";
  }
  for (const child of node.children ?? []) created.append(toDom(child));
  return created;
}

/**
 * 把一段 Markdown 变成可以 append 的文档片段。
 *
 * 举例：bubble.append(renderMarkdown(part.text))
 */
export function renderMarkdown(source) {
  const fragment = document.createDocumentFragment();
  for (const node of parseMarkdown(source)) fragment.append(toDom(node));
  return fragment;
}
