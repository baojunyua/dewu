const feedEl = document.getElementById("feed");
const resultsEl = document.getElementById("results");
const connStatus = document.getElementById("connStatus");
const skuForm = document.getElementById("skuForm");
const skuBtn = document.getElementById("skuBtn");
const cancelBtn = document.getElementById("cancelBtn");
const imageBtn = document.getElementById("imageBtn");
const imageInput = document.getElementById("imageInput");
const pasteZone = document.getElementById("pasteZone");
const pastePreview = document.getElementById("pastePreview");
const pasteThumb = document.getElementById("pasteThumb");
const pasteCopy = document.getElementById("pasteCopy");
const pasteClear = document.getElementById("pasteClear");
const clearBtn = document.getElementById("clearBtn");
const clearResultsBtn = document.getElementById("clearResultsBtn");
const lightbox = document.getElementById("lightbox");
const lightboxImg = document.getElementById("lightboxImg");
const lightboxClose = document.getElementById("lightboxClose");
const minProfitInput = document.getElementById("minProfit");
const minSales7dInput = document.getElementById("minSales7d");
const costInputGlobal = document.getElementById("costInput");
const resultNav = document.getElementById("resultNav");
const historyTabs = document.getElementById("historyTabs");
const resultPosition = document.getElementById("resultPosition");
const resultPrev = document.getElementById("resultPrev");
const resultNext = document.getElementById("resultNext");
const historyCount = document.getElementById("historyCount");
const taskStatus = document.getElementById("taskStatus");
const taskTitle = document.getElementById("taskTitle");
const taskDetail = document.getElementById("taskDetail");
const taskTime = document.getElementById("taskTime");
const soundToggle = document.getElementById("soundToggle");
const completionToast = document.getElementById("completionToast");

let selectedFile = null;
let previewUrl = null;
let busy = false;
let busyMode = null; // "sku" | "image" | null
let ws;
let pingTimer;
const cardDataMap = new WeakMap();
const resultCards = new Map();
const completedJobs = new Set();
let activeJobId = null;
const MAX_RESULTS = 3;
const ROWS_PER_PAGE = 8;
let resultHistory = [];
let selectedResultId = null;
let taskStartedAt = 0;
let taskTimer;
let toastTimer;
let toastResultId = null;
let audioContext;
let soundEnabled = false;
try { soundEnabled = localStorage.getItem("dewu-completion-sound") === "true"; } catch {}

function icons() {
  window.lucide?.createIcons();
}

function icon(name) {
  return `<i data-lucide="${name}" aria-hidden="true"></i>`;
}

function updateTask(state, title, detail) {
  taskStatus.hidden = state !== "running";
  taskStatus.dataset.state = state;
  taskTitle.textContent = title;
  taskDetail.textContent = detail;
  document.getElementById("taskSymbol").innerHTML = icon(
    state === "running" ? "loader-circle" : state === "success" ? "circle-check"
      : state === "warning" ? "triangle-alert" : state === "error" ? "circle-x" : "circle-dot"
  );
  document.title = `${state === "running" ? "查询中" : title} · 得物决策助手`;
  icons();
}

function beginTask(query) {
  clearInterval(taskTimer);
  clearTimeout(toastTimer);
  completionToast.hidden = true;
  taskStartedAt = Date.now();
  taskTime.textContent = "0 s";
  updateTask("running", "正在查询", query);
  taskTimer = setInterval(() => {
    taskTime.textContent = `${Math.floor((Date.now() - taskStartedAt) / 1000)} s`;
  }, 1000);
}

function prepareSound() {
  if (!soundEnabled) return;
  try {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    audioContext ||= new Audio();
    if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
  } catch {}
}

function playCompletionSound() {
  if (!soundEnabled || !audioContext || audioContext.state !== "running") return;
  try {
    [660, 880].forEach((frequency, index) => {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const start = audioContext.currentTime + index * 0.16;
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.08, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.18);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.2);
    });
  } catch {}
}

function updateSoundToggle() {
  soundToggle.setAttribute("aria-checked", String(soundEnabled));
  soundToggle.title = soundEnabled ? "关闭完成提示音" : "开启完成提示音";
  soundToggle.innerHTML = icon(soundEnabled ? "volume-2" : "volume-x");
  icons();
}

function finishTask(data) {
  clearInterval(taskTimer);
  const cancelled = !data.ok && /终止/.test(data.message || "");
  const partial = data.ok && (data.warnings || []).length > 0;
  const title = cancelled ? "查询已终止" : !data.ok ? "查询失败" : partial ? "查询完成，部分数据缺失" : "查询完成";
  const state = cancelled || partial ? "warning" : data.ok ? "success" : "error";
  const sizeCount = data.size_prices?.length || 0;
  const salesCount = Object.keys(data.sales_by_size || {}).length;
  const detail = data.ok
    ? `${data.query} · ${sizeCount} 个尺码 · ${salesCount} 个尺码销量`
    : data.message || "查询未完成";
  updateTask(state, title, detail);
  taskTime.textContent = formatElapsed(data.elapsed_ms);
  completionToast.dataset.state = state;
  document.getElementById("toastSymbol").innerHTML = icon(state === "success" ? "circle-check" : "triangle-alert");
  document.getElementById("toastTitle").textContent = title;
  document.getElementById("toastDetail").textContent = detail;
  toastResultId = data.job_id || selectedResultId;
  document.getElementById("toastView").hidden = cancelled;
  completionToast.hidden = false;
  clearTimeout(toastTimer);
  if (!document.hidden) toastTimer = setTimeout(() => { completionToast.hidden = true; }, 10000);
  playCompletionSound();
  icons();
}

function rememberResult(data) {
  const id = data.job_id || `local-${Date.now()}-${Math.random()}`;
  const index = resultHistory.findIndex(entry => entry.id === id);
  if (index >= 0) {
    resultHistory[index].data = data;
  } else {
    resultHistory.unshift({ id, data });
    const removed = resultHistory.splice(MAX_RESULTS);
    for (const entry of removed) {
      resultCards.get(entry.id)?.remove();
      resultCards.delete(entry.id);
    }
    selectedResultId = id;
  }
  return id;
}

function selectResult(id) {
  if (!resultHistory.some(entry => entry.id === id)) return;
  selectedResultId = id;
  for (const [key, card] of resultCards) card.hidden = key !== id;
  renderHistoryNav();
}

function renderHistoryNav() {
  const count = resultHistory.length;
  historyCount.textContent = `${count} / ${MAX_RESULTS}`;
  resultNav.hidden = count === 0;
  clearResultsBtn.disabled = count === 0;
  const index = resultHistory.findIndex(entry => entry.id === selectedResultId);
  resultPrev.disabled = index <= 0;
  resultNext.disabled = index < 0 || index >= count - 1;
  resultPosition.textContent = count ? `${index + 1} / ${count}` : "";
  historyTabs.innerHTML = resultHistory.map((entry, position) => `
    <button type="button" role="tab" class="history-tab" aria-selected="${entry.id === selectedResultId}" tabindex="${entry.id === selectedResultId ? 0 : -1}"
      aria-controls="result-${entry.id}" id="history-${entry.id}" data-history-index="${position}"
      title="${escapeHtml(entry.data.query).replaceAll('"', '&quot;')}">
      <span>${escapeHtml(entry.data.query)}</span>${entry.data.pending_sales ? '<span class="tab-pending"></span>' : ""}
    </button>`).join("");
  historyTabs.querySelectorAll("[data-history-index]").forEach(button => {
    button.addEventListener("click", () => selectResult(resultHistory[Number(button.dataset.historyIndex)].id));
  });
}

const SKU_BTN_LABEL = "查价";
const IMAGE_BTN_LABEL = "识图查价";

function setConn(state, text) {
  connStatus.classList.remove("online", "offline");
  if (state) connStatus.classList.add(state);
  connStatus.querySelector(".label").textContent = text;
}

function ts() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function addFeed(kind, title, detail) {
  const li = document.createElement("li");
  li.className = kind || "";
  li.innerHTML = `<div class="meta">${ts()} · ${escapeHtml(title)}</div><div>${escapeHtml(
    detail
  )}</div>`;
  feedEl.prepend(li);
  while (feedEl.children.length > 80) feedEl.lastElementChild.remove();
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderRunningLabel(text) {
  return `${text}<span class="btn-dots" aria-hidden="true"></span>`;
}

function setBusy(v, mode = null) {
  busy = !!v;
  busyMode = busy ? mode || busyMode || "sku" : null;

  skuBtn.disabled = busy;
  if (cancelBtn) {
    cancelBtn.disabled = !busy;
    cancelBtn.classList.toggle("is-armed", busy);
  }
  imageBtn.disabled = busy || !selectedFile;

  const skuRunning = busy && busyMode === "sku";
  const imageRunning = busy && busyMode === "image";
  skuBtn.classList.toggle("is-running", skuRunning);
  imageBtn.classList.toggle("is-running", imageRunning);

  if (skuRunning) {
    skuBtn.innerHTML = renderRunningLabel("查询中");
  } else {
    skuBtn.textContent = SKU_BTN_LABEL;
  }

  if (imageRunning) {
    imageBtn.innerHTML = renderRunningLabel("识图中");
  } else {
    imageBtn.textContent = IMAGE_BTN_LABEL;
  }
}

function openLightbox(src) {
  lightboxImg.src = src;
  lightbox.hidden = false;
  document.body.style.overflow = "hidden";
}

function closeLightbox() {
  lightbox.hidden = true;
  lightboxImg.removeAttribute("src");
  document.body.style.overflow = "";
}

lightboxClose.addEventListener("click", (e) => {
  e.stopPropagation();
  closeLightbox();
});
lightbox.addEventListener("click", closeLightbox);
lightboxImg.addEventListener("click", (e) => e.stopPropagation());
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !lightbox.hidden) closeLightbox();
});

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.addEventListener("open", () => {
    setConn("online", "已连接 · 实时推送");
    clearInterval(pingTimer);
    pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 20000);
  });

  ws.addEventListener("close", () => {
    setConn("offline", "已断开 · 重连中…");
    clearInterval(pingTimer);
    setTimeout(connectWs, 1500);
  });

  ws.addEventListener("error", () => {
    setConn("offline", "连接异常");
  });

  ws.addEventListener("message", (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    handleEvent(msg.event, msg.data || {});
  });
}

function formatPrice(p) {
  if (p == null || p === "") return null;
  const n = Number(p);
  if (Number.isNaN(n)) return String(p);
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2);
}

function formatElapsed(ms) {
  if (ms == null || ms === "") return "—";
  const n = Number(ms);
  if (!Number.isFinite(n)) return "—";
  const s = n / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  return `${Math.round(s)} s`;
}

function formatCount(n) {
  if (n == null) return "未采集";
  const v = Number(n);
  if (!Number.isFinite(v) || v < 0) return "未采集";
  return String(v);
}

function getRules() {
  const minProfit = Number(minProfitInput?.value);
  const minSales7d = Number(minSales7dInput?.value);
  return {
    minProfit: Number.isFinite(minProfit) ? minProfit : 30,
    minSales7d: Number.isFinite(minSales7d) ? minSales7d : 10,
  };
}

function getCost() {
  const raw = costInputGlobal?.value?.trim();
  if (!raw) return NaN;
  const cost = Number(raw);
  return Number.isFinite(cost) && cost >= 0 ? cost : NaN;
}

function handleEvent(event, data) {
  if (event === "hello") {
    addFeed("progress", "服务", data.message || "hello");
    return;
  }
  if (event === "job_started") {
    activeJobId = data.job_id;
    setBusy(true, data.query_type);
    beginTask(data.query);
    addFeed(
      "progress",
      "任务开始",
      `${data.query_type === "image" ? "图片" : "货号"}：${data.query}`
    );
    return;
  }
  if (event === "progress") {
    addFeed("progress", data.stage || "进度", data.message || "");
    if (busy) {
      taskDetail.textContent = data.message || "";
      if (data.stage === "sales" || data.stage === "huozhi_search") taskTitle.textContent = "正在采集销量";
    }
    return;
  }
  if (event === "price_result") {
    if (completedJobs.has(data.job_id)) return;
    addFeed("result-ok", "收入已采集", `${data.query} · 正在继续查询商家销量`);
    prependResult({ ...data, pending_sales: true });
    updateTask("running", "收入已就绪，继续查销量", data.query);
    return;
  }
  if (event === "result") {
    if (data.job_id && completedJobs.has(data.job_id)) return;
    if (data.job_id) {
      completedJobs.add(data.job_id);
      if (completedJobs.size > 200) completedJobs.delete(completedJobs.values().next().value);
    }
    if (!activeJobId || activeJobId === data.job_id) {
      activeJobId = null;
      setBusy(false);
    }
    const ok = !!data.ok;
    const cancelled = !ok && /终止/.test(data.message || "");
    if (cancelled) {
      addFeed("result-fail", "已终止", data.message || "用户已终止查询");
      finishTask(data);
      return;
    }
    const sell = formatPrice(data.sell_price);
    const low = formatPrice(data.lowest_price);
    const sizeCount = Array.isArray(data.size_prices) ? data.size_prices.length : 0;
    const salesCount = Object.keys(data.sales_by_size || {}).length;
    const summary = data.ok
      ? `${data.query} → 售价¥${sell ?? "—"} / 最低预计¥${low ?? "—"} / ${sizeCount}码 / 销量${salesCount}码`
      : data.message || "未知错误";
    addFeed(ok ? "result-ok" : "result-fail", ok ? "查价成功" : "查价失败", summary);
    prependResult(data);
    finishTask(data);
  }
}

function salesOf(data, size) {
  if (data.sales_source !== "merchant_7d") return { d1: null, d3: null, d7: null };
  const map = data.sales_by_size || {};
  const row = map[String(size)] || map[Number(size)] || {};
  const count = (value) => {
    const number = nonNegativeNumber(value);
    return Number.isInteger(number) ? number : null;
  };
  return {
    d1: count(row.sales_1d),
    d3: count(row.sales_3d),
    d7: count(row.sales_7d),
  };
}

function nonNegativeNumber(value) {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function buildDecisionRows(data, cost) {
  const rules = getRules();
  const sizes = Array.isArray(data.size_prices) ? data.size_prices : [];
  const hasCost = Number.isFinite(cost) && cost >= 0;
  return sizes.map((row) => {
    const income = nonNegativeNumber(row.price);
    const profit =
      income != null && hasCost ? Math.round((income - cost) * 100) / 100 : null;
    const sales = salesOf(data, row.size);
    let state, label, reason;
    if (!hasCost) {
      state = "cost_missing";
      label = "待填成本";
      reason = "请先输入成本，才能计算利润和购买建议";
    } else if (income == null) {
      state = "income_missing";
      label = "收入缺失";
      reason = "未采集到有效预计收入，无法计算利润";
    } else if (data.pending_sales) {
      state = "pending";
      label = "查询中";
      reason = "商家销量仍在查询，暂不生成购买建议";
    } else if (sales.d7 == null) {
      state = "sales_missing";
      label = "待核实";
      reason = "销量未采集";
    } else if (profit < rules.minProfit) {
      state = "profit_low";
      label = "观望";
      reason = `利润 ¥${formatPrice(profit)}，低于最低利润 ¥${formatPrice(rules.minProfit)}`;
    } else if (sales.d7 < rules.minSales7d) {
      state = "sales_low";
      label = "观望";
      reason = `7日销量 ${sales.d7}，低于最低销量 ${rules.minSales7d}`;
    } else {
      state = "buy";
      label = "建议买";
      reason = `利润 ¥${formatPrice(profit)} ≥ ¥${formatPrice(rules.minProfit)}，7日销量 ${sales.d7} ≥ ${rules.minSales7d}`;
    }
    return {
      size: row.size,
      income,
      raw: row.raw,
      profit,
      sales,
      buy: state === "buy",
      state,
      label,
      reason,
    };
  });
}

function renderDecision(data, cost, pageState = { page: 0 }) {
  const rows = buildDecisionRows(data, cost);
  const rules = getRules();
  const sizeCount = Array.isArray(data.size_prices) ? data.size_prices.length : 0;

  if (sizeCount === 0) {
    const why = escapeHtml(data.message || "未能采集各码预计收入");
    return `<div class="advise-banner skip">${why}</div>
      <div class="table-wrap">
        <table class="decision-table">
          <thead>
            <tr>
              <th class="col-size">尺码</th>
              <th>预计收入</th>
              <th class="col-profit">利润</th>
              <th>7日销量</th>
              <th class="col-advise">建议</th>
            </tr>
          </thead>
          <tbody><tr><td colspan="5" class="empty-row">无尺码数据，请重试查价</td></tr></tbody>
        </table>
      </div>`;
  }

  // 只展示利润达标的尺码
  const qualified = rows.filter(
    (r) => r.profit != null && r.profit >= rules.minProfit
  );
  const buys = qualified.filter((r) => r.buy);
  const unverified = qualified.filter((r) => r.state === "sales_missing").length;

  const summary = !Number.isFinite(cost)
    ? `<div class="advise-banner skip">请先在上方输入成本价（共 ${sizeCount} 个尺码已采集）</div>`
    : qualified.length
      ? data.pending_sales
        ? `<div class="advise-banner skip">利润达标 ${qualified.length} 个尺码 · 等待商家销量，暂不生成建议</div>`
        : `<div class="advise-banner ${buys.length ? "buy" : "skip"}">利润达标 ${qualified.length} 个尺码 · ${
          buys.length ? `建议买 ${buys.length} 个` : unverified === qualified.length ? "销量缺失，暂不能判断" : "暂无同时满足销量阈值的建议"
        }${unverified ? ` · ${unverified} 个销量待核实` : ""}</div>`
      : `<div class="advise-banner skip">暂无利润≥${rules.minProfit} 的尺码（已采集 ${sizeCount} 码）</div>`;
  const status = data.pending_sales
    ? `<div class="advise-banner skip">收入已采集 · 商家销量查询中</div>`
    : (data.warnings || []).map((warning) => `<div class="advise-banner skip">${escapeHtml(warning)}</div>`).join("");

  const makeTable = (list) => {
    const body = list
      .map((r) => {
        const income =
          r.income != null ? `¥${formatPrice(r.income)}` : r.raw || "¥--";
        const profitText = r.profit == null ? "—" : `¥${formatPrice(r.profit)}`;
        const cls = r.buy ? "row-buy" : "";
        return `<tr class="${cls}">
        <td class="col-size num">${escapeHtml(String(r.size))}</td>
        <td class="num col-income">${escapeHtml(income)}</td>
        <td class="col-profit num ${r.profit >= 0 ? "pos" : "neg"}">${escapeHtml(
          profitText
        )}</td>
        <td class="num col-sales">${escapeHtml(formatCount(r.sales.d7))}</td>
        <td class="col-advise"><span class="tag ${r.buy ? "tag-buy" : "tag-skip"}" title="${escapeHtml(r.reason)}" aria-label="${escapeHtml(`${r.label}：${r.reason}`)}">${escapeHtml(r.label)}</span>${
          r.state === "sales_low" || r.state === "profit_low"
            ? `<small class="advice-reason">${r.state === "sales_low" ? "销量不足" : "利润不足"}</small>`
            : ""
        }</td>
      </tr>`;
      })
      .join("");
    return `<div class="table-wrap">
      <table class="decision-table">
        <thead>
          <tr>
            <th class="col-size">尺码</th>
            <th>预计收入</th>
            <th class="col-profit">利润</th>
            <th>7日销量</th>
            <th class="col-advise">建议</th>
          </tr>
        </thead>
        <tbody>${
          body ||
          `<tr><td colspan="5" class="empty-row">${
            Number.isFinite(cost) ? "无达标尺码" : "输入成本后显示"
          }</td></tr>`
        }</tbody>
      </table>
    </div>`;
  };

  const visibleRows = Number.isFinite(cost) ? qualified : rows;
  const pages = Math.max(1, Math.ceil(visibleRows.length / ROWS_PER_PAGE));
  pageState.page = Math.max(0, Math.min(pageState.page, pages - 1));
  const start = pageState.page * ROWS_PER_PAGE;
  const tablesHtml = makeTable(visibleRows.slice(start, start + ROWS_PER_PAGE));
  const pager = pages > 1 ? `<nav class="size-pager" aria-label="尺码分页">
    <span>${start + 1}–${Math.min(start + ROWS_PER_PAGE, visibleRows.length)} / ${visibleRows.length} 个尺码</span>
    <div class="pager-controls">
      <button type="button" class="icon-btn" data-size-prev aria-label="上一页尺码" title="上一页尺码" ${pageState.page === 0 ? "disabled" : ""}>${icon("chevron-left")}</button>
      <span class="page-position">${pageState.page + 1} / ${pages}</span>
      <button type="button" class="icon-btn" data-size-next aria-label="下一页尺码" title="下一页尺码" ${pageState.page === pages - 1 ? "disabled" : ""}>${icon("chevron-right")}</button>
    </div>
  </nav>` : "";
  return `${status}${summary}<div class="decision-page${pages > 1 ? " is-paged" : ""}">${tablesHtml}</div>${pager}`;
}

function prependResult(data) {
  const id = rememberResult(data);
  if (resultsEl.classList.contains("empty-state")) {
    resultsEl.classList.remove("empty-state");
    resultsEl.innerHTML = "";
  }

  // 成本由用户填写真实进货价，不要用详情售价预填（否则利润≈0）
  const card = document.createElement("article");
  card.className = "card";
  card.id = `result-${id}`;
  card.setAttribute("role", "tabpanel");
  card.setAttribute("aria-labelledby", `history-${id}`);
  card.dataset.result = "1";
  cardDataMap.set(card, data);

  const sell = formatPrice(data.sell_price);
  const low = formatPrice(data.lowest_price);
  const sellText = sell != null ? `¥${sell}` : "—";
  const lowText = low != null ? `¥${low}` : "—";

  function shotBlock(url, label) {
    if (!url) return "";
    const src = `/screenshots/${encodeURIComponent(url)}`;
    return `<div class="shot-block">
      <div class="shot-label">${escapeHtml(label)}</div>
      <div class="shot-wrap"><img class="shot" src="${src}" alt="${escapeHtml(
      label
    )}" title="单击放大" /></div>
    </div>`;
  }

  const shots = [
    [data.screenshot, "尺码网格"],
    [data.sales_screenshot, "最近购买"],
    [data.merchant_screenshot, "商家版销量"],
  ].filter(([url]) => url);
  card.innerHTML = `
    <div class="result-content">
    <div class="body">
      ${data.product_image ? `<button type="button" class="product-preview" data-product-image aria-label="放大商品主图" title="放大商品主图">
        <img src="/screenshots/${encodeURIComponent(data.product_image)}" alt="商品主图" />
        <span class="product-zoom" aria-hidden="true">${icon("zoom-in")}</span>
      </button>` : ""}
      <div class="price">${sellText}</div>
      <div class="price-meta">详情当前售价 · 各码最低预计收入 ${lowText}</div>
      <div class="title">${escapeHtml(data.title || data.query || "未命名商品")}</div>
      <div class="sub">
        货号 ${escapeHtml(data.article_number || data.query || "—")}
        · 耗时 ${formatElapsed(data.elapsed_ms)}
      </div>
      <div class="decision-host"></div>
    </div>
    ${shots.length ? `<aside class="evidence">
      <div class="evidence-tabs" role="tablist" aria-label="查询截图">
        ${shots.map(([, label], index) => `<button type="button" role="tab" aria-selected="${index === 0}" tabindex="${index === 0 ? 0 : -1}" data-shot-tab="${index}" aria-controls="shot-${id}-${index}" id="shot-tab-${id}-${index}">${escapeHtml(label)}</button>`).join("")}
      </div>
      <div class="shots">${shots.map(([url, label], index) => `<div role="tabpanel" aria-labelledby="shot-tab-${id}-${index}" id="shot-${id}-${index}" ${index ? "hidden" : ""}>${shotBlock(url, label)}</div>`).join("")}</div>
    </aside>` : ""}
    </div>
  `;

  const host = card.querySelector(".decision-host");
  const previous = resultCards.get(id);
  const pageState = previous?._pageState || { page: 0 };
  card._pageState = pageState;
  function refresh() {
    host.innerHTML = renderDecision(data, getCost(), pageState);
    host.querySelector("[data-size-prev]")?.addEventListener("click", () => {
      pageState.page--;
      refresh();
      host.querySelector("[data-size-prev]")?.focus({ preventScroll: true });
    });
    host.querySelector("[data-size-next]")?.addEventListener("click", () => {
      pageState.page++;
      refresh();
      host.querySelector("[data-size-next]")?.focus({ preventScroll: true });
    });
    icons();
  }
  card._refreshDecision = refresh;
  refresh();

  const productPreview = card.querySelector("[data-product-image]");
  const productImg = productPreview?.querySelector("img");
  productPreview?.addEventListener("click", () => openLightbox(productImg.src));
  productImg?.addEventListener("error", () => { productPreview.hidden = true; });

  card.querySelectorAll("img.shot").forEach((img) => {
    img.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openLightbox(img.src);
    });
  });
  const shotPanels = card.querySelectorAll(".shots > [role=tabpanel]");
  const shotTabs = card.querySelectorAll("[data-shot-tab]");
  function selectShot(selected) {
    card._shotIndex = selected;
    shotTabs.forEach((tab, index) => {
      tab.setAttribute("aria-selected", String(index === selected));
      tab.setAttribute("tabindex", index === selected ? "0" : "-1");
    });
    shotPanels.forEach((panel, index) => { panel.hidden = index !== selected; });
  }
  selectShot(Math.min(previous?._shotIndex || 0, Math.max(0, shots.length - 1)));
  shotTabs.forEach(button => {
    button.addEventListener("click", () => selectShot(Number(button.dataset.shotTab)));
    button.addEventListener("keydown", event => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const next = Math.max(0, Math.min(shots.length - 1, card._shotIndex + (event.key === "ArrowRight" ? 1 : -1)));
      selectShot(next);
      shotTabs[next]?.focus({ preventScroll: true });
    });
  });

  if (previous?.isConnected) previous.replaceWith(card);
  else resultsEl.prepend(card);
  resultCards.set(id, card);
  selectResult(selectedResultId);
  icons();
}

function refreshAllDecisions() {
  resultsEl.querySelectorAll(".card[data-result]").forEach((card) => {
    if (typeof card._refreshDecision === "function") card._refreshDecision();
  });
}

minProfitInput?.addEventListener("change", refreshAllDecisions);
minSales7dInput?.addEventListener("change", refreshAllDecisions);
costInputGlobal?.addEventListener("change", refreshAllDecisions);
costInputGlobal?.addEventListener("input", refreshAllDecisions);

skuForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (busy) return;
  const sku = document.getElementById("skuInput").value.trim();
  if (!sku) return;
  setBusy(true, "sku");
  prepareSound();
  beginTask(sku);
  addFeed("progress", "本地", `提交货号 ${sku}`);
  try {
    const res = await fetch("/api/query/sku", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sku }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    handleEvent("result", data);
  } catch (err) {
    addFeed("result-fail", "请求失败", String(err));
    finishTask({ ok: false, query: sku, message: String(err), elapsed_ms: Date.now() - taskStartedAt });
  } finally {
    setBusy(false);
  }
});

function revokePreview() {
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
}

function updateFile(file, { autoSubmit = false } = {}) {
  selectedFile = file || null;
  revokePreview();

  if (selectedFile) {
    previewUrl = URL.createObjectURL(selectedFile);
    pasteThumb.src = previewUrl;
    pastePreview.hidden = false;
    pasteCopy.querySelector("strong").textContent = "已选图片";
    const shortName =
      selectedFile.name.length > 28
        ? selectedFile.name.slice(0, 28) + "…"
        : selectedFile.name;
    pasteCopy.querySelector("span").textContent =
      shortName + " · 点右上角「清除」可换图";
    pasteZone.classList.add("has-file");
  } else {
    pasteThumb.removeAttribute("src");
    pastePreview.hidden = true;
    pasteCopy.querySelector("strong").textContent = "识图查询";
    pasteCopy.querySelector("span").textContent =
      "Ctrl+V 粘贴 / 拖入 / 点击选图 · 选图后可点「清除」";
    pasteZone.classList.remove("has-file");
    if (imageInput) imageInput.value = "";
  }

  imageBtn.disabled = busy || !selectedFile;

  if (autoSubmit && selectedFile && !busy) {
    submitImageQuery();
  }
}

function fileFromClipboard(items) {
  if (!items) return null;
  for (const item of items) {
    if (item.type && item.type.startsWith("image/")) {
      const blob = item.getAsFile?.();
      if (blob instanceof Blob) {
        const ext = (blob.type.split("/")[1] || "png").replace("jpeg", "jpg");
        return new File([blob], `paste-${Date.now()}.${ext}`, {
          type: blob.type || "image/png",
        });
      }
    }
  }
  return null;
}

async function submitImageQuery() {
  if (busy || !selectedFile) return;
  setBusy(true, "image");
  prepareSound();
  beginTask(selectedFile.name);
  addFeed("progress", "本地", `识图上传 ${selectedFile.name}`);
  try {
    const fd = new FormData();
    fd.append("file", selectedFile);
    const res = await fetch("/api/query/image", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || `HTTP ${res.status}`);
    handleEvent("result", data);
  } catch (err) {
    addFeed("result-fail", "请求失败", String(err));
    finishTask({ ok: false, query: selectedFile.name, message: String(err), elapsed_ms: Date.now() - taskStartedAt });
  } finally {
    setBusy(false);
  }
}

imageInput.addEventListener("change", () => {
  updateFile(imageInput.files?.[0] || null);
});

pasteZone.addEventListener("click", (e) => {
  if (e.target.closest(".paste-clear")) return;
  imageInput.click();
});

pasteClear.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  updateFile(null);
});

pasteZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  pasteZone.classList.add("dragover");
});

pasteZone.addEventListener("dragleave", () => {
  pasteZone.classList.remove("dragover");
});

pasteZone.addEventListener("drop", (e) => {
  e.preventDefault();
  pasteZone.classList.remove("dragover");
  const file = e.dataTransfer?.files?.[0];
  if (file && file.type.startsWith("image/")) {
    updateFile(file, { autoSubmit: true });
  } else {
    addFeed("result-fail", "识图", "请拖入图片文件");
  }
});

document.addEventListener("paste", (e) => {
  const file = fileFromClipboard(e.clipboardData?.items);
  if (!file) return;
  e.preventDefault();
  updateFile(file, { autoSubmit: true });
  addFeed("progress", "本地", "已粘贴图片，开始识图查价");
});

imageBtn.addEventListener("click", () => submitImageQuery());

cancelBtn?.addEventListener("click", async () => {
  if (!busy) return;
  cancelBtn.disabled = true;
  addFeed("progress", "本地", "正在终止查询…");
  try {
    const res = await fetch("/api/cancel", { method: "POST" });
    const data = await res.json();
    addFeed(
      "progress",
      "终止",
      data.was_running ? "已发送终止指令，稍后停止" : "当前没有进行中的查询"
    );
    if (!data.was_running) setBusy(false);
  } catch (err) {
    addFeed("result-fail", "终止失败", String(err));
    cancelBtn.disabled = false;
  }
});

clearBtn.addEventListener("click", () => {
  feedEl.innerHTML = "";
});

clearResultsBtn?.addEventListener("click", () => {
  resultCards.clear();
  resultHistory = [];
  selectedResultId = null;
  resultsEl.classList.add("empty-state");
  resultsEl.innerHTML = "<p>查询后结果会显示在这里</p>";
  renderHistoryNav();
});

resultPrev.addEventListener("click", () => {
  const index = resultHistory.findIndex(entry => entry.id === selectedResultId);
  if (index > 0) selectResult(resultHistory[index - 1].id);
});
resultNext.addEventListener("click", () => {
  const index = resultHistory.findIndex(entry => entry.id === selectedResultId);
  if (index < resultHistory.length - 1) selectResult(resultHistory[index + 1].id);
});
historyTabs.addEventListener("keydown", event => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || !resultHistory.length) return;
  event.preventDefault();
  const current = resultHistory.findIndex(entry => entry.id === selectedResultId);
  const next = event.key === "Home" ? 0 : event.key === "End" ? resultHistory.length - 1
    : Math.max(0, Math.min(resultHistory.length - 1, current + (event.key === "ArrowRight" ? 1 : -1)));
  selectResult(resultHistory[next].id);
  historyTabs.querySelector('[aria-selected="true"]')?.focus();
});
soundToggle.addEventListener("click", () => {
  soundEnabled = !soundEnabled;
  try { localStorage.setItem("dewu-completion-sound", String(soundEnabled)); } catch {}
  prepareSound();
  updateSoundToggle();
  if (soundEnabled) playCompletionSound();
});
document.getElementById("toastClose").addEventListener("click", () => { completionToast.hidden = true; });
document.getElementById("toastView").addEventListener("click", () => {
  selectResult(toastResultId);
  document.querySelector(".results").scrollIntoView({ behavior: "smooth", block: "start" });
  completionToast.hidden = true;
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !busy) {
    document.title = "得物决策助手";
    if (!completionToast.hidden) {
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { completionToast.hidden = true; }, 10000);
    }
  }
});
renderHistoryNav();
updateSoundToggle();
connectWs();
