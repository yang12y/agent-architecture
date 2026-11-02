/**
 * 图表：把后端给的一幅图（kind + x + series）画成 SVG。箱线图的 series 存的是五数概括。
 *
 * **不引任何图表库**：这个前端没有构建步骤，加一个库要连带引体积、版本、样式三样东西，
 * 而折线 / 柱状 / 饼 / 箱线这四种图本身也就几百行。画出来的 SVG 用 CSS 变量上色，跟界面同一套配色。
 *
 * 交互自己接：悬停出准星与读数、点图例隐藏系列、点标题全屏、导出 PNG。
 * 所有文字走 textContent 与 <title>，不拼 HTML 字符串（与 markdown.js 同一条规矩）。
 */

const NS = "http://www.w3.org/2000/svg";

// 画布坐标系：宽度固定、靠 viewBox 跟着容器缩放。高度固定，太高会把对话挤成一张图。
const WIDTH = 720;
const HEIGHT = 300;

// 绘图区四边的留白：左边放纵轴刻度，下边放类目轴标签。
const PAD = { top: 18, right: 16, bottom: 42, left: 54 };

// 系列配色。前两个与界面主色、强调色一致。
const COLORS = ["#2f6fed", "#e8710a", "#2e9e5b", "#a259d9", "#d1483f", "#0fa3b1"];

// 类目轴最多标几个标签。再多就隔几个标一个，否则字挤成一团。
const MAX_X_LABELS = 9;

// 类目轴标签最多显示几个字，超了截断 —— 完整值在悬浮提示与数据表里。
const MAX_X_LABEL_CHARS = 10;

// 点数超过这个数就不画圆点了：两百个点会糊成一团。
const MAX_DOTS = 24;

// 饼图每片小于这个占比就不在图上写百分比（会重叠），只留在图例与数据表里。
const MIN_SLICE_LABEL = 0.07;

// 图种的中文名。
const KIND_LABEL = {
  line: "折线图",
  bar: "柱状图",
  pie: "饼图",
  box: "箱线图",
  time_domain: "时域波形",
  frequency_domain: "频谱",
  envelope: "包络谱",
};

// 三种信号图：数值是 base64 float32，横轴靠 step / bins / 采样率换算出来。
// 它们不是模型给的类目数据，所以图例、系列开关、悬停读数一概不参与。
//
// 举例：SIGNAL_KINDS.has("frequency_domain") -> true
const SIGNAL_KINDS = new Set(["time_domain", "frequency_domain", "envelope"]);

// 导出 PNG 时内联进 SVG 的样式。序列化出来的文件不带页面 CSS，不带这一份就是黑白的。
// 颜色与 style.css 里的变量保持一致 —— 导出的是图片，不是网页，只能写死。
const EXPORT_STYLE = [
  "text { font-family: system-ui, -apple-system, Segoe UI, Microsoft YaHei, sans-serif; }",
  ".chart-grid { stroke: #e4e5e9; stroke-width: 1; }",
  ".chart-tick { fill: #6b7076; font-size: 11px; }",
  ".chart-axis-title { fill: #6b7076; font-size: 12px; }",
  ".chart-dot { stroke: #ffffff; stroke-width: 1.5; }",
  ".chart-slice-label { fill: #ffffff; font-size: 12px; font-weight: 600; }",
  ".chart-crosshair, .chart-band, .chart-readout { display: none; }",
  "svg { background: #ffffff; }",
].join("");

// 每幅图的界面状态（哪些系列被点掉了）。按 chart_id 记：
// 消息列表重建之后，用户点掉的那条系列还是点掉的。
const CHART_STATE = new Map();

/** 取一幅图的状态，没有就建一份。
 *
 * 举例：stateOf(chart) -> { hidden: Set(0) }
 */
function stateOf(chart) {
  let state = CHART_STATE.get(chart.chart_id);
  if (!state) {
    state = { hidden: new Set() };
    CHART_STATE.set(chart.chart_id, state);
  }
  return state;
}

/**
 * 把一幅图画成 <figure>：标题 + 图 + 图例 + 可展开的数据表。
 *
 * 数据表不是装饰：图看趋势，表读精确值；它同时是屏幕阅读器的兜底。
 *
 * 举例：renderChartNode({kind: "line", title: "压力", x: ["a"], series: [...]})
 */
export function renderChartNode(chart) {
  const state = stateOf(chart);
  const signal = signalOf(chart);
  const figure = element("figure", "chart chart--" + chart.kind);

  const head = element("figcaption", "chart-head");
  // 标题本身就是「放大」的入口：光标变放大镜，点了开全屏。
  const title = element("button", "chart-title", chart.title);
  title.type = "button";
  title.dataset.chartAction = "zoom";
  title.title = "点开看大图";
  const meta = element("span", "chart-meta");
  const unit = chart.kind === "box" ? " 组" : " 个数据点";
  const count = signal ? signal.values.length : chart.x.length;
  meta.append(
    element("span", "chart-kind", (KIND_LABEL[chart.kind] || chart.kind) + " · " + count + unit),
  );
  const actions = element("span", "chart-actions");
  actions.append(actionButton("zoom", "放大"), actionButton("png", "PNG"));
  meta.append(actions);
  head.append(title, meta);
  figure.append(head);

  const body = element("div", "chart-body");
  const built = signal
    ? { svg: signalSvg(chart, signal), geometry: null }
    : chart.kind === "pie"
      ? { svg: pieSvg(chart), geometry: null }
      : chart.kind === "box"
        ? { svg: boxSvg(chart), geometry: null }
        : axesSvg(chart);
  body.append(built.svg);
  figure.append(body);

  // 信号图只有一条曲线，没有图例可点。
  if (!signal && (chart.kind === "pie" || chart.series.length > 1)) {
    figure.append(legendNode(chart, chart.kind === "pie"));
  }
  figure.append(dataNode(chart, signal));

  // 恢复上次的隐藏状态（重建之后别把用户点掉的系列又画回来）。
  applySeriesState(figure, state);
  if (built.geometry) attachHover(figure, built.svg, chart, built.geometry, state);

  figure.addEventListener("click", (event) => {
    const action = event.target.closest("[data-chart-action]");
    if (action) {
      if (action.dataset.chartAction === "zoom") zoomChart(chart);
      else if (action.dataset.chartAction === "png") exportPng(built.svg, chart);
      return;
    }
    const copy = event.target.closest("[data-chart-copy]");
    if (copy) {
      // 复制键在 <summary> 里：不拦一下，点它会把数据表收起来。
      event.preventDefault();
      copyAsTable(copy, chart);
      return;
    }
    const legend = event.target.closest("[data-legend]");
    if (legend && built.geometry) toggleSeries(figure, chart, Number(legend.dataset.legend));
  });
  return figure;
}

/** 建一个普通节点。 */
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** 建一个 SVG 节点并一次挂好属性。 */
function svgNode(tag, attributes) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes || {})) {
    node.setAttribute(key, String(value));
  }
  return node;
}

/** 标题行右侧的小按钮。 */
function actionButton(action, text) {
  const button = element("button", "chart-action", text);
  button.type = "button";
  button.dataset.chartAction = action;
  return button;
}

/** 悬浮提示。浏览器原生就能显示，不用自己写浮层。 */
function tipNode(text) {
  const tip = svgNode("title", {});
  tip.textContent = text;
  return tip;
}
// ---------- 信号图：时域 / 频域 / 包络 ----------

/**
 * 把信号图里的 base64 解成 float32 采样；不是信号图、或者数据坏了就返回 null。
 *
 * 解码在前端做是有意的：后端只搬字节，画多宽、画哪段由界面决定。
 *
 * 举例：signalOf({kind: "frequency_domain", signal: {data: "AAAAgD8=", …}}).values[0] -> 0
 */
function signalOf(chart) {
  const signal = chart.signal;
  if (!signal || !SIGNAL_KINDS.has(chart.kind) || !signal.data) return null;
  try {
    const binary = atob(signal.data);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return { ...signal, values: new Float32Array(bytes.buffer) };
  } catch {
    return null;
  }
}

/**
 * 第 index 个采样点对应的横坐标：时域是秒，频域与包络是赫兹。
 *
 * 间隔由服务端算好（axis_step）：它在打包时才知道谱宽与原始点数，
 * 前端自己推必然要猜 FFT 是单边还是双边，猜错就是差一倍。
 *
 * 举例：signalX({axis_step: 0.625}, "frequency_domain", 40) -> 25
 */
function signalX(signal, index) {
  return index * (Number(signal.axis_step) || 0);
}

/** 横轴单位：时域讲秒，频谱讲赫兹。 */
function signalUnit(kind) {
  return kind === "time_domain" ? "时间（s）" : "频率（Hz）";
}

/**
 * 画一条信号曲线：一次连线 + 谱峰标记。
 *
 * 谱峰用圆点标出来并带悬浮提示 —— 判定对不对，全看这几个峰站不站得住。
 *
 * 举例：signalSvg({kind: "frequency_domain", …}, signal) -> <svg>
 */
function signalSvg(chart, signal) {
  const values = signal.values;
  const svg = svgNode("svg", {
    viewBox: "0 0 " + WIDTH + " " + HEIGHT,
    role: "img",
    "aria-label": chart.title,
  });
  const plot = {
    x: PAD.left,
    y: PAD.top,
    w: WIDTH - PAD.left - PAD.right,
    h: HEIGHT - PAD.top - PAD.bottom,
  };

  const count = values.length;
  const scale = niceScale(Math.min(0, ...values), Math.max(...values), 5);
  const yOf = (value) => plot.y + plot.h - ((value - scale.lo) / (scale.hi - scale.lo)) * plot.h;
  const xOf = (index) => plot.x + (count <= 1 ? plot.w / 2 : (index / (count - 1)) * plot.w);

  for (const tick of scale.ticks) {
    const y = yOf(tick);
    svg.append(
      svgNode("line", { x1: plot.x, x2: plot.x + plot.w, y1: y, y2: y, class: "chart-grid" }),
    );
    const label = svgNode("text", {
      x: plot.x - 8,
      y: y + 4,
      class: "chart-tick",
      "text-anchor": "end",
    });
    label.textContent = formatNumber(tick);
    svg.append(label);
  }

  // 横轴只标 9 个以内的刻度：点太密，字会叠在一起。
  const stride = Math.max(1, Math.ceil(count / MAX_X_LABELS));
  for (let index = 0; index < count; index += stride) {
    const label = svgNode("text", {
      x: xOf(index),
      y: plot.y + plot.h + 18,
      class: "chart-tick",
      "text-anchor": "middle",
    });
    label.textContent = formatNumber(signalX(signal, index));
    svg.append(label);
  }

  const points = Array.from(values, (value, index) => xOf(index) + "," + yOf(value)).join(" ");
  svg.append(
    svgNode("polyline", {
      points,
      fill: "none",
      stroke: COLORS[0],
      "stroke-width": "1.2",
      "stroke-linejoin": "round",
    }),
  );

  for (const peak of signal.peaks || []) {
    const index = peakIndex(signal, chart.kind, peak.frequency);
    if (index < 0 || index >= count) continue;
    const dot = svgNode("circle", { cx: xOf(index), cy: yOf(values[index]), r: 3, class: "chart-dot" });
    dot.setAttribute("fill", COLORS[4]);
    dot.append(
      tipNode(
        "频率 " +
          formatNumber(peak.frequency) +
          " Hz · 幅值 " +
          formatNumber(peak.amplitude) +
          " " +
          (signal.unit || ""),
      ),
    );
    svg.append(dot);
  }

  const yLabel = svgNode("text", {
    x: 14,
    y: HEIGHT / 2,
    class: "chart-axis-title",
    "text-anchor": "middle",
    transform: "rotate(-90 14 " + HEIGHT / 2 + ")",
  });
  yLabel.textContent = signal.unit || "";
  svg.append(yLabel);

  const xLabel = svgNode("text", {
    x: plot.x + plot.w / 2,
    y: HEIGHT - 6,
    class: "chart-axis-title",
    "text-anchor": "middle",
  });
  xLabel.textContent = signalUnit(chart.kind);
  svg.append(xLabel);

  return svg;
}

/**
 * 谱峰频率 → 采样点序号（signalX 的反函数）；时域图没有峰，返回 -1。
 *
 * 举例：peakIndex({axis_step: 0.625}, "frequency_domain", 25) -> 40
 */
function peakIndex(signal, kind, frequency) {
  const step = Number(signal.axis_step) || 0;
  if (kind === "time_domain" || step <= 0) return -1;
  return Math.round(frequency / step);
}

/**
 * 折线图与柱状图：一套坐标轴。
 *
 * 返回 { svg, geometry } —— geometry 是悬停读数要用的那几样（每格中心、列宽、准星、高亮带），
 * 画的时候顺手算好，比悬停时再算一遍可靠。
 */
function axesSvg(chart) {
  const svg = svgNode("svg", {
    viewBox: "0 0 " + WIDTH + " " + HEIGHT,
    role: "img",
    "aria-label": chart.title,
  });
  const plot = {
    x: PAD.left,
    y: PAD.top,
    w: WIDTH - PAD.left - PAD.right,
    h: HEIGHT - PAD.top - PAD.bottom,
  };

  const values = [];
  for (const series of chart.series) values.push(...series.values);
  // 柱状图从 0 起算 —— 截掉基线会让柱子之间的大小关系失真；折线图不必。
  const lowest = chart.kind === "bar" ? Math.min(0, ...values) : Math.min(...values);
  const highest = chart.kind === "bar" ? Math.max(0, ...values) : Math.max(...values);
  const scale = niceScale(lowest, highest, 5);
  const yOf = (value) => plot.y + plot.h - ((value - scale.lo) / (scale.hi - scale.lo)) * plot.h;
  const xOf = xPositioner(chart, plot);
  const centers = chart.x.map((_, index) => xOf(index));
  const band =
    chart.kind === "bar" ? plot.w / chart.x.length : plot.w / Math.max(1, chart.x.length - 1);

  // 悬停高亮带：先加，后面画的都盖在它上面。
  const bandRect = svgNode("rect", {
    class: "chart-band",
    x: 0,
    y: plot.y,
    width: band,
    height: plot.h,
    visibility: "hidden",
  });
  svg.append(bandRect);

  for (const tick of scale.ticks) {
    const y = yOf(tick);
    svg.append(
      svgNode("line", { x1: plot.x, x2: plot.x + plot.w, y1: y, y2: y, class: "chart-grid" }),
    );
    const label = svgNode("text", {
      x: plot.x - 8,
      y: y + 4,
      class: "chart-tick",
      "text-anchor": "end",
    });
    label.textContent = formatNumber(tick);
    svg.append(label);
  }

  const stride = Math.max(1, Math.ceil(chart.x.length / MAX_X_LABELS));
  chart.x.forEach((name, index) => {
    if (index % stride !== 0 && index !== chart.x.length - 1) return;
    const label = svgNode("text", {
      x: centers[index],
      y: plot.y + plot.h + 18,
      class: "chart-tick",
      "text-anchor": "middle",
    });
    label.textContent = shorten(name);
    const tip = svgNode("title", {});
    tip.textContent = name;
    label.append(tip);
    svg.append(label);
  });

  // 一条系列一个 <g>：点图例时整组隐藏，不用重画。
  chart.series.forEach((series, index) => {
    const group = svgNode("g", { class: "chart-series" });
    group.dataset.series = String(index);
    if (chart.kind === "line") drawLine(group, chart, xOf, yOf, series, index);
    else drawBars(group, chart, plot, yOf, series, index);
    svg.append(group);
  });

  // 准星最后加，画在数据上面。
  const crosshair = svgNode("line", {
    class: "chart-crosshair",
    x1: 0,
    x2: 0,
    y1: plot.y,
    y2: plot.y + plot.h,
    visibility: "hidden",
  });
  svg.append(crosshair);

  if (chart.y_title) {
    const label = svgNode("text", {
      x: 14,
      y: HEIGHT / 2,
      class: "chart-axis-title",
      "text-anchor": "middle",
      transform: "rotate(-90 14 " + HEIGHT / 2 + ")",
    });
    label.textContent = chart.y_title;
    svg.append(label);
  }
  if (chart.x_title) {
    const label = svgNode("text", {
      x: plot.x + plot.w / 2,
      y: HEIGHT - 6,
      class: "chart-axis-title",
      "text-anchor": "middle",
    });
    label.textContent = chart.x_title;
    svg.append(label);
  }

  return { svg, geometry: { plot, yOf, scale, centers, band, crosshair, bandRect } };
}

/** 一格一个横坐标：折线铺满整个宽度，柱子按等宽的格子居中。 */
function xPositioner(chart, plot) {
  const count = chart.x.length;
  if (chart.kind === "bar") {
    const band = plot.w / count;
    return (index) => plot.x + band * (index + 0.5);
  }
  return (index) => plot.x + (count === 1 ? plot.w / 2 : (index / (count - 1)) * plot.w);
}

/** 折线：一条系列一条折线，点少的时候补圆点。 */
function drawLine(group, chart, xOf, yOf, series, seriesIndex) {
  const color = COLORS[seriesIndex % COLORS.length];
  const points = series.values.map((value, index) => xOf(index) + "," + yOf(value)).join(" ");
  group.append(
    svgNode("polyline", {
      points,
      fill: "none",
      stroke: color,
      "stroke-width": 2,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    }),
  );
  if (series.values.length > MAX_DOTS) return;
  series.values.forEach((value, index) => {
    const dot = svgNode("circle", {
      cx: xOf(index),
      cy: yOf(value),
      r: 3,
      fill: color,
      class: "chart-dot",
    });
    dot.append(tipNode(chart.x[index] + " · " + series.name + "：" + formatNumber(value)));
    group.append(dot);
  });
}

/** 柱状：同一格里的多条系列并排。 */
function drawBars(group, chart, plot, yOf, series, seriesIndex) {
  const count = chart.x.length;
  const band = plot.w / count;
  const gap = Math.min(10, band * 0.2);
  const slot = (band - gap) / chart.series.length;
  const base = plot.y + plot.h;
  const color = COLORS[seriesIndex % COLORS.length];
  series.values.forEach((value, index) => {
    const y = yOf(value);
    const top = Math.min(y, base);
    const rect = svgNode("rect", {
      x: plot.x + band * index + gap / 2 + slot * seriesIndex,
      y: top,
      width: Math.max(1, slot * 0.86),
      height: Math.max(1, Math.abs(base - y)),
      rx: 2,
      fill: color,
      class: "chart-bar",
    });
    rect.append(tipNode(chart.x[index] + " · " + series.name + "：" + formatNumber(value)));
    group.append(rect);
  });
}
/**
 * 箱线图：一格一组样本，画成「箱体 P25~P75 + 中位数横线 + 到极值的须线」。
 *
 * 数据约定（与后端 contracts/charts.py 一致）：每条系列的 values 是 5 × 格数，
 * 依次是 最小值 / P25 / 中位数 / P75 / 最大值 —— 分位数是工具算好传下来的，这里不重算。
 * 须线直接到最小/最大值，不剔异常点；要看单个离群值请回原始明细。
 */
function boxSvg(chart) {
  const svg = svgNode("svg", {
    viewBox: "0 0 " + WIDTH + " " + HEIGHT,
    role: "img",
    "aria-label": chart.title,
  });
  const plot = {
    x: PAD.left,
    y: PAD.top,
    w: WIDTH - PAD.left - PAD.right,
    h: HEIGHT - PAD.top - PAD.bottom,
  };

  const values = [];
  for (const series of chart.series) values.push(...series.values);
  const scale = niceScale(Math.min(...values), Math.max(...values), 5);
  const yOf = (value) => plot.y + plot.h - ((value - scale.lo) / (scale.hi - scale.lo)) * plot.h;

  for (const tick of scale.ticks) {
    const y = yOf(tick);
    svg.append(
      svgNode("line", { x1: plot.x, x2: plot.x + plot.w, y1: y, y2: y, class: "chart-grid" }),
    );
    const label = svgNode("text", {
      x: plot.x - 8,
      y: y + 4,
      class: "chart-tick",
      "text-anchor": "end",
    });
    label.textContent = formatNumber(tick);
    svg.append(label);
  }

  const count = chart.x.length;
  const band = plot.w / count;
  const centers = chart.x.map((_, index) => plot.x + band * (index + 0.5));

  const stride = Math.max(1, Math.ceil(chart.x.length / MAX_X_LABELS));
  chart.x.forEach((name, index) => {
    if (index % stride !== 0 && index !== chart.x.length - 1) return;
    const label = svgNode("text", {
      x: centers[index],
      y: plot.y + plot.h + 18,
      class: "chart-tick",
      "text-anchor": "middle",
    });
    label.textContent = shorten(name);
    const tip = svgNode("title", {});
    tip.textContent = name;
    label.append(tip);
    svg.append(label);
  });

  // 同一格里多条系列并排：每条占一个窄槽，互不遮挡。
  const gap = Math.min(10, band * 0.2);
  const slot = (band - gap) / chart.series.length;
  chart.series.forEach((series, seriesIndex) => {
    const color = COLORS[seriesIndex % COLORS.length];
    const group = svgNode("g", { class: "chart-series" });
    group.dataset.series = String(seriesIndex);
    chart.x.forEach((name, index) => {
      const base = index * 5;
      const [min, q1, median, q3, max] = series.values.slice(base, base + 5);
      if ([min, q1, median, q3, max].some((value) => !Number.isFinite(value))) return;
      const cx = plot.x + band * index + gap / 2 + slot * seriesIndex + slot / 2;
      const half = Math.max(6, Math.min(28, slot * 0.34));

      // 须线：从最小值到最大值，两端各一道短横帽。
      group.append(
        svgNode("line", {
          x1: cx,
          x2: cx,
          y1: yOf(min),
          y2: yOf(max),
          stroke: color,
          "stroke-width": 1.2,
          class: "chart-whisker",
        }),
      );
      for (const edge of [min, max]) {
        group.append(
          svgNode("line", {
            x1: cx - half / 2,
            x2: cx + half / 2,
            y1: yOf(edge),
            y2: yOf(edge),
            stroke: color,
            "stroke-width": 1.2,
            class: "chart-whisker",
          }),
        );
      }

      // 箱体：P75 在上、P25 在下（y 轴向下，所以用 q3 当顶）。
      const box = svgNode("rect", {
        x: cx - half,
        y: yOf(q3),
        width: half * 2,
        height: Math.max(1, yOf(q1) - yOf(q3)),
        fill: color,
        "fill-opacity": 0.16,
        stroke: color,
        "stroke-width": 1.4,
        class: "chart-box",
      });
      box.append(
        tipNode(
          name +
            " · " +
            series.name +
            "：最小 " +
            formatNumber(min) +
            " / P25 " +
            formatNumber(q1) +
            " / 中位数 " +
            formatNumber(median) +
            " / P75 " +
            formatNumber(q3) +
            " / 最大 " +
            formatNumber(max),
        ),
      );
      group.append(box);

      // 中位数：箱体中间那道实线。
      group.append(
        svgNode("line", {
          x1: cx - half,
          x2: cx + half,
          y1: yOf(median),
          y2: yOf(median),
          stroke: color,
          "stroke-width": 2.2,
          class: "chart-median",
        }),
      );
    });
    svg.append(group);
  });

  if (chart.y_title) {
    const label = svgNode("text", {
      x: 14,
      y: HEIGHT / 2,
      class: "chart-axis-title",
      "text-anchor": "middle",
      transform: "rotate(-90 14 " + HEIGHT / 2 + ")",
    });
    label.textContent = chart.y_title;
    svg.append(label);
  }
  if (chart.x_title) {
    const label = svgNode("text", {
      x: plot.x + plot.w / 2,
      y: HEIGHT - 6,
      class: "chart-axis-title",
      "text-anchor": "middle",
    });
    label.textContent = chart.x_title;
    svg.append(label);
  }
  return svg;
}

/** 饼图：一片一个扇形，占比够大的片上写百分比。 */
function pieSvg(chart) {
  const svg = svgNode("svg", {
    viewBox: "0 0 " + WIDTH + " " + HEIGHT,
    role: "img",
    "aria-label": chart.title,
  });
  const series = chart.series[0];
  const values = series.values.map((value) => Math.max(0, value));
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0) {
    const note = svgNode("text", {
      x: WIDTH / 2,
      y: HEIGHT / 2,
      class: "chart-tick",
      "text-anchor": "middle",
    });
    note.textContent = "这些数值里没有可画的比例";
    svg.append(note);
    return svg;
  }

  const cx = WIDTH / 2;
  const cy = HEIGHT / 2;
  const radius = Math.min(WIDTH * 0.42, HEIGHT * 0.44);
  let angle = -Math.PI / 2;
  values.forEach((value, index) => {
    const share = value / total;
    const next = angle + share * Math.PI * 2;
    const color = COLORS[index % COLORS.length];
    const slice = svgNode("path", {
      d: arcPath(cx, cy, radius, angle, next),
      fill: color,
      stroke: "#fff",
      "stroke-width": 1,
    });
    slice.append(
      tipNode(
        chart.x[index] + "：" + formatNumber(value) + "（" + (share * 100).toFixed(1) + "%）",
      ),
    );
    svg.append(slice);

    if (share >= MIN_SLICE_LABEL) {
      const mid = (angle + next) / 2;
      const label = svgNode("text", {
        x: cx + Math.cos(mid) * radius * 0.62,
        y: cy + Math.sin(mid) * radius * 0.62 + 4,
        class: "chart-slice-label",
        "text-anchor": "middle",
      });
      label.textContent = (share * 100).toFixed(0) + "%";
      svg.append(label);
    }
    angle = next;
  });
  return svg;
}

/** 扇形路径：从圆心拉两条边，再画一段弧。 */
function arcPath(cx, cy, radius, start, end) {
  const x1 = cx + radius * Math.cos(start);
  const y1 = cy + radius * Math.sin(start);
  const x2 = cx + radius * Math.cos(end);
  const y2 = cy + radius * Math.sin(end);
  const large = end - start > Math.PI ? 1 : 0;
  return (
    "M " + cx + " " + cy + " L " + x1 + " " + y1 +
    " A " + radius + " " + radius + " 0 " + large + " 1 " + x2 + " " + y2 + " Z"
  );
}

/**
 * 图例。饼图列每一片（带占比），多条系列时列系列名。
 *
 * 多系列时每条是**按钮**：点一下把那组藏起来，再点回来。
 */
function legendNode(chart, withShare) {
  const box = element("div", "chart-legend");
  const values = chart.series[0].values;
  const total = values.reduce((sum, value) => sum + Math.max(0, value), 0);

  if (withShare) {
    chart.x.forEach((name, index) => {
      const share = total > 0 ? (Math.max(0, values[index]) / total) * 100 : 0;
      box.append(
        legendItem(
          COLORS[index % COLORS.length],
          name + " " + formatNumber(values[index]) + "（" + share.toFixed(1) + "%）",
          null,
        ),
      );
    });
    return box;
  }

  chart.series.forEach((series, index) => {
    box.append(legendItem(COLORS[index % COLORS.length], series.name, index));
  });
  return box;
}

/** 图例里的一条：一个小方块 + 一行字。传了序号就是可点击的按钮。 */
function legendItem(color, text, seriesIndex) {
  const item = element(seriesIndex === null ? "span" : "button", "chart-legend-item");
  if (seriesIndex !== null) {
    item.type = "button";
    item.dataset.legend = String(seriesIndex);
    item.title = "点一下隐藏/显示这条系列";
  }
  const dot = element("i", "chart-legend-dot");
  dot.style.background = color;
  item.append(dot, element("span", null, text));
  return item;
}

/** 可展开的数据表：图看趋势，它读精确值，也是屏幕阅读器的兜底。 */
function dataNode(chart, signal) {
  const details = element("details", "chart-data");
  const summary = element("summary", "chart-data-head");
  summary.append(element("span", null, "数据"));
  // 复制键放在标题行里：不展开也能复制。点它时 figure 那边会拦掉「收起」。
  const copy = element("button", "chart-copy", "复制 TSV");
  copy.type = "button";
  copy.dataset.chartCopy = "1";
  copy.title = "复制成表：制表符分隔，粘进 Excel / 飞书表格就是一张表";
  summary.append(copy);
  details.append(summary);

  if (signal) {
    details.append(signalTable(chart, signal));
    return details;
  }
  if (chart.kind === "box") {
    details.append(boxTable(chart));
    return details;
  }

  const table = element("table", null);
  const head = element("thead", null);
  const headRow = element("tr", null);
  headRow.append(cellNode("th", "类别"));
  for (const series of chart.series) headRow.append(cellNode("th", series.name));
  head.append(headRow);

  const body = element("tbody", null);
  chart.x.forEach((name, index) => {
    const row = element("tr", null);
    row.append(cellNode("td", name));
    for (const series of chart.series) row.append(cellNode("td", formatNumber(series.values[index])));
    body.append(row);
  });

  table.append(head, body);
  details.append(table);
  return details;
}

/**
 * 信号图的数据表：有谱峰就列谱峰，没有（时域）就列前 20 个采样点。
 *
 * 谱峰那张表才是研判的依据 —— 频率、幅值、对得上哪个倍频，都在这几行里。
 *
 * 举例：signalTable({kind: "frequency_domain", …}, signal) -> <table>
 */
function signalTable(chart, signal) {
  const peaks = signal.peaks || [];
  const table = element("table", null);
  const head = element("thead", null);
  const headRow = element("tr", null);
  const axis = chart.kind === "time_domain" ? "时间（s）" : "频率（Hz）";
  for (const name of [axis, signal.unit || "幅值"]) headRow.append(cellNode("th", name));
  head.append(headRow);

  const body = element("tbody", null);
  if (peaks.length > 0) {
    for (const peak of peaks) {
      const row = element("tr", null);
      row.append(
        cellNode("td", formatNumber(peak.frequency)),
        cellNode("td", formatNumber(peak.amplitude)),
      );
      body.append(row);
    }
  } else {
    const limit = Math.min(signal.values.length, 20);
    for (let index = 0; index < limit; index += 1) {
      const row = element("tr", null);
      row.append(
        cellNode("td", formatNumber(signalX(signal, index))),
        cellNode("td", formatNumber(signal.values[index])),
      );
      body.append(row);
    }
  }

  table.append(head, body);
  return table;
}

/** 箱线图的数据表：一行 = 一格 × 一条系列，列是那 5 个数。 */
function boxTable(chart) {
  const table = element("table", null);
  const head = element("thead", null);
  const headRow = element("tr", null);
  for (const name of ["类别", "系列", "最小值", "P25", "中位数", "P75", "最大值"]) {
    headRow.append(cellNode("th", name));
  }
  head.append(headRow);

  const body = element("tbody", null);
  chart.x.forEach((name, index) => {
    for (const series of chart.series) {
      const base = index * 5;
      const row = element("tr", null);
      row.append(cellNode("td", name), cellNode("td", series.name));
      for (let offset = 0; offset < 5; offset += 1) {
        row.append(cellNode("td", formatNumber(series.values[base + offset])));
      }
      body.append(row);
    }
  });
  table.append(head, body);
  return table;
}

/** 表头/格子。 */
function cellNode(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}
// ---------- 交互：悬停读数、点图例、放大、导出 ----------

/**
 * 挂上悬停读数：一条准星 + 一条高亮带 + 一个读数小窗。
 *
 * 坐标换算是从屏幕像素反推回 viewBox 的 720 宽：SVG 被 CSS 拉伸过，
 * 直接拿 offsetX 会跟画布对不上。
 */
function attachHover(figure, svg, chart, geometry, state) {
  const body = figure.querySelector(".chart-body");
  const readout = element("div", "chart-readout");
  readout.hidden = true;
  if (body) body.append(readout);

  const hide = () => {
    geometry.crosshair.setAttribute("visibility", "hidden");
    geometry.bandRect.setAttribute("visibility", "hidden");
    readout.hidden = true;
  };

  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    if (!rect || !rect.width) return;
    const x = ((event.clientX - rect.left) / rect.width) * WIDTH;
    const index = nearestIndex(geometry.centers, x);
    if (index < 0) return;
    const center = geometry.centers[index];
    geometry.crosshair.setAttribute("x1", center);
    geometry.crosshair.setAttribute("x2", center);
    geometry.crosshair.setAttribute("visibility", "visible");
    geometry.bandRect.setAttribute("x", center - geometry.band / 2);
    geometry.bandRect.setAttribute("width", geometry.band);
    geometry.bandRect.setAttribute("visibility", "visible");
    paintReadout(body, readout, chart, state, index, event);
  });
}

/** 读数小窗：这一格的标签 + 每条可见系列的值。 */
function paintReadout(body, readout, chart, state, index, event) {
  readout.replaceChildren();
  readout.append(element("div", "chart-readout-head", chart.x[index]));
  chart.series.forEach((series, seriesIndex) => {
    if (state.hidden.has(seriesIndex)) return;
    const row = element("div", "chart-readout-row");
    const dot = element("i", "chart-readout-dot");
    dot.style.background = COLORS[seriesIndex % COLORS.length];
    row.append(
      dot,
      element("span", null, series.name),
      element("strong", null, formatNumber(series.values[index])),
    );
    readout.append(row);
  });
  readout.hidden = false;

  if (!body) return;
  const bodyRect = body.getBoundingClientRect();
  if (!bodyRect || !bodyRect.width) return;
  // 跟着光标走，靠右边时翻到光标左侧，别被图表边缘裁掉。
  const at = event.clientX - bodyRect.left;
  const left = at > bodyRect.width - 150 ? Math.max(4, at - 150) : at + 14;
  readout.style.left = left + "px";
  readout.style.top = "10px";
}

/** 离光标最近的那一格；没有格子时返回 -1。 */
function nearestIndex(centers, x) {
  let best = -1;
  let bestDistance = Infinity;
  centers.forEach((center, index) => {
    const distance = Math.abs(center - x);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  });
  return best;
}

/** 按状态把系列藏起来 / 放出来，并给图例打上「关掉了」的样子。 */
function applySeriesState(figure, state) {
  for (const group of figure.querySelectorAll(".chart-series")) {
    const index = Number(group.dataset.series);
    group.style.display = state.hidden.has(index) ? "none" : "";
  }
  for (const item of figure.querySelectorAll("[data-legend]")) {
    const off = state.hidden.has(Number(item.dataset.legend));
    item.dataset.off = off ? "true" : "false";
    item.setAttribute("aria-pressed", off ? "false" : "true");
  }
}

/** 点图例：把那条系列藏起来或放回来。 */
function toggleSeries(figure, chart, seriesIndex) {
  if (!Number.isFinite(seriesIndex)) return;
  const state = stateOf(chart);
  if (state.hidden.has(seriesIndex)) state.hidden.delete(seriesIndex);
  else state.hidden.add(seriesIndex);
  applySeriesState(figure, state);
  const readout = figure.querySelector(".chart-readout");
  if (readout) readout.hidden = true;
}

// 全屏放大用的那层：点标题时按需建，建一次就复用。
let zoomLayer = null;

/** 把一幅图放大到整屏看。里面是重新画的一份，交互照样能用。 */
function zoomChart(chart) {
  const layer = ensureZoomLayer();
  layer.replaceChildren();
  const inner = element("div", "chart-zoom-inner");
  inner.append(renderChartNode(chart));
  layer.append(inner);
  layer.hidden = false;
}

function ensureZoomLayer() {
  if (zoomLayer) return zoomLayer;
  zoomLayer = element("div", "chart-zoom");
  zoomLayer.hidden = true;
  zoomLayer.addEventListener("click", (event) => {
    if (event.target === zoomLayer) closeZoom();
  });
  const host = globalThis.document?.body ?? globalThis.document;
  if (host) host.append(zoomLayer);
  globalThis.document?.addEventListener?.("keydown", (event) => {
    if (event.key === "Escape" && zoomLayer && !zoomLayer.hidden) closeZoom();
  });
  return zoomLayer;
}

function closeZoom() {
  if (!zoomLayer) return;
  zoomLayer.hidden = true;
  zoomLayer.replaceChildren();
}

// ---------- 复制成表 ----------

/**
 * 把一幅图摊成 TSV：一行表头（类别 + 各系列名），每个类别一行。
 *
 * 数值用原样，不用图上那个短写法 —— 粘进 Excel 是为了接着算，精度别丢。
 *
 * 举例：chartTsv({x: ["a"], series: [{name: "甲", values: [1.5]}]})
 *       -> "类别\t甲\na\t1.5"
 */
export function chartTsv(chart) {
  const signal = signalOf(chart);
  if (signal) {
    const axis = chart.kind === "time_domain" ? "时间(s)" : "频率(Hz)";
    const head = [axis, signal.unit || "幅值"].join("\t");
    const peaks = signal.peaks || [];
    const rows = peaks.length
      ? peaks.map((peak) => [String(peak.frequency), String(peak.amplitude)].join("\t"))
      : Array.from(signal.values, (value, index) =>
          [String(signalX(signal, index)), String(value)].join("\t"),
        );
    return [head, ...rows].join("\n");
  }
  if (chart.kind === "box") {
    // 箱线图一行是一格 × 一条系列：5 个统计量各占一列，粘进 Excel 能直接接着算。
    const boxHead = ["类别", "系列", "最小值", "P25", "中位数", "P75", "最大值"].join("\t");
    const boxRows = [];
    chart.x.forEach((name, index) => {
      const base = index * 5;
      for (const series of chart.series) {
        const stats = series.values.slice(base, base + 5).map(String);
        boxRows.push([name, series.name, ...stats].join("\t"));
      }
    });
    return [boxHead, ...boxRows].join("\n");
  }
  const head = ["类别", ...chart.series.map((series) => series.name)].join("\t");
  const rows = chart.x.map((name, index) =>
    [name, ...chart.series.map((series) => String(series.values[index] ?? ""))].join("\t"),
  );
  return [head, ...rows].join("\n");
}

/** 点「复制 TSV」：写剪贴板，按钮上回一句成败，1.5 秒后复原。 */
async function copyAsTable(button, chart) {
  const ok = await writeClipboard(chartTsv(chart));
  button.textContent = ok ? "已复制" : "复制失败";
  setTimeout(() => {
    button.textContent = "复制 TSV";
  }, 1500);
}

/** 优先用异步剪贴板；拿不到就退回老的 execCommand。都不行就返回 false。 */
async function writeClipboard(text) {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的兜底
  }
  return legacyCopy(text);
}

/** 老浏览器兜底：一个藏起来的 textarea + execCommand("copy")。 */
function legacyCopy(text) {
  if (typeof document.execCommand !== "function" || !document.body) return false;
  const area = element("textarea", "chart-copy-area");
  area.value = text;
  area.setAttribute("readonly", "true");
  document.body.append(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  return ok;
}


/**
 * 把一张 SVG 画到 2 倍大的白底画布上，返回 canvas；环境不支持就给 null。
 *
 * 序列化出来的 SVG 不带页面 CSS，所以先把样式表内联进去 —— 少了这一步导出的图是黑白的。
 */
function svgToCanvas(svg) {
  return new Promise((resolve) => {
    if (typeof XMLSerializer !== "function" || typeof Image !== "function") return resolve(null);
    if (typeof svg.cloneNode !== "function") return resolve(null);
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", NS);
    clone.setAttribute("width", String(WIDTH));
    clone.setAttribute("height", String(HEIGHT));
    const style = document.createElementNS(NS, "style");
    style.textContent = EXPORT_STYLE;
    if (typeof clone.insertBefore === "function") clone.insertBefore(style, clone.firstChild);
    let markup = "";
    try {
      markup = new XMLSerializer().serializeToString(clone);
    } catch {
      return resolve(null);
    }
    const image = new Image();
    image.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = WIDTH * 2;
        canvas.height = HEIGHT * 2;
        const context = canvas.getContext("2d");
        if (!context) return resolve(null);
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        resolve(canvas);
      } catch {
        resolve(null);
      }
    };
    image.onerror = () => resolve(null);
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
  });
}

/** 导出一张 PNG：画到 canvas 上，再走 toBlob 下载。 */
function exportPng(svg, chart) {
  svgToCanvas(svg).then((canvas) => {
    if (!canvas) return;
    canvas.toBlob((blob) => {
      if (blob) downloadBlob(blob, fileNameOf(chart));
    }, "image/png");
  });
}

/**
 * 把一幅图渲染成 PNG 的 base64（不带 data: 前缀），给 .docx 导出用。
 *
 * python-docx 不支持 SVG，所以图只能以位图进 Word；前端已经有画好的矢量，
 * 在这儿转一下比在后端装一个 SVG 光栅化依赖便宜得多。
 *
 * 举例：await chartPngBase64(chart) -> "iVBORw0KGgo..."；环境不支持时 null
 */
export async function chartPngBase64(chart) {
  const signal = signalOf(chart);
  const built = signal
    ? signalSvg(chart, signal)
    : chart.kind === "pie"
      ? pieSvg(chart)
      : chart.kind === "box"
        ? boxSvg(chart)
        : axesSvg(chart).svg;
  const canvas = await svgToCanvas(built);
  if (!canvas) return null;
  try {
    const url = canvas.toDataURL("image/png");
    return url.split(",")[1] ?? null;
  } catch {
    return null;
  }
}

function downloadBlob(blob, name) {
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return;
  const url = URL.createObjectURL(blob);
  const link = element("a", null);
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** 文件名：标题去掉路径与引号这类字符，太长截断。 */
function fileNameOf(chart) {
  const safe = String(chart.title || "图表")
    .replace(/[\\/:*?"<>|]+/g, "-")
    .trim()
    .slice(0, 40);
  return (safe || "图表") + ".png";
}
// ---------- 数值 ----------

/**
 * 挑一组好看的刻度：步长取 1 / 2 / 2.5 / 5 × 10 的整数次幂。
 *
 * 举例：niceScale(0, 97, 5) -> { lo: 0, hi: 100, ticks: [0, 20, 40, 60, 80, 100] }
 */
function niceScale(lowest, highest, count) {
  let lo = lowest;
  let hi = highest;
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = 0;
    hi = 1;
  }
  if (lo === hi) {
    // 一条平线：上下各撑开一点，否则刻度全叠在一起。
    const pad = Math.abs(lo) > 0 ? Math.abs(lo) * 0.1 : 1;
    lo -= pad;
    hi += pad;
  }
  const rawStep = (hi - lo) / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const candidates = [1, 2, 2.5, 5, 10];
  let step = magnitude * 10;
  for (const candidate of candidates) {
    if (magnitude * candidate >= rawStep) {
      step = magnitude * candidate;
      break;
    }
  }
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let index = 0; start + index * step <= end + step / 1e6; index += 1) {
    ticks.push(Number((start + index * step).toFixed(10)));
  }
  return { lo: start, hi: end, ticks };
}

/**
 * 数字的短写法：整数原样，小数最多两位，很小的数用三位有效数字。
 *
 * 举例：formatNumber(0.6200001) -> "0.62"；formatNumber(1200) -> "1200"
 */
function formatNumber(value) {
  if (!Number.isFinite(value)) return "-";
  if (Number.isInteger(value)) return String(value);
  const abs = Math.abs(value);
  if (abs >= 100) return trimZeros(value.toFixed(1));
  if (abs >= 1) return trimZeros(value.toFixed(2));
  if (abs === 0) return "0";
  return trimZeros(value.toPrecision(3));
}

/** 只去掉小数点后面的零，别把 100 变成 1。 */
function trimZeros(text) {
  if (!text.includes(".")) return text;
  return text.replace(/0+$/, "").replace(/\.$/, "");
}

/** 把一段文本截到类目轴的宽度上限，超了补省略号。 */
function shorten(text) {
  const chars = Array.from(String(text));
  if (chars.length <= MAX_X_LABEL_CHARS) return String(text);
  return chars.slice(0, MAX_X_LABEL_CHARS - 1).join("") + "…";
}
