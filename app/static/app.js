const $ = (sel) => document.querySelector(sel);
let charsCache = [];
let refreshingAll = false;
let marketUnit = null; // 1 个提取器的利润(ISK),来自市场行情
const AUTO_REFRESH_MS = 5 * 60 * 1000;  // 角色数据：页面开着时每 5 分钟自动刷新
const MARKET_REFRESH_MS = 15 * 60 * 1000; // 市场行情：每 15 分钟自动刷新

function fmtNum(n) {
  if (n == null) return "—";
  return Number(n).toLocaleString("zh-CN");
}
function fmtIsk(n) {
  if (n == null) return "—";
  return Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
function fmtBig(n) {
  if (n == null) return "—";
  const v = Number(n);
  const abs = Math.abs(v);
  if (abs >= 1e12) return (v / 1e12).toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + "T";
  if (abs >= 1e9) return (v / 1e9).toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + "B";
  if (abs >= 1e6) return (v / 1e6).toLocaleString("zh-CN", { maximumFractionDigits: 1 }) + "M";
  if (abs >= 1e3) return (v / 1e3).toLocaleString("zh-CN", { maximumFractionDigits: 1 }) + "K";
  return v.toLocaleString("zh-CN");
}
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function pct(p) { return p == null ? null : Math.round(p * 100); }

function extractLine(c) {
  if (c.total_sp == null) return "";
  const tip = "已训练技能点(不含未分配)超过 550 万的部分，每 50 万 SP 可提取 1 个（未分配不参与提取/售卖）；利润 = 单利 × 可提个数，单利 = 大型技能注入器中间价 − 技能提取器中间价";
  if (!c.extractor_count) {
    const remain = c.extractable_sp || 0;
    return `<div class="extract" title="${tip}">可提 <b>0</b> 个（超出 550 万部分仅 ${fmtNum(remain)} SP，不足 50 万）</div>`;
  }
  const totalProfit = marketUnit ? c.extractor_count * marketUnit : null;
  const profitTxt = totalProfit ? ` · 利润≈${fmtBig(totalProfit)}` : "";
  return `<div class="extract" title="${tip}">可提 <b>${c.extractor_count}</b> 个${profitTxt}</div>`;
}

function extractDetail(c) {
  if (c.total_sp == null) return "";
  let h;
  if (!c.extractor_count) {
    h = `<div class="sub extract-sub">🧪 可提取 0 个（超出 550 万部分约 ${fmtNum(c.extractable_sp)} SP，不足 50 万，暂无法提取）</div>`;
    return h;
  }
  h = `<div class="sub extract-sub">🧪 可提取 ${c.extractor_count} 个技能提取器（约 ${fmtNum(c.extractable_sp)} SP）</div>`;
  if (marketUnit) {
    h += `<div class="sub extract-sub">💰 预计利润 ≈ ${fmtIsk(c.extractor_count * marketUnit)} ISK（单利 ≈ ${fmtBig(marketUnit)}）</div>`;
  }
  return h;
}
function walletLine(c) {
  if (c.wallet_isk == null) return "";
  return `<div class="wallet" title="个人钱包余额">钱包 <b>${fmtIsk(c.wallet_isk)}</b> ISK</div>`;
}
function cloneBadge(c) {
  if (c.clone_status === "omega") {
    return '<span class="clone-badge omega" title="根据技能数据推断：未发现被 Alpha 压制的技能">Ω Omega(推定)</span>';
  }
  if (c.clone_status === "alpha") {
    return '<span class="clone-badge alpha" title="检测到被 Alpha 限制压制的技能（trained > active）">Alpha(确定)</span>';
  }
  return "";
}

function cloneChip(c) {
  if (c.clone_status === "omega") return '<span class="clone-chip omega">Omega(推定)</span>';
  if (c.clone_status === "alpha") return '<span class="clone-chip alpha">Alpha(确定)</span>';
  return "";
}
function speedLine(c) {
  if (!c.training_speed) return "";
  return `<div class="speed" title="当前训练速度（根据技能队列推算，含 Alpha 减速/脑插加成）">训练 ${fmtNum(c.training_speed)} SP/小时</div>`;
}
/* ---------- 首页角色磁贴 ---------- */
function tileHtml(c) {
  const warn = c.needs_reauth
    ? '<span class="warn-badge">⚠ 需重新授权</span>'
    : "";
  let hint = "";
  const q0 = (c.queue || [])[0];
  if (!c.needs_reauth && c.total_sp != null) {
    if (q0 && isActiveTraining(c)) {
      const to = q0.target_level != null ? `Lv${q0.target_level}` : "Lv?";
      hint = `<div class="train-hint" title="${escapeHtml(q0.name)}">训练中 <b>${escapeHtml(q0.name)}</b> → ${to}</div>`;
    } else if (q0) {
      hint = `<div class="train-hint paused" title="队列中有技能但未在训练（可能 Omega 过期或排队受限制）">⏸ 队列暂停</div>`;
    } else {
      hint = `<div class="train-hint">未在训练</div>`;
    }
  }
  const unalloc = c.unallocated_sp != null
    ? `<div class="unalloc">未分配 <b>${fmtNum(c.unallocated_sp)}</b> SP</div>`
    : "";
  return `
  <div class="tile${c.needs_reauth ? " warned" : ""}" data-id="${c.character_id}" tabindex="0" role="button" aria-label="查看 ${escapeHtml(c.character_name)} 详情">
    ${warn}
    ${cloneBadge(c)}
    <img class="avatar" src="${escapeHtml(c.portrait_url)}" alt="" loading="lazy">
    <div class="name">${nameMaskSpan(c.character_name)}</div>
    <span class="sp-num" title="${totalSpTip(c)}">${totalSpDisplay(c)}</span>
    <span class="sp-cap">技 能 点</span>
    ${walletLine(c)}
    ${unalloc}
    ${extractLine(c)}
    ${speedLine(c)}
    ${hint}
    <div class="updated">更新于 ${fmtTime(c.last_updated)}</div>
  </div>`;
}

/* ---------- 详情弹窗 ---------- */
function fmtRemain(iso) {
  if (!iso) return "";
  const diff = new Date(iso).getTime() - Date.now();
  if (!Number.isFinite(diff) || diff <= 0) return "";
  const h = Math.floor(diff / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  if (h >= 48) {
    const d = Math.floor(h / 24);
    return `剩余约 ${d} 天 ${h % 24} 小时`;
  }
  if (h >= 1) return `剩余约 ${h} 小时 ${m} 分`;
  return `剩余约 ${m} 分钟`;
}

function queueHtml(queue) {
  if (!queue || !queue.length) {
    return '<div class="queue-item none">未在训练，队列为空</div>';
  }
  return queue.map((q, i) => {
    const from = q.current_level != null ? `Lv${q.current_level}` : "Lv0";
    const to = q.target_level != null ? `Lv${q.target_level}` : "Lv?";
    const active0 = i === 0 && !!q.finish_date && !!q.training_start_date;
    let livePct = null;
    let remain = "";
    if (active0) {
      const st = new Date(q.training_start_date).getTime();
      const fn = new Date(q.finish_date).getTime();
      if (Number.isFinite(st) && Number.isFinite(fn) && fn > st) {
        const f = Math.min(1, Math.max(0, (Date.now() - st) / (fn - st)));
        livePct = Math.round(f * 100);
      }
      remain = fmtRemain(q.finish_date);
    }
    const barPct = livePct != null ? livePct : (q.progress != null ? pct(q.progress) : null);
    const bar = barPct != null
      ? `<div class="progress"><div class="progress-inner" style="width:${barPct}%"></div></div>`
      : "";
    const finish = q.finish_date
      ? `<span class="sub">${fmtTime(q.finish_date)}${remain ? ` · ${remain}` : ""}</span>` : "";
    let label;
    if (i === 0) {
      label = active0 ? "训练中(实时)" : "⏸ 暂停";
    } else {
      label = `队列 ${i}`;
    }
    const progText = barPct != null ? `${barPct}%` : "—";
    return `
      <div class="queue-item${i === 0 ? " training" : ""}">
        <div class="queue-title">
          <span class="tag">${label}</span>
          <b>${escapeHtml(q.name)}</b>
          <span class="levels">${from} → ${to}</span>
        </div>
        ${bar}
        <div class="queue-meta">
          <span class="sub">进度 ${progText}${livePct != null ? "（实时推算）" : ""}</span>
          ${finish}
        </div>
      </div>`;
  }).join("");
}
function detailHtml(c) {
  let body;
  if (c.needs_reauth) {
    body = `<div class="reauth">⚠ ${escapeHtml(c.auth_error || "授权已失效")}
              <a class="btn small" href="/login">重新授权</a></div>`;
  } else {
    const speedTxt = c.training_speed
      ? `<div class="sub speed-sub">⚡ 训练速度 ${fmtNum(c.training_speed)} SP/小时</div>` : "";
    body = `<div class="sp-row"><span class="sp-num" title="${totalSpTip(c)}">${totalSpDisplay(c)}</span>
              <span class="sp-label">技能点</span></div>
            <div class="sub">已训练 ${fmtNum(c.total_sp)} · 未分配 ${fmtNum(c.unallocated_sp)}</div>
            ${c.wallet_isk != null ? `<div class="sub wallet-sub">💳 钱包余额 ${fmtIsk(c.wallet_isk)} ISK</div>` : ""}
            ${extractDetail(c)}
            ${speedTxt}`;
  }
  return `
    <div class="detail-head">
      <img class="avatar" src="${escapeHtml(c.portrait_url)}" alt="头像">
      <div class="who">
        <div class="name">${nameMaskSpan(c.character_name)} ${cloneChip(c)}</div>
        <div class="sub">更新于 ${fmtTime(c.last_updated)}</div>
        <div class="acct-edit">
          <input id="acct-input" maxlength="30" value="${escapeHtml(c.account_name || "")}"
                 placeholder="所属账号/分组名，如：主号">
          <button class="btn small" id="acct-save">保存</button>
        </div>
      </div>
      <div class="actions">
        <button class="btn small refresh" data-id="${c.character_id}">刷新</button>
        <button class="btn small danger remove" data-id="${c.character_id}">移除</button>
      </div>
    </div>
    <div class="detail-body">
      ${body}
      <h3>技能队列</h3>
      <div class="queue">${queueHtml(c.queue)}</div>
    </div>`;
}

/* ---------- 弹窗开关 ---------- */
function openDetail(id) {
  const c = charsCache.find((x) => x.character_id === id);
  if (!c) return;
  const modal = $("#modal");
  const body = $("#modal-body");
  body.dataset.openId = String(id);
  body.innerHTML = detailHtml(c);
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  bindModalActions();
  bindMaskables(body);
  modal.querySelector(".modal-panel").scrollTop = 0;
}

function closeDetail() {
  $("#modal").classList.add("hidden");
  document.body.style.overflow = "";
}

function bindModalActions() {
  const refreshBtn = $("#modal-body").querySelector(".refresh");
  const removeBtn = $("#modal-body").querySelector(".remove");
  const saveAcctBtn = $("#modal-body").querySelector("#acct-save");
  if (refreshBtn) refreshBtn.addEventListener("click", onRefresh);
  if (removeBtn) removeBtn.addEventListener("click", onRemove);
  if (saveAcctBtn) saveAcctBtn.addEventListener("click", onSaveAccount);
}

async function onSaveAccount() {
  const body = $("#modal-body");
  const id = Number(body.dataset.openId);
  if (!id) return;
  const input = body.querySelector("#acct-input");
  const name = (input ? input.value : "").trim();
  try {
    const res = await fetch(`/api/characters/${id}/account`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_name: name }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const c = await res.json();
    const idx = charsCache.findIndex((x) => x.character_id === id);
    if (idx >= 0) charsCache[idx] = c; else charsCache.push(c);
    render(charsCache);
    showBanner(name ? `已将「${c.character_name}」归入「${name}」` : "已清除该角色的账号分组", false);
  } catch (e) {
    showBanner("保存账号分组失败：" + e.message, true);
  }
}

/* ---------- 消息条 ---------- */
function showBanner(msg, isError) {
  const b = $("#banner");
  if (!msg) { b.classList.add("hidden"); return; }
  b.textContent = msg;
  b.classList.toggle("error", !!isError);
  b.classList.remove("hidden");
}
function showStatus(msg) {
  const s = $("#status");
  if (!msg) { s.classList.add("hidden"); return; }
  s.textContent = msg;
  s.classList.remove("hidden");
}

/* ---------- 数据加载与渲染 ---------- */
async function load() {
  showStatus("正在加载…");
  try {
    const res = await fetch("/api/characters", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const chars = await res.json();
    render(chars);
    showStatus("");
  } catch (e) {
    showStatus("加载失败：" + e.message + "，请确认服务已启动。");
  }
}

function groupSections(chars) {
  const map = new Map();
  for (const c of chars) {
    const key = (c.account_name || "").trim();
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(c);
  }
  for (const list of map.values()) {
    list.sort((a, b) => (a.character_name || "").localeCompare(b.character_name || "", "zh"));
  }
  const sections = [];
  const named = [...map.keys()].filter((k) => k).sort((a, b) => a.localeCompare(b, "zh"));
  for (const name of named) sections.push({ key: name, label: name, chars: map.get(name) });
  const unknown = map.get("") || [];
  if (unknown.length) sections.push({ key: "", label: "未设置账号", chars: unknown });
  return sections;
}

let viewMode = "list";

function initViewMode() {
  try {
    const v = localStorage.getItem("viewMode");
    if (v === "list" || v === "cards") viewMode = v;
  } catch (e) { /* ignore */ }
}

function applyViewButtons() {
  const lb = document.getElementById("btn-view-list");
  const cb = document.getElementById("btn-view-cards");
  if (lb) lb.classList.toggle("active", viewMode === "list");
  if (cb) cb.classList.toggle("active", viewMode === "cards");
}

function setView(mode) {
  viewMode = mode;
  try { localStorage.setItem("viewMode", mode); } catch (e) { /* ignore */ }
  applyViewButtons();
  if (charsCache.length) render(charsCache);
}

let hideNames = false;
let showExtractEvents = true;
let lastHistoryRows = [];
let lastHistoryEvents = [];

function initHideNames() {
  try { hideNames = localStorage.getItem("hideNames") === "1"; } catch (e) { /* ignore */ }
}

function initShowEvents() {
  try {
    const v = localStorage.getItem("showExtractEvents");
    showExtractEvents = v === null ? true : v === "1";
  } catch (e) { /* ignore */ }
  const el = document.getElementById("opt-show-events");
  if (el) el.checked = showExtractEvents;
}

function nameMaskSpan(real) {
  const esc = escapeHtml(real);
  if (!hideNames) return esc;
  return `<span class="maskable" data-real="${esc}">${maskedAccount(real)}</span>`;
}

function toggleHideNames(checked) {
  hideNames = !!checked;
  try { localStorage.setItem("hideNames", hideNames ? "1" : "0"); } catch (e) { /* ignore */ }
  if (charsCache.length) render(charsCache); else load();
}
function isActiveTraining(c) {
  const q0 = (c.queue || [])[0];
  return !!(q0 && q0.training_start_date && q0.finish_date);
}
function projectedTotal(c) {
  if (c.total_sp == null) return null;
  const q0 = (c.queue || [])[0];
  if (!q0 || q0.start_sp == null || q0.end_sp == null || q0.current_sp == null ||
      !q0.training_start_date || !q0.finish_date) return null;
  const st = new Date(q0.training_start_date).getTime();
  const fn = new Date(q0.finish_date).getTime();
  if (!Number.isFinite(st) || !Number.isFinite(fn) || fn <= st) return null;
  let frac = (Date.now() - st) / (fn - st);
  if (frac < 0) return null;
  frac = Math.min(1, frac);
  const projSkill = q0.start_sp + (q0.end_sp - q0.start_sp) * frac;
  const gained = Math.max(0, projSkill - q0.current_sp);
  return c.total_sp + gained;
}

function combinedTotal(c) {
  if (c.total_sp == null) return null;
  return c.total_sp + (c.unallocated_sp || 0);
}

function projectedCombined(c) {
  const p = projectedTotal(c);
  if (p == null) return null;
  return p + (c.unallocated_sp || 0);
}

function totalSpDisplay(c) {
  const pc = projectedCombined(c);
  if (pc != null) return `≈ ${fmtNum(pc)}`;
  const ct = combinedTotal(c);
  return ct != null ? fmtNum(ct) : fmtNum(c.total_sp);
}

function totalSpTip(c) {
  const ct = combinedTotal(c);
  const free = c.unallocated_sp || 0;
  const trained = c.total_sp != null ? c.total_sp : 0;
  if (ct == null) return "总技能点（暂无数据）";
  const pc = projectedCombined(c);
  if (pc != null) {
    return `总技能点(含未分配)：CCP 结算 ${fmtNum(ct)} SP（已训练 ${fmtNum(trained)} + 未分配 ${fmtNum(free)}）；实时推算约 ${fmtNum(pc)} SP`;
  }
  return `总技能点(含未分配) = 已训练 ${fmtNum(trained)} + 未分配 ${fmtNum(free)} = ${fmtNum(ct)} SP`;
}

function projectTick() {
  if (refreshingAll) return;
  const modal = $("#modal");
  if (modal && !modal.classList.contains("hidden")) {
    const ae = document.activeElement;
    if (ae && ae.id === "acct-input") return; // 正在编辑账号名时不打扰
    const body = $("#modal-body");
    const id = Number(body.dataset.openId);
    const c = charsCache.find((x) => x.character_id === id);
    if (c) renderDetail(c); // 弹窗打开:刷新当前角色的实时进度
    return;
  }
  if (document.hidden || !charsCache.length) return;
  render(charsCache); // 弹窗关闭:整体刷新(实时推算/进度跳动)
}
function maskedAccount(real) {
  const n = Math.max(2, Math.min(String(real).length, 6));
  return "●".repeat(n);
}

function bindMaskables(scope) {
  if (!scope) return;
  scope.querySelectorAll(".maskable").forEach((el) => {
    if (el.__maskBound) return;
    el.__maskBound = true;
    const real = el.dataset.real || "";
    el.dataset.mask = el.textContent;
    el.addEventListener("mouseenter", () => { el.textContent = real; });
    el.addEventListener("mouseleave", () => { el.textContent = el.dataset.mask; });
  });
}
function renderCards(chars) {
  const sections = groupSections(chars);
  $("#cards").innerHTML = sections.map((s) => {
    const nameTag = s.key
      ? `<span class="grp-acc-name maskable" data-key="${escapeHtml(s.key)}" data-real="${escapeHtml(s.key)}" title="悬停查看 · 点击改名">${maskedAccount(s.key)}</span>`
      : `<span class="grp-acc-name">${escapeHtml(s.label)}</span>`;
    return `<section class="grp">
      <div class="grp-head">${nameTag}<span class="grp-count">${s.chars.length} 个角色</span></div>
      <div class="grp-cards">${s.chars.map(tileHtml).join("")}</div>
    </section>`;
  }).join("");
  bindMaskables($("#cards"));
}

function listRow(c) {
  let extHtml;
  if (c.total_sp == null) {
    extHtml = `<span class="muted">—</span>`;
  } else if (!c.extractor_count) {
    extHtml = `<span class="muted">0</span>`;
  } else {
    extHtml = `${c.extractor_count} 个`;
    if (marketUnit) extHtml += `<span class="sub">≈${fmtBig(c.extractor_count * marketUnit)}</span>`;
  }
  let trainHtml;
  if (c.needs_reauth) {
    trainHtml = `<span class="reauth-tag">需重新授权</span>`;
  } else if (isActiveTraining(c)) {
    const q0 = (c.queue || [])[0];
    trainHtml = `${escapeHtml(q0.name)} → Lv${q0.target_level != null ? q0.target_level : "?"}`;
  } else if ((c.queue || []).length) {
    trainHtml = `<span class="muted">⏸ 队列暂停</span>`;
  } else {
    trainHtml = `<span class="muted">未训练</span>`;
  }
  return `<tr class="c-row" data-id="${c.character_id}" title="点击查看详情">
    <td class="c-name"><img class="mini-avatar" src="${escapeHtml(c.portrait_url)}" alt="" loading="lazy"><span>${nameMaskSpan(c.character_name)}</span></td>
    <td class="num" title="${totalSpTip(c)}">${totalSpDisplay(c)}</td>
    <td class="num muted">${fmtNum(c.unallocated_sp)}</td>
    <td class="num">${fmtIsk(c.wallet_isk)}</td>
    <td class="num">${extHtml}</td>
    <td class="train">${trainHtml}</td>
  </tr>`;
}

function renderListView(chars) {
  const sections = groupSections(chars);
  const thead = `<thead><tr><th>角色</th><th>总SP(含未分配)</th><th>未分配</th><th>钱包</th><th>可提提取器</th><th>训练中</th></tr></thead>`;
  const bodyHtml = sections.map((s) => {
    const totalSp = s.chars.reduce((a, c) => a + (c.total_sp || 0) + (c.unallocated_sp || 0), 0);
    const totalIsk = s.chars.reduce((a, c) => a + (c.wallet_isk || 0), 0);
    const totalExt = s.chars.reduce((a, c) => a + (c.extractor_count || 0), 0);
    const accNameTag = s.key
      ? `<span class="acc-name maskable" data-key="${escapeHtml(s.key)}" data-real="${escapeHtml(s.key)}" title="悬停查看 · 点击改名">${maskedAccount(s.key)} ✎</span>`
      : `<span class="acc-name plain">${escapeHtml(s.label)}</span>`;
    const band = `<tr class="acc-row"><td colspan="6">${accNameTag}
      <span class="acc-meta">${s.chars.length} 人 · 总SP ${fmtBig(totalSp)} · 钱包 ${fmtIsk(totalIsk)} · 可提 ${totalExt} 个</span></td></tr>`;
    return band + s.chars.map(listRow).join("");
  }).join("");
  $("#list-view").innerHTML = `<table class="char-table">${thead}<tbody>${bodyHtml}</tbody></table>`;
  bindMaskables($("#list-view"));
}

function render(chars) {
  charsCache = chars;
  $("#empty").classList.toggle("hidden", chars.length > 0);
  const listMode = viewMode === "list";
  $("#cards").classList.toggle("hidden", listMode);
  $("#list-view").classList.toggle("hidden", !listMode);
  if (listMode) renderListView(chars); else renderCards(chars);

  refreshTotals();
  updateOpenModal();
  fitRefreshChars();
}

function refreshTotals() {
  const chars = charsCache;
  const total = chars.reduce((s, c) => s + (c.extractor_count || 0), 0);
  const sumEl = $("#summary");
  if (total > 0) {
    const profitTxt = marketUnit ? ` · 总利润 ≈ ${fmtIsk(total * marketUnit)} ISK` : "";
    sumEl.textContent = `⚡ 合计可提取 ${fmtNum(total)} 个技能提取器${profitTxt}`;
    sumEl.classList.remove("hidden");
  } else {
    sumEl.classList.add("hidden");
  }

  const wallets = chars.filter((c) => c.wallet_isk != null);
  const sumIskEl = $("#summary-isk");
  if (wallets.length > 0) {
    const totalIsk = wallets.reduce((s, c) => s + Number(c.wallet_isk), 0);
    const missing = chars.length - wallets.length;
    const note = missing > 0 ? `（${missing} 个角色暂无钱包数据）` : "";
    sumIskEl.textContent = `💰 全部角色钱包合计 ≈ ${fmtIsk(totalIsk)} ISK${note}`;
    sumIskEl.classList.remove("hidden");
  } else {
    sumIskEl.classList.add("hidden");
  }
}
function updateOpenModal() {
  const modal = $("#modal");
  const body = $("#modal-body");
  if (modal.classList.contains("hidden")) return;
  const id = Number(body.dataset.openId);
  if (!id) return;
  const c = charsCache.find((x) => x.character_id === id);
  if (c) renderDetail(c);
}

function sleepMs(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function refreshOne(id) {
  try {
    const res = await fetch(`/api/characters/${id}/refresh`, { method: "POST" });
    if (!res.ok) return null;
    const ch = await res.json();
    const idx = charsCache.findIndex((x) => x.character_id === id);
    if (idx >= 0) charsCache[idx] = ch; else charsCache.push(ch);
    return ch;
  } catch (e) {
    return null;
  }
}

function fitScheduleSavedRefresh(delay = 600) {
  clearTimeout(fitScheduleSavedRefresh.timer);
  fitScheduleSavedRefresh.timer = setTimeout(() => {
    if (!document.hidden) fitLoadSaved();
  }, delay);
}

function fitSyncPlanForCharacter(characterId) {
  const plan = fitState && fitState.optimizedPlan;
  if (plan && Number(plan.character_id) === Number(characterId) && fitOptimizedPlanValid()) {
    fitSyncOptimizedPlan();
  }
}

function updateCharDom(c) {
  const id = c.character_id;
  const tile = document.querySelector(`.tile[data-id="${id}"]`);
  if (tile) {
    tile.outerHTML = tileHtml(c);
    const nt = document.querySelector(`.tile[data-id="${id}"]`);
    if (nt) bindMaskables(nt);
  }
  const row = document.querySelector(`#list-view .c-row[data-id="${id}"]`);
  if (row) {
    row.outerHTML = listRow(c);
    const nr = document.querySelector(`#list-view .c-row[data-id="${id}"]`);
    if (nr) bindMaskables(nr);
  }
  refreshTotals();
  const modal = $("#modal");
  const body = $("#modal-body");
  if (modal && !modal.classList.contains("hidden") && Number(body.dataset.openId) === id) {
    const ae = document.activeElement;
    if (!(ae && ae.id === "acct-input")) renderDetail(c);
  }
  fitScheduleSavedRefresh();
  fitSyncPlanForCharacter(c.character_id);
}

let rotBusy = false;
const ROTATE_INTERVAL_MS = 30 * 60 * 1000; // 角色自动刷新：30 分钟一次
const ROTATE_POLL_MS = 60 * 1000;          // 每分钟检查一次是否到点

async function rotatePass() {
  if (rotBusy || document.hidden) return;
  const last = Number(localStorage.getItem("rotateLastTs") || 0);
  if (Date.now() - last < ROTATE_INTERVAL_MS) return; // 未满 30 分钟，不重复请求
  const targets = charsCache.filter((c) => !c.needs_reauth).map((c) => c.character_id);
  if (!targets.length) return;
  rotBusy = true;
  try {
    for (let i = 0; i < targets.length; i++) {
      if (document.hidden) break;
      const ch = await refreshOne(targets[i]);
      if (ch) updateCharDom(ch);
      if ((i + 1) % 5 === 0 || i + 1 === targets.length) {
        const st = $("#status");
        st.textContent = `角色自动刷新中 ${i + 1}/${targets.length}`;
        st.classList.remove("hidden");
        setTimeout(() => st.classList.add("hidden"), 2500);
      }
      await sleepMs(300);
    }
  } finally {
    rotBusy = false;
  }
  localStorage.setItem("rotateLastTs", String(Date.now()));
  loadHistory();
}async function onRefreshAll() {
  if (refreshingAll) return;
  const btn = $("#btn-refresh-all");
  const targets = charsCache.filter((c) => !c.needs_reauth).map((c) => c.character_id);
  if (!targets.length) { showBanner("没有可刷新的角色", true); return; }
  refreshingAll = true;
  btn.disabled = true;
  btn.textContent = "刷新中…";
  showStatus(`正在逐个刷新 0/${targets.length} …`);
  try {
    let done = 0;
    for (const id of targets) {
      const ch = await refreshOne(id);
      if (ch) updateCharDom(ch);
      done++;
      showStatus(`正在逐个刷新 ${done}/${targets.length} …`);
      await sleepMs(200);
    }
    showBanner("已刷新全部角色", false);
    showStatus(`已刷新 ${targets.length} 个角色`);
    loadHistory();
    setTimeout(() => showStatus(""), 3000);
  } catch (e) {
    showBanner("刷新全部失败：" + e.message, true);
  } finally {
    refreshingAll = false;
    btn.disabled = false;
    btn.textContent = "⟳ 刷新全部";
  }
}
async function autoRefresh() {
  if (refreshingAll || document.hidden) return;
  try {
    const res = await fetch("/api/characters", { cache: "no-store" });
    if (res.ok) render(await res.json());
  } catch (e) {
    /* 静默失败，等待下一轮 */
  }
}

function refreshTile(id) {
  const c = charsCache.find((x) => x.character_id === id);
  const el = document.querySelector(`.tile[data-id="${id}"]`);
  if (c && el) el.outerHTML = tileHtml(c);
}

/* ---------- 操作 ---------- */
async function onRefresh(ev) {
  const btn = ev.currentTarget;
  const id = Number(btn.dataset.id);
  btn.disabled = true;
  btn.textContent = "刷新中…";
  try {
    const res = await fetch(`/api/characters/${id}/refresh`, { method: "POST" });
    if (res.ok) {
      const c = await res.json();
      const idx = charsCache.findIndex((x) => x.character_id === id);
      if (idx >= 0) charsCache[idx] = c; else charsCache.push(c);
      refreshTile(id);
      renderDetail(c);
      fitScheduleSavedRefresh();
      fitSyncPlanForCharacter(c.character_id);
      if (c.needs_reauth) showBanner(c.auth_error || "该角色需要重新授权", true);
      else showBanner("");
    } else {
      const data = await res.json().catch(() => ({}));
      showBanner(data.detail || "刷新失败，请稍后重试", true);
      btn.disabled = false;
      btn.textContent = "刷新";
    }
  } catch (e) {
    showBanner("刷新失败：" + e.message, true);
    btn.disabled = false;
    btn.textContent = "刷新";
  }
}

function renderDetail(c) {
  const body = $("#modal-body");
  body.dataset.openId = String(c.character_id);
  body.innerHTML = detailHtml(c);
  bindModalActions();
  bindMaskables(body);
}

async function onRemove(ev) {
  const id = Number(ev.currentTarget.dataset.id);
  if (!window.confirm("确定移除该角色吗？将同时删除本地保存的令牌与缓存数据。")) return;
  try {
    const res = await fetch(`/api/characters/${id}`, { method: "DELETE" });
    if (res.ok) {
      closeDetail();
      load();
    } else {
      showBanner("移除失败，请重试", true);
    }
  } catch (e) {
    showBanner("移除失败：" + e.message, true);
  }
}

/* ---------- 顶部市场行情 ---------- */
function marketContainers() {
  return [document.getElementById("market"), document.getElementById("market-page")].filter(Boolean);
}

async function loadMarket() {
  try {
    const res = await fetch("/api/market/prices", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderMarket(await res.json());
  } catch (e) {
    const html = `<span class="mi-time">行情加载失败，请稍后重试</span>`;
    marketContainers().forEach((el) => {
      el.innerHTML = html;
      el.classList.remove("hidden");
    });
  }
}

function renderMarket(data) {
  marketUnit = (data && typeof data.unit_profit === "number") ? data.unit_profit : null;
  const parts = (data.items || []).map((it) => `
    <div class="mi">
      <span class="mi-name">${escapeHtml(it.name)}</span>
      <span class="mi-buy">收 ${fmtIsk(it.buy)}</span>
      <span class="mi-sell">售 ${fmtIsk(it.sell)}</span>
      <span class="mi-mid">中 ${fmtIsk(it.mid)}</span>
    </div>`).join("");
  const html = `<span class="mi-time">${data.stale ? "⚠ 旧数据 " : ""}更新 ${fmtTime(data.updated)}</span>${parts}`;
  marketContainers().forEach((el) => {
    el.innerHTML = html;
    el.classList.remove("hidden");
  });
  if (charsCache.length) render(charsCache);
}
async function renameAccount(el) {
  const key = el.dataset.key;
  if (key == null) return;
  const members = charsCache.filter((c) => (c.account_name || "").trim() === key);
  if (!members.length) return;
  const name = window.prompt(`为该账号组重命名（共 ${members.length} 个角色）：`, key || "");
  if (name === null) return;
  const trimmed = name.trim();
  if (trimmed === key) return;
  try {
    for (const c of members) {
      const res = await fetch(`/api/characters/${c.character_id}/account`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_name: trimmed }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const updated = await res.json();
      const idx = charsCache.findIndex((x) => x.character_id === updated.character_id);
      if (idx >= 0) charsCache[idx] = updated;
    }
    render(charsCache);
    showBanner(trimmed ? `已将 ${members.length} 个角色归入「${trimmed}」` : "已清除该账号组的分组名", false);
  } catch (e) {
    showBanner("修改账号名失败：" + e.message, true);
  }
}
async function onDeleteAll() {
  const count = charsCache.length;
  if (!count) {
    showBanner("当前没有可删除的角色", true);
    return;
  }
  if (!window.confirm(`确定要删除全部 ${count} 个角色吗？\n将同时删除每个角色的令牌与本地缓存，不可恢复。`)) return;
  try {
    const res = await fetch("/api/characters", { method: "DELETE" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    closeDetail();
    render([]);
    showBanner("已删除全部角色", false);
    showStatus("");
  } catch (e) {
    showBanner("删除失败：" + e.message, true);
  }
}
/* ---------- 事件绑定 ---------- */
/* ---------- 市场页：物品价格查询 ---------- */
async function doSearchItem() {
  const q = ($("#item-q").value || "").trim();
  const hint = $("#item-hint");
  const results = $("#item-results");
  const detail = $("#item-detail");
  detail.classList.add("hidden");
  if (!q) {
    hint.textContent = "请输入物品名称";
    hint.classList.remove("hidden");
    results.classList.add("hidden");
    return;
  }
  hint.textContent = "搜索中…";
  hint.classList.remove("hidden");
  try {
    const res = await fetch("/api/market/search?q=" + encodeURIComponent(q));
    if (!res.ok) throw new Error("HTTP " + res.status);
    const list = await res.json();
    if (!list.length) {
      results.classList.add("hidden");
      hint.textContent = "未找到「" + q + "」，请尝试更准确的官方名称（中文或英文）";
      return;
    }
    results.innerHTML = list.map((it) => `
      <button class="res-item" data-type="${it.type_id}" title="点击查看各中心价格">
        <span class="res-name">${escapeHtml(it.name)}</span>
        <span class="res-id">#${it.type_id}</span>
      </button>`).join("");
    results.classList.remove("hidden");
    hint.classList.add("hidden");
  } catch (e) {
    hint.textContent = "搜索失败：" + e.message;
  }
}

async function selectItem(typeId, detailSel) {
  const hint = $("#item-hint");
  const detail = document.querySelector(detailSel || "#item-detail");
  hint.classList.add("hidden");
  detail.classList.remove("hidden");
  detail.innerHTML = `<p class="loading">正在查询（可能需要几秒）…</p>`;
  try {
    const res = await fetch(`/api/market/item?type_id=${typeId}&refresh=1`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    renderItemDetail(detail, await res.json());
  } catch (e) {
    detail.innerHTML = `<p class="loading">查询失败：${escapeHtml(e.message)}</p>`;
  }
}

function renderItemDetail(detail, data) {
  const rows = (data.hubs || []).map((h) => {
    if (h.error) {
      return `<tr><td class="hub">${escapeHtml(h.name)}</td><td colspan="3" class="err">${escapeHtml(h.error)}</td></tr>`;
    }
    return `<tr>
      <td class="hub">${escapeHtml(h.name)}</td>
      <td class="num buy">${fmtIsk(h.buy)}</td>
      <td class="num sell">${fmtIsk(h.sell)}</td>
      <td class="num mid">${fmtIsk(h.mid)}</td>
    </tr>`;
  }).join("");
  detail.innerHTML = `
    <div class="item-detail-head">
      <h3>${escapeHtml(data.name)} <span class="res-id">#${data.type_id}</span></h3>
      <span class="sub">更新 ${fmtTime(data.updated)}</span>
    </div>
    <table class="price-table">
      <thead><tr><th>贸易中心</th><th>收购价</th><th>出售价</th><th>中间价</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}
/* ---------- 市场页：按分类浏览 ---------- */
let catState = { offset: 0, q: "", more: false, pending: false };
let marketUiLoaded = false;

function catItemBtn(it) {
  return `<button class="res-item" data-type="${it.type_id}" title="点击查看价格">
    <span class="res-name">${escapeHtml(it.name)}</span>
    <span class="res-id">#${it.type_id}</span>
  </button>`;
}

async function loadCategories() {
  try {
    const res = await fetch("/api/market/categories");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const cats = await res.json();
    const sel = $("#cat-select");
    sel.innerHTML = cats.map((c) =>
      `<option value="${c.category_id}">${escapeHtml(c.name)}（${c.count} 种）</option>`).join("");
    if (cats.length) loadCatItems(true);
  } catch (e) {
    const st = $("#cat-status");
    st.textContent = "分类加载失败：" + e.message;
    st.classList.remove("hidden");
  }
}

async function loadCatItems(reset) {
  if (catState.pending) return;
  const sel = $("#cat-select");
  const cid = Number(sel.value);
  if (!cid) return;
  const q = ($("#cat-q").value || "").trim();
  if (reset) { catState.q = q; catState.offset = 0; } else { catState.q = q; }
  const status = $("#cat-status");
  const box = $("#cat-items");
  status.textContent = reset ? "加载中…" : "加载更多…";
  status.classList.remove("hidden");
  catState.pending = true;
  try {
    const url = `/api/market/browse?category_id=${cid}&q=${encodeURIComponent(catState.q)}&offset=${catState.offset}&limit=100`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const items = await res.json();
    if (reset) box.innerHTML = items.map(catItemBtn).join("");
    else box.insertAdjacentHTML("beforeend", items.map(catItemBtn).join(""));
    catState.offset += items.length;
    catState.more = items.length === 100;
    $("#cat-more").classList.toggle("hidden", !catState.more);
    box.classList.remove("hidden");
    if (reset && items.length === 0) status.textContent = "该分类下没有匹配物品";
    else status.classList.add("hidden");
  } catch (e) {
    status.textContent = "加载失败：" + e.message;
  } finally {
    catState.pending = false;
  }
}
/* ---------- 可提取总数按天走势 ---------- */
async function loadHistory() {
  try {
    const [hRes, eRes] = await Promise.all([
      fetch("/api/history?limit=60"),
      fetch("/api/finance/extractions?limit=200"),
    ]);
    if (!hRes.ok) throw new Error("HTTP " + hRes.status);
    const rows = await hRes.json();
    const events = eRes.ok ? await eRes.json() : [];
    lastHistoryRows = rows;
    lastHistoryEvents = events;
    renderHistory(rows, events);
  } catch (e) {
    /* 静默 */
  }
}

function renderHistory(rows, events) {
  events = events || [];
  const el = $("#history-chart");
  const note = $("#history-note");
  if (!el) return;
  const eventsBox = $("#history-events");
  if (!rows || !rows.length) {
    el.innerHTML = "";
    note.textContent = "暂无历史记录：从今天起每天自动记录一个数据点，随时间积累后这里会形成走势。";
    if (eventsBox) eventsBox.innerHTML = "";
    return;
  }
  const vals = rows.map((r) => r.total_extractors);
  const maxV = Math.max(1, ...vals);
  const last = rows[rows.length - 1];
  note.textContent =
    `已记录 ${rows.length} 天 · 最近(${last.date}) 可提取 ${fmtNum(last.total_extractors)} 个 · 最高 ${fmtNum(Math.max(...vals))} 个` +
    (showExtractEvents ? " · 🔶=提取事件" : "（已隐藏提取事件）");

  const W = 840, H = 210, padL = 52, padR = 14, padT = 14, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  const n = rows.length;
  const x = (i) => (n === 1 ? padL + iw / 2 : padL + (i / (n - 1)) * iw);
  const yMax = maxV * 1.1;
  const y = (v) => padT + ih - (v / yMax) * ih;
  const pts = rows.map((r, i) => `${x(i).toFixed(1)},${y(r.total_extractors).toFixed(1)}`);

  let grid = "";
  [0, 0.5, 1].forEach((f) => {
    const yy = padT + ih - f * ih;
    const val = Math.round(yMax * f);
    grid += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" stroke="#2d333b" stroke-width="1"/>
             <text x="${padL - 8}" y="${yy + 4}" text-anchor="end" fill="#8b949e" font-size="11">${fmtNum(val)}</text>`;
  });

  const area = n > 1
    ? `<path d="M${pts[0]} L${pts.slice(1).join(" L")} L${x(n - 1).toFixed(1)},${padT + ih} L${x(0).toFixed(1)},${padT + ih} Z" fill="rgba(242,169,0,0.12)" stroke="none"/>`
    : "";
  const line = `<polyline points="${pts.join(" ")}" fill="none" stroke="#f2a900" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`;
  const dots = n <= 45
    ? rows.map((r, i) => {
        const tip = `${r.date}\n可提取 ${fmtNum(r.total_extractors)} 个\n已训练合计 ${fmtNum(r.trained_sp_total)} SP\n含未分配合计 ${fmtNum(r.combined_sp_total)} SP\n角色数 ${r.char_count}`;
        return `<circle cx="${x(i).toFixed(1)}" cy="${y(r.total_extractors).toFixed(1)}" r="4" fill="#ffd55a" style="cursor:pointer"><title>${escapeHtml(tip)}</title></circle>`;
      }).join("")
    : "";
  const evByDate = {};
  events.forEach((ev) => {
    (evByDate[ev.date] = evByDate[ev.date] || []).push(ev);
  });
  const markers = (showExtractEvents ? rows : []).map((r, i) => {
    const evs = evByDate[r.date];
    if (!evs || !evs.length) return "";
    const lines = evs.map((ev) => `${ev.account} / ${ev.character_name || "角色未知"} x${ev.qty}`).join("\n");
    const cx = x(i);
    return `<path d="M${cx},${y(r.total_extractors) - 9} l7,7 l-7,7 l-7,-7 Z" fill="#ff6b6b" stroke="#fff" stroke-width="1" style="cursor:pointer"><title>${escapeHtml(r.date + "\n提取:\n" + lines)}</title></path>`;
  }).join("");

  const mid = n > 2 ? rows[Math.floor(n / 2)].date : "";
  const xl = `${rows[0].date}${mid ? `<text x="${x(Math.floor(n / 2))}" y="${H - 8}" text-anchor="middle" fill="#8b949e" font-size="11">${escapeHtml(mid)}</text>` : ""}<text x="${x(n - 1)}" y="${H - 8}" text-anchor="end" fill="#8b949e" font-size="11">${escapeHtml(rows[n - 1].date)}</text>`;

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="background:var(--panel);border:1px solid var(--line);border-radius:10px">
    ${grid}${area}${line}${dots}${markers}${xl}
  </svg>`;

  if (eventsBox) {
    const recent = showExtractEvents ? events.slice().sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 10) : [];
    eventsBox.innerHTML = recent.length
      ? `<div class="he-title">提取事件(最近 ${recent.length} 条)</div>` + recent.map((ev) =>
          `<div class="he-row">🔶 ${escapeHtml(ev.date)} · ${escapeHtml(ev.account)} · ${escapeHtml(ev.character_name || "角色未知")} · ${ev.qty} 个</div>`
        ).join("")
      : "";
  }
}
/* ---------- 农场财务 ---------- */
let finEditing = null; // { kind: 'income'|'expense', id }
let finEntryById = {};

function finDateInput() {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}
function finMonthNow() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function finMsg(text, isErr) {
  const b = $("#fin-msg");
  b.textContent = text || "";
  b.classList.toggle("error", !!isErr);
  b.classList.toggle("hidden", !text);
}

function finSelectOptions(accountList) {
  const names = accountList.filter((n) => n && n !== "未归属").sort((a, b) => a.localeCompare(b, "zh"));
  const entryOpts = '<option value="">未归属</option>' +
    names.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
  const filterOpts = '<option value="">全部账号</option><option value="__none__">未归属</option>' +
    names.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
  return { entryOpts, filterOpts };
}

function applyFinOptions(accounts) {
  const keep = (sel) => { const el = $(sel); return el ? el.value : ""; };
  const vInc = keep("#fin-inc-account"), vExp = keep("#fin-exp-account"), vFil = keep("#fin-filter-account");
  const { entryOpts, filterOpts } = finSelectOptions(accounts);
  const i = $("#fin-inc-account"); if (i) i.innerHTML = entryOpts;
  const e = $("#fin-exp-account"); if (e) e.innerHTML = entryOpts;
  const f = $("#fin-filter-account"); if (f) f.innerHTML = filterOpts;
  if (i) i.value = vInc; if (e) e.value = vExp; if (f) f.value = vFil;
}

function finKindLabel(e) {
  let name;
  if (e.kind === "expense") {
    name = e.category === "omega" ? "欧米伽" : (e.category === "mct" ? "多角色训练 MCT" : "技能提取器");
  } else {
    name = "收入 · 卖注入器";
  }
  return e.auto ? name + " · 自动" : name;
}

function finCardHtml(title, d) {
  const cls = d.profit >= 0 ? "pos" : "neg";
  return `<div class="fin-card">
    <div class="fin-label">${escapeHtml(title)}</div>
    <div class="fin-row"><span>收入</span><b>${fmtIsk(d.income)}</b></div>
    <div class="fin-row"><span>支出</span><b>${fmtIsk(d.expense)}</b></div>
    <div class="fin-row"><span>利润</span><b class="${cls}">${fmtIsk(d.profit)}</b></div>
  </div>`;
}

async function financeReload() {
  const month = $("#fin-month").value || "";
  const account = $("#fin-filter-account").value || "";
  try {
    const qs = new URLSearchParams();
    if (month) qs.set("month", month);
    if (account) qs.set("account", account);
    const sRes = await fetch(`/api/finance/summary?${qs.toString()}`);
    const eRes = await fetch(`/api/finance/entries?${qs.toString()}`);
    if (!sRes.ok || !eRes.ok) throw new Error("HTTP");
    const sum = await sRes.json();
    const entries = await eRes.json();
    applyFinOptions(sum.accounts.map((a) => a.account));
    renderFinance(sum, entries);
  } catch (err) {
    finMsg("财务数据加载失败：" + err.message, true);
  }
}

function renderFinance(sum, entries) {
  finEntryById = {};
  let cards = finCardHtml("累计利润", sum.cumulative);
  if (sum.month) cards += finCardHtml(`本月(${sum.month_label || "?"})利润`, sum.month);

  const accRows = sum.accounts.map((a) => {
    const cls = a.profit >= 0 ? "pos" : "neg";
    return `<tr><td>${escapeHtml(a.account)}</td><td class="num">${fmtIsk(a.income)}</td>
      <td class="num">${fmtIsk(a.expense)}</td><td class="num ${cls}">${fmtIsk(a.profit)}</td></tr>`;
  }).join("");
  $("#fin-summary").innerHTML = `<div class="fin-cards">${cards}</div>`;
  $("#fin-accounts").innerHTML = accRows
    ? `<table class="fin-table"><thead><tr><th>账号</th><th>收入</th><th>支出</th><th>利润</th></tr></thead><tbody>${accRows}</tbody></table>`
    : `<p class="sub">暂无收支记录</p>`;

  const eRows = entries.map((e) => {
    finEntryById[e.id] = e;
    const cls = e.kind === "income" ? "pos" : "neg";
    const sign = e.kind === "income" ? "" : "-";
    return `<tr>
      <td>${escapeHtml(e.date)}</td>
      <td>${escapeHtml(finKindLabel(e))}</td>
      <td>${escapeHtml(e.account_name || "未归属")}</td>
      <td class="num ${cls}">${sign}${fmtIsk(e.amount)}</td>
      <td class="num">${e.qty ? fmtNum(e.qty) : "—"}</td>
      <td class="num">${e.qty ? fmtIsk(e.amount / e.qty) : "—"}</td>
      <td class="note">${escapeHtml(e.note || "")}</td>
      <td class="ops">
        <button class="btn small fin-edit-btn" data-kind="${e.kind}" data-id="${e.id}">编辑</button>
        <button class="btn small danger fin-del-btn" data-id="${e.id}">删除</button>
      </td>
    </tr>`;
  }).join("");
  $("#fin-entries").innerHTML = eRows
    ? `<table class="fin-table"><thead><tr><th>日期</th><th>类型</th><th>账号</th><th>金额(ISK)</th><th>数量</th><th>单价(均价)</th><th>备注</th><th>操作</th></tr></thead><tbody>${eRows}</tbody></table>`
    : `<p class="sub">该条件下暂无明细</p>`;
}

function finClearForm(kind) {
  const p = kind === "income"
    ? ["#fin-inc-date", "#fin-inc-qty", "#fin-inc-amount", "#fin-inc-note"]
    : ["#fin-exp-date", "#fin-exp-amount", "#fin-exp-note"];
  p.forEach((s) => { const el = $(s); if (el) el.value = ""; });
}

async function saveFinance(kind) {
  const isIncome = kind === "income";
  const amount = Number((isIncome ? $("#fin-inc-amount") : $("#fin-exp-amount")).value);
  if (!(amount > 0)) { finMsg("请填写大于 0 的金额", true); return; }
  const date = (isIncome ? $("#fin-inc-date") : $("#fin-exp-date")).value;
  if (!date) { finMsg("请选择日期", true); return; }
  const account_name = (isIncome ? $("#fin-inc-account") : $("#fin-exp-account")).value;
  let qty = null;
  if (isIncome) {
    const q = $("#fin-inc-qty").value;
    if (q !== "") {
      qty = Number(q);
      if (!Number.isInteger(qty) || qty <= 0) { finMsg("数量必须为正整数", true); return; }
    }
  }
  const body = {
    kind,
    date,
    account_name,
    amount,
    qty,
    note: (isIncome ? $("#fin-inc-note") : $("#fin-exp-note")).value,
  };
  if (!isIncome) body.category = $("#fin-exp-type").value;
  const editing = finEditing && finEditing.kind === kind ? finEditing : null;
  try {
    const url = editing ? `/api/finance/entries/${editing.id}` : "/api/finance/entries";
    const res = await fetch(url, {
      method: editing ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "HTTP " + res.status);
    }
    if (editing) finCancelEdit(kind);
    else finClearForm(kind);
    finMsg(editing ? "已保存修改" : "已保存", false);
    financeReload();
  } catch (e) {
    finMsg("保存失败：" + e.message, true);
  }
}

function finCancelEdit(kind) {
  if (finEditing && finEditing.kind === kind) finEditing = null;
  const b = $(kind === "income" ? "#fin-inc-edit" : "#fin-exp-edit");
  if (b) b.classList.add("hidden");
  const btn = $(kind === "income" ? "#btn-fin-inc-save" : "#btn-fin-exp-save");
  if (btn) btn.textContent = kind === "income" ? "保存收入" : "保存支出";
}

function startFinEdit(kind, id, entry) {
  finEditing = { kind, id };
  const date = kind === "income" ? "#fin-inc-date" : "#fin-exp-date";
  const acct = kind === "income" ? "#fin-inc-account" : "#fin-exp-account";
  const amount = kind === "income" ? "#fin-inc-amount" : "#fin-exp-amount";
  const note = kind === "income" ? "#fin-inc-note" : "#fin-exp-note";
  $(date).value = entry.date || finDateInput();
  $(acct).value = entry.account_name || "";
  $(amount).value = entry.amount;
  $(note).value = entry.note || "";
  if (kind === "income") $("#fin-inc-qty").value = entry.qty || "";
  else $("#fin-exp-type").value = ["omega", "mct", "extractor"].includes(entry.category) ? entry.category : "omega";
  const b = $(kind === "income" ? "#fin-inc-edit" : "#fin-exp-edit");
  b.textContent = `正在编辑 #${id} · 修改后点保存 `;
  b.appendChild(document.getElementById(kind === "income" ? "btn-fin-inc-cancel" : "btn-fin-exp-cancel"));
  b.classList.remove("hidden");
  const btn = $(kind === "income" ? "#btn-fin-inc-save" : "#btn-fin-exp-save");
  btn.textContent = "保存修改";
}
function financeInit() {
  const dInc = $("#fin-inc-date"); if (dInc && !dInc.value) dInc.value = finDateInput();
  const dExp = $("#fin-exp-date"); if (dExp && !dExp.value) dExp.value = finDateInput();
  const m = $("#fin-month"); if (m && !m.value) m.value = finMonthNow();
  financeReload();
}
/* ---------- 配装规划 ---------- */
let fitState = { shipTypeId: null, shipName: "", name: "", items: [], savedId: null, last: null };
let fitSkillMode = "online";
let fitAnalyzeSeq = 0;
let fitCharFilter = null;
const FIT_CHAR_STORAGE_KEY = "fitCharacterId";
const FIT_CHAR_FILTER_STORAGE_KEY = "fitCharacterFilter";
const FIT_RESET_POLICY_STORAGE_KEY = "fitResetPolicyHours";
const FIT_RESET_POLICY_HOURS = [24, 72, 168];

function fitResetPolicyHours() {
  const value = Number(($("#fit-reset-policy") || {}).value);
  return FIT_RESET_POLICY_HOURS.includes(value) ? value : 24;
}

function fitInitResetPolicy() {
  const select = $("#fit-reset-policy");
  if (!select) return;
  let saved = 24;
  try { saved = Number(localStorage.getItem(FIT_RESET_POLICY_STORAGE_KEY)) || 24; } catch (e) { /* ignore */ }
  select.value = String(FIT_RESET_POLICY_HOURS.includes(saved) ? saved : 24);
}

function fitApplyPlanResetPolicy(plan) {
  const select = $("#fit-reset-policy");
  const value = Number(plan && plan.optimization_policy && plan.optimization_policy.max_time_regret_hours);
  if (!select || !FIT_RESET_POLICY_HOURS.includes(value)) return;
  select.value = String(value);
  try { localStorage.setItem(FIT_RESET_POLICY_STORAGE_KEY, String(value)); } catch (e) { /* ignore */ }
}

function fitMsg(text, isErr) {
  const el = $("#fit-import-msg");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = isErr ? "#ff9b9b" : "var(--muted)";
}

function fitSetShipInfo(text) {
  const el = $("#fit-ship-info");
  if (el) el.textContent = text || "";
}

function fitCharacterName(id) {
  const c = (charsCache || []).find((x) => String(x.character_id) === String(id));
  return c ? c.character_name : "";
}

function fitUpdateAnalysisCharLabel() {
  const el = $("#fit-analysis-char");
  if (!el) return;
  const sel = $("#fit-char");
  const c = (charsCache || []).find((x) => String(x.character_id) === String(sel && sel.value));
  const name = c ? c.character_name : "未选择角色";
  el.textContent = fitCharFilter === ""
    ? `全部角色方案 · 技能分析角色：${name}`
    : `技能分析角色：${name}`;
}

function fitCharacterPlanCounts() {
  const counts = new Map();
  (fitSavedCache || []).forEach((f) => {
    const id = fitBoundCharacterId(f);
    if (!id) return;
    const key = String(id);
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return counts;
}

function fitRenderCharButtons() {
  const box = $("#fit-char-buttons");
  if (!box) return;
  const counts = fitCharacterPlanCounts();
  const chars = charsCache || [];
  const allActive = fitCharFilter === "";
  const total = (fitSavedCache || []).length;
  const allCount = total > 0 ? ` (${total})` : "";
  const buttons = [`<button class="btn fit-char-btn all${allActive ? " active" : ""}" data-char-filter="" title="显示全部角色的方案">全部角色${allCount}</button>`]
    .concat(chars.map((c) => {
      const id = String(c.character_id);
      const count = counts.get(id) || 0;
      const countText = count > 0 ? ` (${count})` : "";
      return `<button class="btn fit-char-btn${!allActive && String(fitCharFilter) === id ? " active" : ""}" data-char-filter="${id}" title="只显示 ${escapeHtml(c.character_name)} 的方案">${escapeHtml(c.character_name)}${countText}</button>`;
    }));
  box.innerHTML = buttons.join("") + (!chars.length ? '<span class="sub">暂无角色</span>' : "");
  fitUpdateAnalysisCharLabel();
}

function fitSetCharFilter(value, analyze = true) {
  const next = value == null ? "" : String(value);
  const sel = $("#fit-char");
  const previous = String((sel && sel.value) || "");
  const changedRole = !!next && next !== previous;
  if (changedRole) fitClearAll();
  fitCharFilter = next;
  try { localStorage.setItem(FIT_CHAR_FILTER_STORAGE_KEY, fitCharFilter); } catch (e) { /* ignore */ }
  if (sel && fitCharFilter) {
    sel.value = fitCharFilter;
    try { localStorage.setItem(FIT_CHAR_STORAGE_KEY, fitCharFilter); } catch (e) { /* ignore */ }
  }
  fitRenderCharButtons();
  fitLoadSaved();
  if (analyze && !changedRole && fitState.shipTypeId) fitAnalyze();
}

function fitRefreshChars() {
  const sel = $("#fit-char");
  if (!sel) return;
  const chars = charsCache || [];
  if (!chars.length) {
    sel.innerHTML = '<option value="">不对比角色(仅列技能)</option>';
    sel.value = "";
    if (fitCharFilter === null) {
      try { fitCharFilter = localStorage.getItem(FIT_CHAR_FILTER_STORAGE_KEY) || ""; }
      catch (e) { fitCharFilter = ""; }
    }
    fitRenderCharButtons();
    return;
  }
  let saved = null;
  let savedFilter = null;
  try {
    saved = localStorage.getItem(FIT_CHAR_STORAGE_KEY);
    savedFilter = localStorage.getItem(FIT_CHAR_FILTER_STORAGE_KEY);
  } catch (e) { /* ignore */ }
  const valid = (v) => v === "" || chars.some((c) => String(c.character_id) === String(v));
  const keep = sel.value;
  let value = null;
  if (valid(keep) && keep !== "") value = keep;
  if (value == null && saved !== null && valid(saved)) value = saved;
  if (value == null) value = String(chars[0].character_id);
  const opts = ['<option value="">不对比角色(仅列技能)</option>']
    .concat(chars.map((c) => `<option value="${c.character_id}">${escapeHtml(c.character_name)}</option>`));
  sel.innerHTML = opts.join("");
  sel.value = value;
  try { localStorage.setItem(FIT_CHAR_STORAGE_KEY, value); } catch (e) { /* ignore */ }
  const filterWasNull = fitCharFilter === null;
  if (fitCharFilter === null) {
    fitCharFilter = savedFilter === null ? value : savedFilter;
  }
  if (fitCharFilter !== "" && !valid(fitCharFilter)) fitCharFilter = value;
  fitRenderCharButtons();
  if (filterWasNull) fitScheduleSavedRefresh(100);
  if (value !== keep && fitState.shipTypeId) fitAnalyze();
}

async function fitShipSearch() {
  const q = ($("#fit-ship-q").value || "").trim();
  if (!q) return;
  const box = $("#fit-ship-results");
  box.textContent = "搜索中…";
  box.classList.remove("hidden");
  try {
    const res = await fetch("/api/market/search?q=" + encodeURIComponent(q) + "&limit=30");
    const list = await res.json();
    if (!list.length) { box.innerHTML = '<span class="sub">未找到匹配物品</span>'; return; }
    box.innerHTML = list.map((it) =>
      `<button class="res-item" data-type="${it.type_id}"><span class="res-name">${escapeHtml(it.name)}</span><span class="res-id">#${it.type_id}</span></button>`
    ).join("");
  } catch (e) {
    box.innerHTML = `<span class="sub">搜索失败:${escapeHtml(e.message)}</span>`;
  }
}

async function fitShipPick(typeId) {
  try {
    const res = await fetch(`/api/fitting/item?type_id=${typeId}`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const info = await res.json();
    if (info.category_id !== 6) { fitMsg("该物品不是舰船,请重新选择", true); return; }
    fitState.shipTypeId = typeId;
    fitState.shipName = info.name;
    $("#fit-ship-results").classList.add("hidden");
    fitSetShipInfo(`舰船:${info.name}(#${typeId})`);
    fitAnalyze();
  } catch (e) {
    fitMsg("读取舰船信息失败:" + e.message, true);
  }
}

async function fitItemSearch() {
  const q = ($("#fit-item-q").value || "").trim();
  if (!q) return;
  const box = $("#fit-item-results");
  box.textContent = "搜索中…";
  box.classList.remove("hidden");
  try {
    const res = await fetch("/api/market/search?q=" + encodeURIComponent(q) + "&limit=30");
    const list = await res.json();
    box.innerHTML = list.length
      ? list.map((it) => `<button class="res-item" data-type="${it.type_id}"><span class="res-name">${escapeHtml(it.name)}</span><span class="res-id">#${it.type_id}</span></button>`).join("")
      : '<span class="sub">未找到匹配物品</span>';
  } catch (e) {
    box.innerHTML = `<span class="sub">搜索失败:${escapeHtml(e.message)}</span>`;
  }
}

async function fitItemAdd(typeId) {
  const qty = Math.max(1, parseInt($("#fit-item-qty").value || "1", 10));
  let slot = $("#fit-item-slot").value || "";
  try {
    const info = await (await fetch(`/api/fitting/item?type_id=${typeId}`)).json();
    if (!slot) slot = info.slot_guess || "";
  } catch (e) { /* 保留手动选择 */ }
  fitState.items.push({
    type_id: typeId, qty, slot, slot_index: fitNextIndex(slot || "unknown"),
    charge_type_id: null, active: false,
  });
  $("#fit-item-results").classList.add("hidden");
  fitAnalyze();
}

async function fitAnalyze(options = {}) {
  const seq = ++fitAnalyzeSeq;
  fitUpgradeSkillIds = null;
  fitUncertainSkillIds = new Set();
  fitRenderExtraSkills();
  if (!fitState.shipTypeId) {
    fitRenderVisual({});
    $("#fit-limits").innerHTML = "";
    $("#fit-items").textContent = "尚未添加装备";
    $("#fit-items-count").textContent = "(0)";
    return;
  }
  const charId = Number($("#fit-char").value) || null;
  try {
    const res = await fetch("/api/fitting/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ship_type_id: fitState.shipTypeId,
        items: fitState.items,
        extra_skills: fitState.extraSkills || [],
        character_id: charId,
        skill_mode: fitSkillMode,
      }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (seq !== fitAnalyzeSeq) return;
    fitSkillLevels = (data.character && data.character.skill_levels) || {};
    fitState.last = data;
    fitRender(data, options);
  } catch (e) {
    if (seq === fitAnalyzeSeq) fitMsg("分析失败:" + e.message, true);
  }
}

function fitBar(label, used, total) {
  const ok = total <= 0 ? used <= 0 : used <= total;
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return `<div class="fit-bar">
    <span class="fit-bar-label">${label}</span>
    <div class="fit-bar-track"><div class="fit-bar-fill ${ok ? "ok" : "bad"}" style="width:${pct}%"></div></div>
    <span class="fit-bar-num ${ok ? "" : "bad"}">${fmtNum(Math.round(used))} / ${fmtNum(Math.round(total))}</span>
  </div>`;
}

function fitRenderTabsSafe() {
  try { fitTabsRender(); fitTabsSave(); } catch (e) { /* ignore */ }
}

function fitSetSkillMode(mode) {
  fitSkillMode = mode;
  document.querySelectorAll("#fit-mode-switch button").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
  if (fitState.shipTypeId) fitAnalyze();
}

function fitStatRow(label, value) {
  return `<div class="fit-stat"><span>${escapeHtml(label)}</span><b>${escapeHtml(String(value))}</b></div>`;
}

function fitRenderStats(data) {
  const el = $("#fit-stats");
  if (!el) return;
  if (!data.stats) { el.textContent = "选择舰船后显示"; return; }
  const s = data.stats;
  const c = s.capacitor, t = s.targeting, m = s.mobility, d = s.defense;
  const pct = (v) => `${Math.round(Number(v))}%`;
  el.innerHTML = `
    <div class="fit-stat-head">电容</div>
    ${fitStatRow("容量", fmtNum(c.capacity))}
    ${fitStatRow("回充时间", c.recharge_s + " 秒")}
    ${fitStatRow("平均回充", c.recharge_avg + "/秒")}
    ${fitStatRow("过量电容回充速度", (c.recharge_peak != null ? c.recharge_peak : "—") + "/秒")}
    ${fitStatRow("持续消耗(已启用)", c.drain + "/秒")}
    ${fitStatRow("稳定度(估算)", c.stable_pct == null ? "—" : c.stable_pct + "%")}
    <div class="fit-stat-head">锁定</div>
    ${fitStatRow("锁定距离", t.range + " km")}
    ${fitStatRow("最大锁定目标", t.max_targets)}
    ${fitStatRow("扫描分辨率", t.scan_resolution)}
    ${fitStatRow("感应强度", "雷达" + t.sensor.radar + " / 光雷达" + t.sensor.ladar + " / 磁力" + t.sensor.magnetometric + " / 引力" + t.sensor.gravimetric)}
    <div class="fit-stat-head">机动</div>
    ${fitStatRow("最大速度", m.max_velocity + " m/s")}
    ${fitStatRow("朝向时间", m.align_s + " 秒")}
    ${fitStatRow("跃迁速度", m.warp_speed + " AU/s")}
    <div class="fit-stat-head">防御</div>
    ${fitStatRow("护盾 / 装甲 / 结构", fmtNum(d.shield_hp) + " / " + fmtNum(d.armor_hp) + " / " + fmtNum(d.hull_hp))}
    ${fitStatRow("护盾抗性 电/热/动/爆", d.shield_res.map(pct).join(" / "))}
    ${fitStatRow("装甲抗性 电/热/动/爆", d.armor_res.map(pct).join(" / "))}
    ${fitStatRow("结构抗性 电/热/动/爆", d.hull_res.map(pct).join(" / "))}
    ${fitStatRow("护盾平均回充", d.shield_regen + "/秒")}
    ${fitStatRow("EHP(均匀伤害)", fmtNum(d.ehp))}
    <div class="sub">${(s.notes || []).map(escapeHtml).join(" · ")}</div>`;
}
function fitHoursText(hours) {
  const h = Number(hours) || 0;
  if (h <= 0) return "—";
  if (h >= 48) return `${Math.floor(h / 24)} 天 ${Math.round(h % 24)} 小时`;
  if (h >= 1) return `${Math.floor(h)} 小时 ${Math.round((h % 1) * 60)} 分`;
  return `${Math.round(h * 60)} 分钟`;
}

function fitPlanSignature() {
  const charId = Number(($("#fit-char") || {}).value) || null;
  return JSON.stringify({
    ship: fitState.shipTypeId,
    items: fitState.items || [],
    extra: fitState.extraSkills || [],
    charId,
    mode: fitSkillMode,
  });
}

function fitNormalizeOptimizedPlan(plan) {
  if (!plan || typeof plan !== "object") return plan;
  if (plan.tracked == null && plan.applied === true) plan.tracked = true;
  delete plan.applied;
  return plan;
}

function fitOptimizedPlanValid() {
  return !!(fitState.optimizedPlan && fitState.optimizedPlan.signature === fitPlanSignature());
}

function fitOptimizedQueueLines(plan) {
  return (plan.nodes || []).map((r) => {
    const hint = String(r.name_en || r.name || "").replace(/"/g, "&quot;");
    const label = String(r.name || r.name_en || hint);
    return `<localized hint="${hint}">${label}</localized> ${r.level || 1}`;
  });
}

function fitRenderOptimizedPlan() {
  const box = $("#fit-skill-optimizer");
  if (!box) return;
  const plan = fitNormalizeOptimizedPlan(fitState.optimizedPlan);
  if (!plan || plan.signature !== fitPlanSignature()) {
    box.innerHTML = "";
    if (plan) {
      fitState.optimizedPlan = null;
      fitTabsSave();
    }
    return;
  }
  const resetLabel = { start: "当前属性", bonus: "奖励属性重置", normal: "普通属性重置" };
  const phases = (plan.phases || []).map((phase) => {
    const eff = phase.profile && phase.profile.effective ? phase.profile.effective : {};
    const labels = phase.profile && phase.profile.labels ? phase.profile.labels : {};
    const attrs = Object.keys(eff).map((k) => `${labels[k] || k} ${Math.round(eff[k])}`).join(" / ");
    const nodes = (phase.nodes || []).map((r) => {
      const orderText = r.order_ok === false ? "队列中 · 顺序错误" : (r.order_ok === true ? "队列中 · 顺序正确" : "队列中");
      const orderTip = r.status === "queued" ? `方案顺序 ${r.plan_order || "—"}，队列顺序 ${r.queue_order || "—"}` : "";
      const queueTag = r.status === "queued"
        ? ` <span class="queue-badge${r.order_ok === false ? " bad" : ""}" title="${escapeHtml(orderTip)}">${orderText}</span>`
        : ' <span class="queue-warning">未在训练队列</span>';
      const planRate = Number(r.rate_sp_hour) || 0;
      const queueRate = Number(r.queue_rate_sp_hour) || 0;
      const speedTip = queueRate > 0 && Math.abs(queueRate - planRate) > 1
        ? `阶段计划速度 ${fmtNum(Math.round(planRate))} SP/小时；当前队列实际 ${fmtNum(Math.round(queueRate))} SP/小时`
        : "";
      return `<tr>
      <td>${escapeHtml(r.name)}${queueTag}</td><td class="num">Lv${r.level}</td>
      <td class="num" title="${escapeHtml(speedTip)}">${fmtNum(Math.round(planRate))} SP/小时</td>
      <td class="num">${(r.hours || 0).toFixed(2)} 小时</td>
      <td class="num">${fmtTime(r.finish_at)}</td></tr>`;
    }).join("");
    const wait = phase.reset && phase.reset.wait_hours > 0 ? ` · 等待 ${phase.reset.wait_hours.toFixed(2)} 小时` : "";
    return `<div class="optimizer-phase">
      <div class="optimizer-phase-head">阶段 ${phase.phase} · ${escapeHtml(resetLabel[phase.reset && phase.reset.type] || "属性")}${wait}</div>
      <div class="sub">${escapeHtml(attrs)}</div>
      <div class="fin-table-wrap"><table class="fin-table"><thead><tr><th>技能</th><th>等级</th><th>训练速度</th><th>训练时长</th><th>预计完成</th></tr></thead><tbody>${nodes}</tbody></table></div>
    </div>`;
  }).join("");
  const completedNodes = Array.isArray(plan.completed_nodes) ? plan.completed_nodes : [];
  const completedRows = completedNodes.map((r) => `<tr>
      <td>${escapeHtml(r.name || r.name_en || `技能 #${r.skill_id}`)}</td>
      <td class="num">Lv${r.level || 1}</td>
      <td><span class="num pos">已完成</span></td>
      <td class="num">${fmtTime(r.completed_at)}</td>
    </tr>`).join("");
  const completedHtml = completedNodes.length
    ? `<details class="fit-items-details optimizer-completed">
        <summary>历史完成节点 <span>(${completedNodes.length})</span></summary>
        <div class="fin-table-wrap"><table class="fin-table">
          <thead><tr><th>技能</th><th>等级</th><th>状态</th><th>记录时间</th></tr></thead>
          <tbody>${completedRows}</tbody>
        </table></div>
      </details>`
    : "";
  const remaps = plan.remaps || {};
  const baselineHours = Number(plan.baseline_hours) || 0;
  const totalHours = Number(plan.total_hours) || 0;
  const fastestHours = Number(plan.fastest_hours);
  const policyRegretHours = Number(plan.optimization_policy && plan.optimization_policy.max_time_regret_hours) || 24;
  const timeRegretHours = Number(plan.time_regret_hours);
  const hasPolicyTiming = Number.isFinite(fastestHours) && Number.isFinite(timeRegretHours);
  const policyTimingText = hasPolicyTiming
    ? `理论最快 ${fastestHours.toFixed(2)} 小时 · 为少用重置增加 ${timeRegretHours.toFixed(2)} 小时（上限 ${policyRegretHours} 小时）`
    : "该旧方案未记录重置策略";
  const savedHours = Number(plan.saved_hours) || 0;
  const savingText = savedHours > 0.01
    ? `预计节省 ${savedHours.toFixed(2)} 小时`
    : (savedHours < -0.01 ? `预计多耗时 ${Math.abs(savedHours).toFixed(2)} 小时` : "优化前后耗时基本相同");
  const applyAction = plan.tracked
    ? '<b class="plan-applied-badge">已设为目标</b>'
    : '<button class="btn small primary" data-opt-apply>设为目标方案</button>';
  box.innerHTML = `<div class="optimizer-summary">
      <b>优化训练方案</b> · 绑定角色 #${plan.character_id || "—"} · 不优化 ${baselineHours.toFixed(2)} 小时 · 优化后 ${totalHours.toFixed(2)} 小时 · 预计完成 ${fmtTime(plan.finish_at)}
      · ${policyTimingText}
      · ${savingText}
      · 奖励重置 ${remaps.bonus_used || 0} 次 · 普通重置 ${remaps.normal_used || 0} 次${plan.rebase_performed ? " · 已按当前训练队列校时" : ""}
      ${plan.queue_order_ok === true ? " · 队列顺序正确" : (plan.queue_order_ok === false ? " · 队列顺序有误" : "")}
      ${plan.not_queued_count ? ` · 未在训练队列 ${plan.not_queued_count} 项` : ""}
      <span>${applyAction}
      <button class="btn small" data-opt-copy>复制训练队列</button></span>
    </div>${phases}${completedHtml}`;
  const applyButton = box.querySelector("[data-opt-apply]");
  if (applyButton) applyButton.addEventListener("click", () => {
    plan.tracked = true;
    delete plan.applied;
    fitState.optimizedPlan = plan;
    fitTabsSave();
    fitRenderOptimizedPlan();
    fitMsg("优化方案已设为目标方案", false);
    if (fitState.savedId) fitSave(false);
  });
  box.querySelector("[data-opt-copy]").addEventListener("click", () => {
    const lines = fitOptimizedQueueLines(plan);
    navigator.clipboard.writeText(lines.join("\n")).then(
      () => fitMsg(`已复制优化后的 ${lines.length} 项技能`, false),
      () => fitMsg("复制失败，请手动选择", true),
    );
  });
}

async function fitOptimizePlan() {
  if (!fitState.shipTypeId) { fitMsg("请先选择舰船", true); return; }
  const charId = Number($("#fit-char").value) || null;
  if (!charId) { fitMsg("请先选择用于优化的角色", true); return; }
  const box = $("#fit-skill-optimizer");
  box.textContent = "正在优化训练方案…";
  try {
    const res = await fetch("/api/fitting/optimize-skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ship_type_id: fitState.shipTypeId,
        items: fitState.items,
        extra_skills: fitState.extraSkills || [],
        character_id: charId,
        skill_mode: fitSkillMode,
        max_time_regret_hours: fitResetPolicyHours(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
    const previousPlan = fitState.optimizedPlan;
    if (previousPlan && Number(previousPlan.character_id) === Number(charId)) {
      const history = new Map();
      [...(previousPlan.completed_nodes || []), ...(data.completed_nodes || [])].forEach((node) => {
        const key = `${node.skill_id || 0}:${node.level || 1}`;
        if (node && node.skill_id && !history.has(key)) history.set(key, node);
      });
      data.completed_nodes = [...history.values()];
    }
    data.signature = fitPlanSignature();
    data.tracked = false;
    delete data.applied;
    fitState.optimizedPlan = data;
    fitTabsSave();
    fitRenderOptimizedPlan();
    fitMsg(`优化完成：预计 ${(data.total_hours || 0).toFixed(2)} 小时`, false);
  } catch (e) {
    fitState.optimizedPlan = null;
    box.innerHTML = `<div class="sub bad">优化失败：${escapeHtml(e.message)}</div>`;
    fitMsg("优化失败：" + e.message, true);
  }
}

function fitRender(data, options = {}) {
  fitPassiveMap = {};
  (data.items || []).forEach((i) => { fitPassiveMap[i.type_id] = !!i.passive; });
  fitRenderTabsSafe();
  fitRenderOptimizedPlan();
  if (options.visual !== false) fitRenderVisual(data);
  fitRenderStats(data);
  const box = $("#fit-items");
  $("#fit-items-count").textContent = `(${data.items.length})`;
  if (!data.items.length) {
    box.textContent = "尚未添加装备";
  } else {
    box.innerHTML = data.items.map((it, i) =>
      `<div class="fit-item-row"><span class="fit-item-slot">${escapeHtml(it.slot)}</span>
        <span class="fit-item-name">${escapeHtml(it.name)}</span>
        <span class="sub">×${it.qty}</span>
        <button class="btn small danger fit-remove" data-type="${it.type_id}" data-slot="${escapeHtml(it.slot)}">移除</button></div>`
    ).join("");
  }
  const L = data.limits;
  $("#fit-limits").innerHTML = [
    fitBar("CPU", L.cpu.used, L.cpu.total),
    fitBar("能量栅格", L.power.used, L.power.total),
    fitBar("改装校准", L.rig_calibration.used, L.rig_calibration.total),
    fitBar("炮台/发射器", L.weapon_slots.used, L.weapon_slots.total),
    fitBar("无人机舱容", L.drone_bay.used, L.drone_bay.total),
    fitBar("无人机带宽", L.drone_bandwidth.used, L.drone_bandwidth.total),
  ].join("") + `<div class="sub">${L.notes.map(escapeHtml).join(" · ")}</div>`;

  const sk = $("#fit-skills");
  const modeLabel = { online: "上线所需", all4: "全部技能到 4", all5: "全部技能到 5" }[fitSkillMode] || "上线所需";
  if (!data.skills.length) {
    sk.innerHTML = '<div class="sub">该配装没有技能需求记录</div>';
    return;
  }
  if (data.character) {
    const ch = data.character;
    const rows = ch.items.map((r) => `<tr>
      <td>${escapeHtml(r.name)}</td>
      <td class="num">${r.need_level}</td>
      <td class="num">${r.active_level}</td>
      <td class="num">${r.trained_level}</td>
      <td>${r.ok ? '<span class="num pos">满足</span>' : '<span class="num bad">不足</span>'}</td>
      <td class="num">${r.rate_sp_hour ? fmtNum(Math.round(r.rate_sp_hour)) + " SP/小时" : "—"}</td>
      <td class="num">${r.sp_deficit ? fmtNum(r.sp_deficit) : "—"}</td>
      <td class="num">${r.hours ? r.hours.toFixed(1) + " 小时" : "—"}</td>
      <td class="num">${r.finish_at ? fmtTime(r.finish_at) : "—"}</td>
    </tr>`).join("");
    const totalSp = ch.total_sp_deficit != null ? ch.total_sp_deficit : ch.items.reduce((a, r) => a + (r.sp_deficit || 0), 0);
    const totalHours = ch.total_hours != null ? ch.total_hours : ch.items.reduce((a, r) => a + (r.hours || 0), 0);
    const remap = ch.remap || {};
    const remapTitle = remap.total_remaps != null
      ? `奖励重置 ${remap.bonus_remaps} 次；普通重置 ${remap.normal_available ? "可用" : "不可用"}${remap.next_normal_remap ? `；下次可用 ${fmtTime(remap.next_normal_remap)}` : ""}`
      : "刷新角色后获取属性重置次数";
    const foot = ch.missing_count > 0
      ? `<tfoot><tr class="fit-total-row"><th colspan="6">合计缺失 ${ch.missing_count} 项</th>
           <th class="num">${fmtNum(totalSp)}</th><th class="num">${fitHoursText(totalHours)}</th>
           <th class="num">${ch.plan_finished_at ? fmtTime(ch.plan_finished_at) : "—"}</th></tr></tfoot>`
      : "";
    sk.innerHTML = `<div class="sub">目标模式:${modeLabel} · 对比角色:<b>${escapeHtml(ch.character_name)}</b> · 缺失 ${ch.missing_count} 项 ·
        合计缺失 <b>${fmtNum(totalSp)}</b> SP · 预计总时长 <b>${fitHoursText(totalHours)}</b>
        · 可重置属性 <b title="${escapeHtml(remapTitle)}">${remap.total_remaps != null ? remap.total_remaps : "—"}</b> 次
        (${ch.rate_mode === "per_skill" ? "训练速度按角色当前主/副属性逐技能计算" : "训练速度使用队列/默认估算"})</div>
      <div class="fin-table-wrap"><table class="fin-table"><thead><tr><th>技能</th><th>需求</th><th>可用</th><th>已训练</th><th>状态</th><th>训练速度</th><th>缺 SP</th><th>预计时长</th><th>预计完成</th></tr></thead><tbody>${rows}</tbody>${foot}</table></div>`;
  } else {
    sk.innerHTML = `<div class="sub">目标模式:${modeLabel}</div><table class="fin-table"><thead><tr><th>技能</th><th>需求等级</th></tr></thead><tbody>` +
      data.skills.map((r) => `<tr><td>${escapeHtml(r.name)}</td><td class="num">${r.need_level}</td></tr>`).join("") +
      `</tbody></table>`;
  }
}

/* ---------- 多配装分页 ---------- */
let fitTabs = [];
let fitActive = 0;

function fitBlankTab() {
  return { shipTypeId: null, shipName: "", name: "", items: [], extraSkills: [], optimizedPlan: null, savedId: null, last: null };
}

function fitTabsLoad() {
  try {
    const raw = localStorage.getItem("fitTabs");
    if (raw) {
      const data = JSON.parse(raw);
      if (Array.isArray(data.tabs) && data.tabs.length) {
        fitTabs = data.tabs;
        fitActive = Math.min(data.active || 0, fitTabs.length - 1);
      }
    }
  } catch (e) { /* ignore */ }
  fitTabs.forEach((t) => {
    if (!Array.isArray(t.extraSkills)) t.extraSkills = [];
    if (!t.optimizedPlan) t.optimizedPlan = null;
  });
  if (!fitTabs.length) { fitTabs = [fitBlankTab()]; fitActive = 0; }
  fitState = fitTabs[fitActive];
  fitRenderExtraSkills();
}

function fitTabsSave() {
  try {
    fitTabs[fitActive] = fitState;
    localStorage.setItem("fitTabs", JSON.stringify({ tabs: fitTabs, active: fitActive }));
  } catch (e) { /* ignore */ }
}

function fitTabsRender() {
  const box = $("#fit-tabs");
  if (!box) return;
  box.innerHTML = fitTabs.map((t, i) => {
    const label = t.name || t.shipName || `配装 ${i + 1}`;
    return `<button class="fit-tab ${i === fitActive ? "active" : ""}" data-tab="${i}">
      ${escapeHtml(label)}${t.shipTypeId ? " ▸" : ""}
      <span class="fit-tab-export" data-export="${i}" title="导出该配装(英文 EFT)">⤓</span>
      ${fitTabs.length > 1 ? `<span class="fit-tab-close" data-close="${i}" title="关闭">×</span>` : ""}
    </button>`;
  }).join("") + `<button class="fit-tab add" data-new="1" title="新建配装">＋</button>`;
}

function fitTabsSwitch(i) {
  if (i === fitActive || !fitTabs[i]) return;
  fitTabs[fitActive] = fitState;
  fitActive = i;
  fitState = fitTabs[i];
  fitApplyPlanResetPolicy(fitState.optimizedPlan);
  fitTabsRender();
  fitAnalyze();
}

function fitTabsNew() {
  fitTabs[fitActive] = fitState;
  fitTabs.push(fitBlankTab());
  fitActive = fitTabs.length - 1;
  fitState = fitTabs[fitActive];
  fitTabsRender();
  fitTabsSave();
  fitAnalyze();
}

function fitTabsClose(i) {
  if (fitTabs.length <= 1) { return; }
  const t = fitTabs[i];
  if (t.items && t.items.length && !window.confirm(`关闭「${t.name || t.shipName || "配装"}」?未保存的内容会丢失。`)) return;
  fitTabs.splice(i, 1);
  if (fitActive >= fitTabs.length) fitActive = fitTabs.length - 1;
  else if (i < fitActive) fitActive -= 1;
  fitState = fitTabs[fitActive];
  fitTabsRender();
  fitTabsSave();
  fitAnalyze();
}
async function fitExportTab(i) {
  fitTabs[fitActive] = fitState;
  const tab = fitTabs[i];
  if (!tab || !tab.shipTypeId) { fitMsg("该配装还没有选择舰船", true); return; }
  try {
    const res = await fetch("/api/fitting/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ship_type_id: tab.shipTypeId,
        items: tab.items || [],
        name: tab.name || tab.shipName || `Fit ${i + 1}`,
      }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const box = $("#fit-export");
    box.classList.remove("hidden");
    box.innerHTML = `<div class="fit-picker-head">配装导出(CCP 官方格式 · 英文)
        <span><button class="btn small" id="fit-export-copy">复制</button>
        <button class="btn small" id="fit-export-close">关闭</button></span></div>
      <textarea id="fit-export-text" readonly rows="10">${escapeHtml(data.text)}</textarea>
      <div class="sub">可直接粘贴回游戏内的配装窗口。</div>`;
    document.getElementById("fit-export-close").addEventListener("click", () => box.classList.add("hidden"));
    document.getElementById("fit-export-copy").addEventListener("click", () => {
      const ta = document.getElementById("fit-export-text");
      ta.select();
      navigator.clipboard.writeText(ta.value).then(
        () => fitMsg("配装已复制到剪贴板(英文 EFT 格式)", false),
        () => fitMsg("复制失败,请手动复制文本框内容", true)
      );
    });
    const ta = document.getElementById("fit-export-text");
    ta.focus(); ta.select();
  } catch (e) {
    fitMsg("导出失败:" + e.message, true);
  }
}
async function fitImportEft() {
  const text = $("#fit-eft").value || "";
  if (!text.trim()) { fitMsg("请先粘贴配装文本", true); return; }
  try {
    const res = await fetch("/api/fitting/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!res.ok || data.error) { fitMsg(data.error || "解析失败", true); return; }
    fitState.shipTypeId = data.ship_type_id;
    fitState.name = data.name || "";
    const counters = {};
    fitState.items = (data.items || []).map((it) => {
      const key = it.slot || "unknown";
      const idx = counters[key] || 0;
      counters[key] = idx + 1;
      return { ...it, slot_index: idx, active: false };
    });
    fitState.extraSkills = [];
    fitState.optimizedPlan = null;
    fitState.savedId = null;
    $("#fit-name").value = fitState.name;
    fitSetShipInfo(`舰船:#${data.ship_type_id}`);
    fitMsg(data.unknown && data.unknown.length ? `导入完成,但有 ${data.unknown.length} 行未识别` : "导入完成", !!(data.unknown && data.unknown.length));
    fitTabsRender();
    fitTabsSave();
    fitAnalyze();
  } catch (e) {
    fitMsg("导入失败:" + e.message, true);
  }
}

let fitSavedCache = [];

function fitBoundCharacterId(f) {
  const raw = f && f.data && f.data.optimizedPlan && f.data.optimizedPlan.character_id;
  const id = Number(raw) || 0;
  return id || null;
}

function fitQueueMatchData(f) {
  const m = f && f.queue_match;
  return m && typeof m === "object" ? m : {
    status: "unknown", matched_count: 0, required_count: 0,
    missing_count: 0, extra_count: 0, completed_count: 0,
    queue_count: 0, order_ok: null, reason: "",
  };
}

function fitQueueMatchSummary(f) {
  const m = fitQueueMatchData(f);
  const matched = Number(m.matched_count || 0);
  const required = Number(m.required_count || 0);
  if (m.status === "unknown") {
    return f && f.data && f.data.optimizedPlan ? "待确认" : "未应用 · 无方案";
  }
  if (m.status === "applied") {
    if (!required) return `已应用 · 已完成${m.completed_count ? ` (${m.completed_count})` : ""}`;
    return `已应用 ${matched}/${required}`;
  }
  if (m.status === "partial") return `部分应用 ${matched}/${required}`;
  if (m.status === "out_of_order") return `队列乱序 ${matched}/${required}`;
  return `未应用 ${matched}/${required}`;
}

function fitQueueMatchTitle(f) {
  const m = fitQueueMatchData(f);
  const details = [fitQueueMatchSummary(f)];
  if (m.required_count) {
    details.push(`匹配 ${m.matched_count || 0}/${m.required_count}`);
    if (m.missing_count) details.push(`缺失 ${m.missing_count}`);
  }
  if (m.extra_count) details.push(`额外 ${m.extra_count}`);
  if (m.order_ok === false) details.push("队列顺序错误");
  if (m.reason) details.push(m.reason);
  details.push("点击载入配装和训练方案");
  return details.join(" · ");
}

async function fitLoadSaved() {
  const box = $("#fit-saved-list");
  if (!box) return;
  try {
    const res = await fetch("/api/fittings", { cache: "no-store" });
    fitSavedCache = await res.json();
    fitRenderCharButtons();
    const visiblePlans = fitSavedCache.filter((f) => {
      if (fitCharFilter === null || fitCharFilter === "") return true;
      return String(fitBoundCharacterId(f) || "") === String(fitCharFilter);
    });
    const groups = { applied: [], partial: [], out_of_order: [], pending: [] };
    visiblePlans.forEach((f) => {
      const status = fitQueueMatchData(f).status;
      if (status === "applied") groups.applied.push(f);
      else if (status === "partial") groups.partial.push(f);
      else if (status === "out_of_order") groups.out_of_order.push(f);
      else groups.pending.push(f);
    });
    const renderGroup = (title, kind, rows, emptyText) => {
      const buttons = rows.map((f) => {
        const selected = fitState.savedId === f.id;
        const boundId = fitBoundCharacterId(f);
        const boundName = fitCharacterName(boundId) || "未绑定角色";
        const summary = fitQueueMatchSummary(f);
        const summaryWithChar = fitCharFilter === "" ? `${boundName} · ${summary}` : summary;
        const title = `${boundName} · ${fitQueueMatchTitle(f)}`;
        return `<button class="btn fit-saved-btn${selected ? " active" : ""}" data-saved-id="${f.id}" title="${escapeHtml(title)}">
          <span class="fit-saved-name">${escapeHtml(f.name)}</span><small>${escapeHtml(summaryWithChar)}</small>
        </button>`;
      }).join("");
      return `<div class="fit-saved-group ${kind}">
        <div class="fit-saved-group-head">${title}<span>${rows.length}</span></div>
        <div class="fit-saved-group-list">${buttons || `<span class="sub">${emptyText}</span>`}</div>
      </div>`;
    };
    box.innerHTML = renderGroup("已应用方案", "applied", groups.applied, "暂无已应用方案")
      + renderGroup("部分应用方案", "partial", groups.partial, "暂无部分应用方案")
      + renderGroup("队列乱序", "out_of_order", groups.out_of_order, "暂无乱序方案")
      + renderGroup("未应用方案", "pending", groups.pending, "暂无未应用方案");
  } catch (e) {
    box.innerHTML = '<span class="sub">读取已保存配装失败</span>';
  }
}

async function fitSave(showMessage = true) {
  if (!fitState.shipTypeId) { fitMsg("请先选择舰船", true); return; }
  const name = ($("#fit-name").value || "").trim() || fitState.shipName;
  const body = JSON.stringify({ name, ship_type_id: fitState.shipTypeId, data: { items: fitState.items, extraSkills: fitState.extraSkills || [], optimizedPlan: fitState.optimizedPlan || null } });
  try {
    const res = await fetch(fitState.savedId ? `/api/fittings/${fitState.savedId}` : "/api/fittings", {
      method: fitState.savedId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" }, body,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
    if (!fitState.savedId && data.id) fitState.savedId = data.id;
    fitState.name = name;
    fitTabsRender();
    fitTabsSave();
    if (showMessage) fitMsg("配装已保存", false);
    fitLoadSaved();
  } catch (e) { fitMsg("保存失败:" + e.message, true); }
}

async function fitLoadOne(id = fitState.savedId) {
  const wanted = Number(id);
  if (!wanted) { fitMsg("请选择已保存配装", true); return; }
  if (!fitSavedCache.length) await fitLoadSaved();
  const f = fitSavedCache.find((x) => x.id === wanted);
  if (!f) { fitMsg("已保存配装不存在，请刷新列表", true); return; }
  fitState.shipTypeId = f.ship_type_id;
  fitState.savedId = f.id;
  fitState.name = f.name;
  fitState.items = (f.data && f.data.items) || [];
  fitState.extraSkills = (f.data && f.data.extraSkills) || [];
  fitState.optimizedPlan = fitNormalizeOptimizedPlan((f.data && f.data.optimizedPlan) || null);
  fitApplyPlanResetPolicy(fitState.optimizedPlan);
  const boundChar = Number(fitState.optimizedPlan && fitState.optimizedPlan.character_id) || null;
  if (boundChar && (charsCache || []).some((c) => Number(c.character_id) === boundChar)) {
    $("#fit-char").value = String(boundChar);
    try { localStorage.setItem(FIT_CHAR_STORAGE_KEY, String(boundChar)); } catch (e) { /* ignore */ }
    fitUpdateAnalysisCharLabel();
  } else if (boundChar) {
    fitState.optimizedPlan = null;
    fitMsg("方案绑定的角色不存在，请重新优化并保存。", true);
  }
  $("#fit-name").value = f.name;
  fitSetShipInfo(`舰船:#${f.ship_type_id}`);
  fitTabsSave();
  fitLoadSaved();
  fitAnalyze();
  if (fitState.optimizedPlan) fitSyncOptimizedPlan();
}

async function fitSyncOptimizedPlan() {
  const plan = fitState.optimizedPlan;
  if (!plan || !plan.character_id) return false;
  const charId = Number(plan.character_id);
  if (!$("#fit-char") || Number($("#fit-char").value) !== charId) return false;
  try {
    const res = await fetch("/api/fitting/rebase-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: charId, plan }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
    data.signature = fitPlanSignature();
    data.tracked = !!(plan.tracked || plan.applied);
    delete data.applied;
    fitState.optimizedPlan = data;
    fitTabsSave();
    fitRenderOptimizedPlan();
    if (fitState.savedId) await fitSave(false);
    return true;
  } catch (e) {
    fitMsg("训练方案时间校准失败：" + e.message, true);
    return false;
  }
}

async function fitDeleteOne() {
  const id = fitState.savedId;
  if (!id) { fitMsg("请先载入或保存一个配装", true); return; }
  if (!window.confirm(`删除配装「${fitState.name || ("#" + id)}」吗?`)) return;
  await fetch(`/api/fittings/${id}`, { method: "DELETE" });
  fitState.savedId = null;
  fitTabsSave();
  fitLoadSaved();
}

function fitClearAll() {
  Object.assign(fitState, fitBlankTab());
  fitSetShipInfo("");
  fitRenderExtraSkills();
  fitTabsRender();
  fitTabsSave();
  $("#fit-name").value = "";
  $("#fit-items").textContent = "尚未添加装备";
  $("#fit-items-count").textContent = "(0)";
  $("#fit-limits").innerHTML = "";
  $("#fit-skill-optimizer").innerHTML = "";
  $("#fit-skills").textContent = "选择舰船并添加装备后,这里显示所需技能。";
  $("#fit-eft").value = "";
  $("#fit-extra-skills").innerHTML = "";
  fitRenderVisual({});
  fitRenderStats({});
  fitMsg("");
}

function fitRenderExtraSkills() {
  const box = $("#fit-extra-skills");
  if (!box) return;
  const list = fitState.extraSkills || [];
  if (!list.length) { box.innerHTML = ""; return; }
  box.innerHTML = '<span class="sub">手动追加:</span>' + list.map((s) =>
    `<span class="fit-extra-skill">${escapeHtml(s.name || `技能 #${s.skill_id}`)} Lv${s.need_level}<button type="button" data-remove-extra="${s.skill_id}" title="移除">×</button></span>`
  ).join("");
}

function fitAddExtraSkill(typeId, name, level) {
  const id = Number(typeId);
  fitState.extraSkills = fitState.extraSkills || [];
  const hit = fitState.extraSkills.find((s) => Number(s.skill_id) === id);
  if (hit) hit.need_level = level;
  else fitState.extraSkills.push({ skill_id: id, name, need_level: level });
  fitTabsSave();
  fitRenderExtraSkills();
  fitAnalyze();
}

async function fitOpenSkillGroupPicker() {
  const el = $("#fit-picker");
  fitPick = { slot: "", index: null, mode: "skillGroup", moduleType: null };
  el.classList.add("floating");
  el.classList.remove("hidden");
  el.innerHTML = `<div class="fit-picker-head">追加技能
      <button class="btn small" id="fit-picker-close">关闭</button></div>
    <div class="sub">先选择技能大类</div>
    <div id="fit-skill-groups" class="item-results"><span class="sub">加载技能大类…</span></div>`;
  document.getElementById("fit-picker-close").addEventListener("click", () => el.classList.add("hidden"));
  try {
    if (!fitSkillGroupsCache) {
      const res = await fetch("/api/fitting/skill-groups");
      if (!res.ok) throw new Error("HTTP " + res.status);
      fitSkillGroupsCache = await res.json();
    }
    const box = document.getElementById("fit-skill-groups");
    box.innerHTML = fitSkillGroupsCache.length
      ? fitSkillGroupsCache.map((g) => `<button class="res-item" data-skill-group="${g.group_id}" data-group-name="${escapeHtml(g.name)}">${escapeHtml(g.name)} <span class="sub">(${g.count})</span></button>`).join("")
      : '<span class="sub">没有技能大类</span>';
  } catch (e) {
    document.getElementById("fit-skill-groups").innerHTML = `<span class="sub">读取技能大类失败:${escapeHtml(e.message)}</span>`;
  }
}

function fitOpenSkillList(groupId, groupName) {
  const el = $("#fit-picker");
  fitPick = { slot: "", index: null, mode: "skill", moduleType: null, groupId: Number(groupId) };
  el.classList.add("floating");
  el.classList.remove("hidden");
  el.innerHTML = `<div class="fit-picker-head">追加技能 · ${escapeHtml(groupName || "技能")}
      <span><button class="btn small" id="fit-skill-back">← 大类</button>
      <button class="btn small" id="fit-picker-close">关闭</button></span></div>
    <div class="search-row"><input id="fit-pick-q" placeholder="搜索该大类中的技能"></div>
    <div id="fit-pick-results" class="item-results"></div>`;
  document.getElementById("fit-picker-close").addEventListener("click", () => el.classList.add("hidden"));
  document.getElementById("fit-skill-back").addEventListener("click", fitOpenSkillGroupPicker);
  const input = document.getElementById("fit-pick-q");
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") fitPickSearch(); });
  input.addEventListener("input", () => { clearTimeout(input.__t); input.__t = setTimeout(fitPickSearch, 250); });
  fitPickSearch();
  if (fitUpgradeSkillIds == null) {
    fitLoadUpgradeSkillIds().then(() => {
      if (fitPick.mode === "skill" && document.getElementById("fit-pick-results")) fitPickSearch();
    });
  }
}

async function fitLoadUpgradeSkillIds() {
  if (!fitState.shipTypeId) { fitUpgradeSkillIds = new Set(); return; }
  try {
    const charId = Number($("#fit-char").value) || null;
    const res = await fetch("/api/fitting/upgrade-skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ship_type_id: fitState.shipTypeId, items: fitState.items, character_id: charId }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    fitUpgradeSkillIds = new Set((data.skill_ids || []).map(Number));
    fitUncertainSkillIds = new Set((data.uncertain_skill_ids || []).map(Number));
  } catch (e) {
    fitUpgradeSkillIds = new Set();
  }
}
async function fitChooseSkillLevel(typeId) {
  try {
    const info = await (await fetch(`/api/fitting/skill-info?type_id=${typeId}`)).json();
    const name = info.name || (`技能 #${typeId}`);
    const rawDesc = String(info.description || "暂无技能说明").replace(/<br\s*\/?>/gi, "\n");
    const desc = escapeHtml(rawDesc).replace(/\n/g, "<br>");
    const prereq = (info.prerequisites || []).length
      ? `<div class="skill-info-meta">前置：${info.prerequisites.map((p) => `${escapeHtml(p.name)} Lv${p.level}`).join("、")}</div>`
      : "";
    const el = $("#fit-picker");
    el.classList.add("floating");
    el.classList.remove("hidden");
    el.innerHTML = `<div class="fit-picker-head">追加技能:${escapeHtml(name)}
        <button class="btn small" id="fit-picker-close">关闭</button></div>
      <div class="skill-effect-card">
        <div class="skill-effect-title">技能效果</div>
        <div class="skill-effect-text">${desc}</div>
        <div class="skill-info-meta">等级系数：${escapeHtml(String(info.rank ?? "—"))} · 主属性：${escapeHtml(info.primary_attribute || "—")} · 副属性：${escapeHtml(info.secondary_attribute || "—")}</div>
        ${prereq}
      </div>
      <div class="sub">选择目标等级</div>
      <div class="search-row">${[1, 2, 3, 4, 5].map((l) => `<button class="btn" data-skill-level="${l}">等级 ${l}</button>`).join("")}</div>`;
    document.getElementById("fit-picker-close").addEventListener("click", () => el.classList.add("hidden"));
    el.querySelectorAll("[data-skill-level]").forEach((btn) => btn.addEventListener("click", () => {
      fitAddExtraSkill(typeId, name, Number(btn.dataset.skillLevel));
      el.classList.add("hidden");
    }));
  } catch (e) {
    fitMsg("读取技能信息失败:" + e.message, true);
  }
}

function fitCopyMissing() {
  const d = fitState.last;
  if (!d || !d.character) { fitMsg("先选择角色再复制缺失技能", true); return; }
  const optimized = fitOptimizedPlanValid() ? fitState.optimizedPlan : null;
  const missing = optimized ? (optimized.nodes || []) : d.character.items.filter((r) => !r.ok);
  if (!missing.length) { fitMsg("该角色已满足全部技能", false); return; }
  // EVE 训练队列导入格式：<localized hint="英文名">本地化名称</localized> 等级
  const lines = optimized ? fitOptimizedQueueLines(optimized) : missing.map((r) => {
    const hint = String(r.name_en || r.name || "").replace(/"/g, "&quot;");
    const label = String(r.name || r.name_en || hint);
    return `<localized hint="${hint}">${label}</localized> ${r.need_level}`;
  });
  navigator.clipboard.writeText(lines.join("\n")).then(
    () => fitMsg(`已复制 ${lines.length} 项可导入游戏训练队列的技能`, false),
    () => fitMsg("复制失败,请手动选择", true)
  );
}
let fitPick = { slot: "", index: null, mode: "add", moduleType: null };
let fitSkillGroupsCache = null;
let fitUpgradeSkillIds = null;
let fitUncertainSkillIds = new Set();
let fitSkillLevels = {};

function fitNextIndex(slot) {
  const idxs = fitState.items.filter((x) => x.slot === slot && x.slot_index != null).map((x) => x.slot_index);
  return idxs.length ? Math.max(...idxs) + 1 : 0;
}

function fitItemAt(slot, index) {
  return fitState.items.find((x) => x.slot === slot && x.slot_index === index) || null;
}

function fitNameOf(typeId, data) {
  const hit = (data.items || []).find((i) => i.type_id === typeId);
  if (hit) return hit.name;
  const cached = (fitNames[typeId] = fitNames[typeId] || "");
  return cached || ("#" + typeId);
}
let fitNames = {};
let fitPassiveMap = {};

function fitIco(typeId) {
  return `https://images.evetech.net/types/${typeId}/icon?size=64`;
}

function fitSlotBoxHtml(slot, index, data) {
  const it = fitItemAt(slot, index);
  if (!it) {
    return `<button class="fit-slot empty" data-slot="${slot}" data-index="${index}" title="点击选择装备">+</button>`;
  }
  const nm = fitNameOf(it.type_id, data);
  const charge = it.charge_type_id ? `<img class="fit-charge-ico" src="${fitIco(it.charge_type_id)}" title="弹药">` : "";
  const qty = it.qty > 1 ? `<span class="fit-qty">×${it.qty}</span>` : "";
  const passive = !!fitPassiveMap[it.type_id];
  const cls = passive ? "fit-slot filled passive" : (it.active ? "fit-slot filled active" : "fit-slot filled");
  const tip = passive ? `${nm}(被动装备,始终生效)` :
    `${nm}${it.active ? "(已启用,点击停用)" : "(未启用,点击启用)"}`;
  return `<button class="${cls}" data-slot="${slot}" data-index="${index}" data-type="${it.type_id}" title="${escapeHtml(tip)}">
    <img src="${fitIco(it.type_id)}" alt="">${charge}${qty}
    <span class="fit-slot-menu" data-menu="1" data-slot="${slot}" data-index="${index}" title="更多操作">⋯</span></button>`;
}

function fitRenderVisual(data) {
  const box = $("#fit-visual");
  if (!box) return;
  if (!fitState.shipTypeId) {
    box.innerHTML = `<div class="fit-ship-empty">
      <button class="fit-ship-slot" title="点击选择舰船">＋ 点击选择舰船</button>
      <div class="sub">先选舰船,然后逐个点击高/中/低/改装槽位添加装备。</div>
    </div>`;
    return;
  }
  const slots = data.limits.slots;
  const col = (label, slot, count) => {
    let cells = "";
    for (let i = 0; i < count; i++) cells += fitSlotBoxHtml(slot, i, data);
    return `<div class="slot-col"><div class="slot-col-title">${label} ${count}</div><div class="slot-cells">${cells}</div></div>`;
  };
  const drones = fitState.items.filter((x) => x.slot === "drone");
  const droneHtml = drones.map((d) => `<button class="fit-slot filled drone" data-slot="drone" data-index="${d.slot_index}" data-type="${d.type_id}" title="${escapeHtml(fitNameOf(d.type_id, data))}">
      <img src="${fitIco(d.type_id)}" alt=""><span class="fit-qty">×${d.qty}</span></button>`).join("");
  box.innerHTML = `
    <div class="fit-ship">
      <button class="fit-ship-slot ship-chosen" title="点击更换舰船">
        <img src="https://images.evetech.net/types/${fitState.shipTypeId}/render?size=256" alt="">
      </button>
      <div class="fit-ship-name">${escapeHtml(data.ship.name)}</div>
    </div>
    <div class="fit-slot-grid">
      ${col("高槽", "hi", Math.round(slots.hi.total))}
      ${col("中槽", "med", Math.round(slots.med.total))}
      ${col("低槽", "low", Math.round(slots.low.total))}
      ${col("改装", "rig", Math.round(slots.rig.total))}
    </div>
    <div class="fit-drone-row">
      <span class="slot-col-title">无人机</span>
      <div class="slot-cells">${droneHtml}<button class="fit-slot empty" data-slot="drone" data-index="${fitNextIndex("drone")}" title="添加无人机">+</button></div>
    </div>`;
}

function fitOpenPicker(msg) {
  const el = $("#fit-picker");
  el.classList.toggle("floating", fitPick.mode === "skill");
  el.classList.remove("hidden");
  const title = fitPick.mode === "ship" ? "选择舰船" : (fitPick.mode === "charge" ? "选择弹药" : (fitPick.mode === "skill" ? "追加技能" : (fitPick.mode === "replace" ? "更换装备" : "选择装备")));
  el.innerHTML = `<div class="fit-picker-head">${title}
      <button class="btn small" id="fit-picker-close">关闭</button></div>
    <div class="search-row"><input id="fit-pick-q" placeholder="输入名称搜索(可留空看常用)"></div>
    <div id="fit-pick-results" class="item-results">${msg || ""}</div>`;
  document.getElementById("fit-picker-close").addEventListener("click", () => el.classList.add("hidden"));
  const input = document.getElementById("fit-pick-q");
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") fitPickSearch(); });
  input.addEventListener("input", () => { clearTimeout(input.__t); input.__t = setTimeout(fitPickSearch, 250); });
  fitPickSearch();
}

async function fitPickSearch() {
  const res = $("#fit-pick-results");
  if (!res) return;
  const q = ($("#fit-pick-q") || {}).value || "";
  let url;
  if (fitPick.mode === "charge") url = `/api/fitting/charges?type_id=${fitPick.moduleType}&q=${encodeURIComponent(q)}`;
  else if (fitPick.mode === "ship") url = `/api/fitting/search?slot=ship&q=${encodeURIComponent(q)}`;
  else if (fitPick.mode === "skill") {
    url = `/api/fitting/search?slot=skill&q=${encodeURIComponent(q)}`;
    if (fitPick.groupId != null) url += `&group_id=${fitPick.groupId}`;
  }
  else url = `/api/fitting/search?slot=${encodeURIComponent(fitPick.slot)}&q=${encodeURIComponent(q)}`;
  try {
    let list = await (await fetch(url)).json();
    if (fitPick.mode === "skill") {
      const present = new Set(
        (fitState.last && fitState.last.skills ? fitState.last.skills : [])
          .map((s) => Number(s.skill_id))
          .concat((fitState.extraSkills || []).map((s) => Number(s.skill_id)))
      );
      list = list.filter((it) => !present.has(Number(it.type_id)));
    }
    res.innerHTML = list.length
      ? list.map((it) => {
          const level = Number(fitSkillLevels[Number(it.type_id)] || 0);
          const maxed = level >= 5;
          const uncertain = !maxed && fitUncertainSkillIds.has(Number(it.type_id));
          const upgrade = !maxed && !uncertain && fitUpgradeSkillIds && fitUpgradeSkillIds.has(Number(it.type_id));
          const cls = `res-item fit-pick${upgrade ? " upgrade-skill" : ""}${uncertain ? " uncertain-skill" : ""}${maxed ? " max-level" : ""}`;
          const tag = maxed
            ? '<span class="max-level-tag">已训练满5级</span>'
            : (uncertain ? '<span class="uncertain-tag">可能不准确，请检查</span>' : "");
          const disabled = maxed ? " disabled" : "";
          return `<button class="${cls}" data-type="${it.type_id}"${disabled}><img class="pick-ico" src="${fitIco(it.type_id)}" alt=""><span class="res-name">${escapeHtml(it.name)}</span>${tag}</button>`;
        }).join("")
      : '<span class="sub">没有可追加的技能（可能已在上线技能需求中）</span>';
  } catch (e) {
    res.innerHTML = `<span class="sub">搜索失败:${escapeHtml(e.message)}</span>`;
  }
}

function fitApplyPick(typeId) {
  if (fitPick.mode === "skill") {
    fitChooseSkillLevel(typeId);
    return;
  }
  if (fitPick.mode === "ship") {
    fitState.shipTypeId = typeId;
    $("#fit-picker").classList.add("hidden");
    fetch(`/api/fitting/item?type_id=${typeId}`).then((r) => r.json()).then((info) => {
      fitState.shipName = info.name;
      fitAnalyze();
    }).catch(() => fitAnalyze());
    return;
  }
  if (fitPick.mode === "charge") {
    const mod = fitItemAt(fitPick.slot, fitPick.index);
    if (mod) mod.charge_type_id = typeId;
    $("#fit-picker").classList.add("hidden");
    fitAnalyze();
    return;
  }
  if (fitPick.slot === "drone") {
    const exist = fitState.items.find((x) => x.slot === "drone" && x.type_id === typeId);
    if (exist) exist.qty += 1;
    else fitState.items.push({ type_id: typeId, qty: 1, slot: "drone", slot_index: fitNextIndex("drone"), charge_type_id: null });
  } else {
    const exist = fitItemAt(fitPick.slot, fitPick.index);
    if (exist) { exist.type_id = typeId; exist.charge_type_id = null; }
    else fitState.items.push({ type_id: typeId, qty: 1, slot: fitPick.slot, slot_index: fitPick.index, charge_type_id: null });
  }
  $("#fit-picker").classList.add("hidden");
  fitAnalyze();
}

function fitSlotToggle(slot, index) {
  const it = fitItemAt(slot, index);
  if (!it) return;
  it.active = !it.active;
  const btn = document.querySelector(`.fit-slot[data-slot="${slot}"][data-index="${index}"]`);
  if (btn) {
    btn.classList.toggle("active", !!it.active);
    btn.classList.toggle("passive", !!fitPassiveMap[it.type_id]);
  }
  // 启停只需要刷新数值，不重新创建全部槽位图标，避免明显卡顿。
  fitAnalyze({ visual: false });
}
function fitSlotClick(slot, index, typeId) {
  fitPick = { slot, index, mode: "add", moduleType: typeId || null };
  const el = $("#fit-picker");
  if (!typeId) { fitOpenPicker(); return; }
  // 已装配槽位:给出操作
  el.classList.remove("floating");
  el.classList.remove("hidden");
  const cur = fitItemAt(slot, index);
  const passive = cur && fitPassiveMap[cur.type_id];
  el.innerHTML = `<div class="fit-picker-head">槽位操作
      <button class="btn small" id="fit-picker-close">关闭</button></div>
    <div class="search-row">
      ${passive ? "" : `<button class="btn" id="fit-act-toggle">${cur && cur.active ? "停用" : "启用"}</button>`}
      <button class="btn" id="fit-act-replace">更换</button>
      <button class="btn" id="fit-act-charge">选择弹药</button>
      <button class="btn danger" id="fit-act-remove">移除</button>
    </div>`;
  document.getElementById("fit-picker-close").addEventListener("click", () => el.classList.add("hidden"));
  const toggleBtn = document.getElementById("fit-act-toggle");
  if (toggleBtn) toggleBtn.addEventListener("click", () => {
    fitSlotToggle(slot, index);
    el.classList.add("hidden");
  });
  document.getElementById("fit-act-replace").addEventListener("click", () => { fitPick.mode = "replace"; fitOpenPicker(); });
  document.getElementById("fit-act-remove").addEventListener("click", () => {
    const idx = fitState.items.findIndex((x) => x.slot === slot && x.slot_index === index);
    if (idx >= 0) fitState.items.splice(idx, 1);
    el.classList.add("hidden");
    fitAnalyze();
  });
  document.getElementById("fit-act-charge").addEventListener("click", async () => {
    const groups = await (await fetch(`/api/fitting/charges?type_id=${typeId}`)).json();
    if (!groups.length) { alert("该装备没有可装填的弹药"); return; }
    fitPick.mode = "charge";
    fitOpenPicker();
  });
}
/* ---------- 分页导航 ---------- */
function showPage(name) {
  document.querySelectorAll(".page").forEach((p) => {
    p.classList.toggle("hidden", p.id !== "page-" + name);
  });
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === name);
  });
  if (name === "market" && !marketUiLoaded) {
    marketUiLoaded = true;
    loadCategories();
  }
  if (name === "fitting") fitRefreshChars();
}

function initNav() {
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.addEventListener("click", () => showPage(a.dataset.page));
  });
  const fromHash = String(location.hash || "").replace("#", "");
  showPage(document.getElementById("page-" + fromHash) ? fromHash : "skills");
}
function bindGlobal() {
  const refreshAllBtn = document.getElementById("btn-refresh-all");
  if (refreshAllBtn) refreshAllBtn.addEventListener("click", onRefreshAll);
  const delAllBtn = document.getElementById("btn-delete-all");
  if (delAllBtn) delAllBtn.addEventListener("click", onDeleteAll);
  const showEventsOpt = document.getElementById("opt-show-events");
  if (showEventsOpt) {
    showEventsOpt.addEventListener("change", (ev) => {
      showExtractEvents = ev.target.checked;
      try { localStorage.setItem("showExtractEvents", showExtractEvents ? "1" : "0"); } catch (e) { /* ignore */ }
      renderHistory(lastHistoryRows, lastHistoryEvents);
    });
  }
  const hideNamesOpt = document.getElementById("opt-hide-names");
  if (hideNamesOpt) {
    hideNamesOpt.checked = hideNames;
    hideNamesOpt.addEventListener("change", (ev) => toggleHideNames(ev.target.checked));
  }
  const incSave = document.getElementById("btn-fin-inc-save");
  const expSave = document.getElementById("btn-fin-exp-save");
  if (incSave) incSave.addEventListener("click", () => saveFinance("income"));
  if (expSave) expSave.addEventListener("click", () => saveFinance("expense"));
  const cInc = document.getElementById("btn-fin-inc-cancel");
  const cExp = document.getElementById("btn-fin-exp-cancel");
  if (cInc) cInc.addEventListener("click", () => finCancelEdit("income"));
  if (cExp) cExp.addEventListener("click", () => finCancelEdit("expense"));
  const finMonth = document.getElementById("fin-month");
  const finAcc = document.getElementById("fin-filter-account");
  const finRef = document.getElementById("btn-fin-refresh");
  if (finMonth) finMonth.addEventListener("change", financeReload);
  if (finAcc) finAcc.addEventListener("change", financeReload);
  if (finRef) finRef.addEventListener("click", financeReload);
  const finEntriesBox = document.getElementById("fin-entries");
  if (finEntriesBox) {
    finEntriesBox.addEventListener("click", async (ev) => {
      const eb = ev.target.closest(".fin-edit-btn");
      if (eb) {
        const entry = finEntryById[Number(eb.dataset.id)];
        if (entry) startFinEdit(entry.kind, entry.id, entry);
        return;
      }
      const delb = ev.target.closest(".fin-del-btn");
      if (delb) {
        const id = Number(delb.dataset.id);
        if (!window.confirm(`确定删除这条记录(#${id})吗？`)) return;
        try {
          const res = await fetch(`/api/finance/entries/${id}`, { method: "DELETE" });
          if (!res.ok) throw new Error("HTTP " + res.status);
          financeReload();
        } catch (e) { finMsg("删除失败：" + e.message, true); }
      }
    });
  }
  const fitShipBtn = document.getElementById("btn-fit-ship-search");
  const fitShipQ = document.getElementById("fit-ship-q");
  if (fitShipBtn) fitShipBtn.addEventListener("click", fitShipSearch);
  if (fitShipQ) fitShipQ.addEventListener("keydown", (ev) => { if (ev.key === "Enter") fitShipSearch(); });
  const fitShipRes = document.getElementById("fit-ship-results");
  if (fitShipRes) fitShipRes.addEventListener("click", (ev) => {
    const b = ev.target.closest(".res-item");
    if (b) fitShipPick(Number(b.dataset.type));
  });
  const fitItemBtn = document.getElementById("btn-fit-item-search");
  const fitItemQ = document.getElementById("fit-item-q");
  if (fitItemBtn) fitItemBtn.addEventListener("click", fitItemSearch);
  if (fitItemQ) fitItemQ.addEventListener("keydown", (ev) => { if (ev.key === "Enter") fitItemSearch(); });
  const fitItemRes = document.getElementById("fit-item-results");
  if (fitItemRes) fitItemRes.addEventListener("click", (ev) => {
    const b = ev.target.closest(".res-item");
    if (b) fitItemAdd(Number(b.dataset.type));
  });
  const fitTabsBox = document.getElementById("fit-tabs");
  if (fitTabsBox) fitTabsBox.addEventListener("click", (ev) => {
    const exp = ev.target.closest(".fit-tab-export");
    if (exp) { fitExportTab(Number(exp.dataset.export)); return; }
    const close = ev.target.closest(".fit-tab-close");
    if (close) { fitTabsClose(Number(close.dataset.close)); return; }
    const add = ev.target.closest("[data-new]");
    if (add) { fitTabsNew(); return; }
    const tab = ev.target.closest(".fit-tab");
    if (tab && tab.dataset.tab !== undefined) fitTabsSwitch(Number(tab.dataset.tab));
  });
  const fitVisual = document.getElementById("fit-visual");
  if (fitVisual) fitVisual.addEventListener("click", (ev) => {
    if (ev.target.closest(".fit-ship-slot")) {
      fitPick = { slot: "ship", index: null, mode: "ship", moduleType: null };
      fitOpenPicker();
      return;
    }
    const b = ev.target.closest(".fit-slot");
    if (!b) return;
    if (ev.target.closest(".fit-slot-menu")) {
      fitSlotClick(b.dataset.slot, Number(b.dataset.index), b.dataset.type ? Number(b.dataset.type) : null);
      return;
    }
    if (b.dataset.type && !fitPassiveMap[Number(b.dataset.type)]) {
      fitSlotToggle(b.dataset.slot, Number(b.dataset.index));
    } else {
      fitSlotClick(b.dataset.slot, Number(b.dataset.index), b.dataset.type ? Number(b.dataset.type) : null);
    }
  });
  const fitPickerBox = document.getElementById("fit-picker");
  if (fitPickerBox) fitPickerBox.addEventListener("click", (ev) => {
    const group = ev.target.closest("[data-skill-group]");
    if (group) {
      fitOpenSkillList(Number(group.dataset.skillGroup), group.dataset.groupName || "");
      return;
    }
    const b = ev.target.closest(".fit-pick");
    if (b) fitApplyPick(Number(b.dataset.type));
  });
  const fitItemsBox = document.getElementById("fit-items");
  if (fitItemsBox) fitItemsBox.addEventListener("click", (ev) => {
    const b = ev.target.closest(".fit-remove");
    if (!b) return;
    const tid = Number(b.dataset.type), slot = b.dataset.slot;
    const idx = fitState.items.findIndex((x) => x.type_id === tid && (x.slot || "") === slot);
    if (idx >= 0) { fitState.items.splice(idx, 1); fitAnalyze(); }
  });
  const fitModeSwitch = document.getElementById("fit-mode-switch");
  if (fitModeSwitch) fitModeSwitch.addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-mode]");
    if (b) fitSetSkillMode(b.dataset.mode);
  });
  const fitCharButtons = document.getElementById("fit-char-buttons");
  if (fitCharButtons) fitCharButtons.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-char-filter]");
    if (!btn) return;
    const value = btn.dataset.charFilter || "";
    fitSetCharFilter(value, value !== "");
  });
  const fitCharSel = document.getElementById("fit-char");
  if (fitCharSel) fitCharSel.addEventListener("change", () => {
    try { localStorage.setItem(FIT_CHAR_STORAGE_KEY, fitCharSel.value); } catch (e) { /* ignore */ }
    fitUpdateAnalysisCharLabel();
    fitAnalyze();
  });
  const btnFitImport = document.getElementById("btn-fit-import");
  const btnFitClear = document.getElementById("btn-fit-clear");
  const btnFitSave = document.getElementById("btn-fit-save");
  const btnFitDelete = document.getElementById("btn-fit-delete");
  const fitSavedList = document.getElementById("fit-saved-list");
  const btnFitCopy = document.getElementById("btn-fit-copy");
  if (btnFitImport) btnFitImport.addEventListener("click", fitImportEft);
  if (btnFitClear) btnFitClear.addEventListener("click", fitClearAll);
  if (btnFitSave) btnFitSave.addEventListener("click", fitSave);
  if (btnFitDelete) btnFitDelete.addEventListener("click", fitDeleteOne);
  if (fitSavedList) fitSavedList.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-saved-id]");
    if (btn) fitLoadOne(Number(btn.dataset.savedId));
  });
  if (btnFitCopy) btnFitCopy.addEventListener("click", fitCopyMissing);
  const btnFitOptimize = document.getElementById("btn-fit-optimize");
  if (btnFitOptimize) btnFitOptimize.addEventListener("click", fitOptimizePlan);
  const fitResetPolicy = document.getElementById("fit-reset-policy");
  if (fitResetPolicy) fitResetPolicy.addEventListener("change", () => {
    try { localStorage.setItem(FIT_RESET_POLICY_STORAGE_KEY, String(fitResetPolicyHours())); } catch (e) { /* ignore */ }
  });
  const btnFitAddSkill = document.getElementById("btn-fit-add-skill");
  if (btnFitAddSkill) btnFitAddSkill.addEventListener("click", fitOpenSkillGroupPicker);
  const fitExtraSkillsBox = document.getElementById("fit-extra-skills");
  if (fitExtraSkillsBox) fitExtraSkillsBox.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-remove-extra]");
    if (!btn) return;
    fitState.extraSkills = (fitState.extraSkills || []).filter((s) => String(s.skill_id) !== String(btn.dataset.removeExtra));
    fitTabsSave();
    fitRenderExtraSkills();
    fitAnalyze();
  });
  const btnList = document.getElementById("btn-view-list");
  const btnCards = document.getElementById("btn-view-cards");
  if (btnList) btnList.addEventListener("click", () => setView("list"));
  if (btnCards) btnCards.addEventListener("click", () => setView("cards"));
  const listBox = document.getElementById("list-view");
  if (listBox) {
    listBox.addEventListener("click", (ev) => {
      const acc = ev.target.closest(".acc-name");
      if (acc && acc.dataset.key) {
        renameAccount(acc);
        return;
      }
      const tr = ev.target.closest(".c-row");
      if (tr) openDetail(Number(tr.dataset.id));
    });
  }
  const searchBtn = document.getElementById("btn-search-item");
  const itemQ = document.getElementById("item-q");
  if (searchBtn) searchBtn.addEventListener("click", doSearchItem);
  if (itemQ) itemQ.addEventListener("keydown", (ev) => { if (ev.key === "Enter") doSearchItem(); });
  const resultsBox = document.getElementById("item-results");
  if (resultsBox) {
    resultsBox.addEventListener("click", (ev) => {
      const b = ev.target.closest(".res-item");
      if (b) selectItem(Number(b.dataset.type));
    });
  }
  const catSel = document.getElementById("cat-select");
  const catQ = document.getElementById("cat-q");
  const btnCat = document.getElementById("btn-cat-load");
  const catMore = document.getElementById("cat-more");
  const catBox = document.getElementById("cat-items");
  if (catSel) catSel.addEventListener("change", () => loadCatItems(true));
  if (catQ) catQ.addEventListener("keydown", (ev) => { if (ev.key === "Enter") loadCatItems(true); });
  if (btnCat) btnCat.addEventListener("click", () => loadCatItems(true));
  if (catMore) catMore.addEventListener("click", () => loadCatItems(false));
  if (catBox) {
    catBox.addEventListener("click", (ev) => {
      const b = ev.target.closest(".res-item");
      if (b) selectItem(Number(b.dataset.type), "#cat-detail");
    });
  }
  // 磁贴点击（事件委托）
  $("#cards").addEventListener("click", (ev) => {
    const acc = ev.target.closest(".grp-acc-name");
    if (acc && acc.dataset.key !== undefined && acc.dataset.key !== "") {
      renameAccount(acc);
      return;
    }
    const tile = ev.target.closest(".tile");
    if (tile) openDetail(Number(tile.dataset.id));
  });
  // 键盘回车打开
  $("#cards").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.classList.contains("tile")) {
      openDetail(Number(ev.target.dataset.id));
    }
  });
  // 弹窗关闭
  document.querySelector(".modal-close").addEventListener("click", closeDetail);
  document.querySelector(".modal-backdrop").addEventListener("click", closeDetail);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeDetail();
  });
}

(function init() {
  initNav();
  initViewMode();
  initHideNames();
  initShowEvents();
  bindGlobal();
  applyViewButtons();
  const msg = new URLSearchParams(location.search).get("msg");
  if (msg) {
    const text = decodeURIComponent(msg);
    showBanner(text, /失败|未配置|错误|无效|已失效|校验/.test(text));
  }
  load();
  loadHistory();
  financeInit();
  fitRefreshChars();
  fitInitResetPolicy();
  fitTabsLoad();
  fitApplyPlanResetPolicy(fitState.optimizedPlan);
  fitTabsRender();
  fitLoadSaved();
  fitAnalyze();
  if (fitOptimizedPlanValid()) fitSyncOptimizedPlan();
  loadMarket(); // 市场行情：首次打开立即刷新一次
  setTimeout(() => rotatePass(), 1500); // 打开页面后：若距上次已满30分钟才刷新一轮
  setInterval(() => rotatePass(), ROTATE_POLL_MS); // 每分钟检查是否到点
  setInterval(() => {
    const page = document.getElementById("page-fitting");
    if (!document.hidden && page && !page.classList.contains("hidden")) fitLoadSaved();
  }, 60 * 1000); // 配装队列匹配状态：每分钟刷新
  setInterval(loadMarket, MARKET_REFRESH_MS); // 市场行情：每 15 分钟
  setInterval(projectTick, 60 * 1000); // 实时推算：每 60 秒推进
})();














































































