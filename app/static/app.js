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

function initHideNames() {
  try { hideNames = localStorage.getItem("hideNames") === "1"; } catch (e) { /* ignore */ }
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
}

let rotBusy = false;
const ROTATE_GAP_MS = 60 * 1000;

async function rotatePass() {
  if (rotBusy || document.hidden) return;
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
        st.textContent = `轮询刷新中 ${i + 1}/${targets.length}`;
        st.classList.remove("hidden");
        setTimeout(() => st.classList.add("hidden"), 2500);
      }
      await sleepMs(300);
    }
  } finally {
    rotBusy = false;
  }
  if (!document.hidden) setTimeout(rotatePass, ROTATE_GAP_MS);
}
async function onRefreshAll() {
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
  const hideNamesOpt = document.getElementById("opt-hide-names");
  if (hideNamesOpt) {
    hideNamesOpt.checked = hideNames;
    hideNamesOpt.addEventListener("change", (ev) => toggleHideNames(ev.target.checked));
  }
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
  bindGlobal();
  applyViewButtons();
  const msg = new URLSearchParams(location.search).get("msg");
  if (msg) {
    const text = decodeURIComponent(msg);
    showBanner(text, /失败|未配置|错误|无效|已失效|校验/.test(text));
  }
  load();
  loadMarket(); // 市场行情：首次打开立即刷新一次
  setTimeout(() => rotatePass(), 1200); // 先显示旧数据，随后逐个轮流刷新
  setInterval(() => rotatePass(), AUTO_REFRESH_MS); // 兜底：每 5 分钟跑一轮
  setInterval(loadMarket, MARKET_REFRESH_MS); // 市场行情：每 15 分钟
  setInterval(projectTick, 60 * 1000); // 实时推算：每 60 秒推进
})();








































