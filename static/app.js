// 共识护航 AI Banking Agent 前端
let state = { token: null, user: null, currentPlanId: null, currentSpaceId: 1, sub: "tx" };

const $ = (id) => document.getElementById(id);

function yuan(cents) {
  if (cents == null) return "—";
  return "¥" + (cents / 100).toFixed(2);
}

function pickAccount(u) {
  $("login-user").value = u;
  document.querySelectorAll(".acc").forEach(b => b.classList.toggle("selected", b.dataset.user === u));
}

async function doLogin(e) {
  e.preventDefault();
  const username = $("login-user").value;
  const password = $("login-pass").value;
  try {
    const r = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password })
    });
    const data = await r.json();
    if (!r.ok) { $("login-err").textContent = data.detail || "登录失败"; return; }
    state.token = data.token;
    state.user = data.user;
    $("login-err").textContent = "";
    $("login-panel").classList.add("hidden");
    $("app").classList.remove("hidden");
    await refreshMe();
    addMsg("agent", `您好，${state.user.display_name}。我是「共识护航」，可以帮您结算共同开销、处理转账/账单/理财/卡片/订阅。`);
    refreshSpace();
  } catch (err) {
    $("login-err").textContent = "网络错误";
  }
}

function logout() {
  fetch("/api/auth/logout", { method: "POST", headers: authHeaders() }).catch(() => {});
  state.token = null;
  $("app").classList.add("hidden");
  $("login-panel").classList.remove("hidden");
}

function authHeaders() {
  return { "Authorization": `Bearer ${state.token}`, "Content-Type": "application/json" };
}

async function refreshMe() {
  const r = await fetch("/api/me", { headers: authHeaders() });
  const data = await r.json();
  $("user-name").textContent = data.user.display_name;
  $("user-balance").textContent = yuan(data.balance_cents);
  $("mode-badge").textContent = data.offline_mode ? "离线测试模式" : "真实模型模式";
  state.currentSpaceId = (data.spaces && data.spaces[0]) ? data.spaces[0].id : null;
}

function switchTab(t) {
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  document.querySelectorAll(".tabpane").forEach(p => p.classList.toggle("active", p.id === "tab-" + t));
  if (t === "space") refreshSpace();
  if (t === "private") refreshPrivate();
  if (t === "audit") refreshAudit();
}

function switchSub(s) {
  state.sub = s;
  document.querySelectorAll(".subtab").forEach(b => b.classList.toggle("active", b.dataset.sub === s));
  refreshPrivate();
}

function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  div.appendChild(b);
  $("chat-log").appendChild(div);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

async function sendChat() {
  const text = $("chat-text").value.trim();
  if (!text) return;
  addMsg("user", text);
  $("chat-text").value = "";
  const r = await fetch("/api/chat", { method: "POST", headers: authHeaders(),
    body: JSON.stringify({ message: text, space_id: state.currentSpaceId }) });
  const data = await r.json();
  if (data.clarification) {
    let q = data.clarification.question;
    if (data.clarification.options) {
      q += "\n" + data.clarification.options.map((o, i) => `${i + 1}. ${o.label}`).join("\n");
    }
    addMsg("agent", q);
  } else {
    addMsg("agent", data.reply || "已处理");
    renderPlan(data.plan);
  }
}

function renderPlan(plan) {
  const panel = $("plan-panel");
  if (!plan) { panel.innerHTML = ""; return; }
  if (plan.plan_id != null) state.currentPlanId = plan.plan_id;
  let html = `<div class="plan-card"><h3>任务进度（状态：${plan.plan_status}）</h3>`;
  if (plan.nodes) {
    plan.nodes.forEach(n => {
      html += `<div class="node-row"><span class="node-status st-${n.status}">${n.status}</span>
        <span>${n.node_key}</span><span class="k" style="color:#999">${n.tool_name}</span></div>`;
    });
  }
  if (plan.waits && plan.waits.length) {
    plan.waits.forEach(w => {
      html += `<div class="wait-actions">`;
      if (w.type === "confirm") {
        html += `<span>需确认：${JSON.stringify(w.detail)}</span>
          <button class="primary" onclick="confirmNode('${w.node_key}',true)">确认执行</button>`;
      } else if (w.type === "auth") {
        html += `<span class="level-chip lv-red">强验证</span>
          <span>模拟短信验证码：<b>${w.challenge.mock_code}</b>（仅模拟环境）</span>
          <input id="otp-${w.node_key}" placeholder="输入验证码">
          <button class="primary" onclick="authNode('${w.node_key}', ${w.challenge.challenge_id}, ${w.authorization_id}, '${w.operation_id}')">验证并执行</button>`;
      }
      html += `</div>`;
    });
  }
  html += `</div>`;
  panel.innerHTML = html;
}

async function continueNode(nodeKey, body) {
  const r = await fetch(`/api/plans/${state.currentPlanId}/continue`, {
    method: "POST", headers: authHeaders(),
    body: JSON.stringify({ node_key: nodeKey, ...body })
  });
  const data = await r.json();
  if (data.status === "failed" || data.error) {
    addMsg("agent", "执行失败：" + (data.error || data.detail));
    return;
  }
  renderPlan(data);
  if (data.plan_status === "completed") addMsg("agent", "计划已完成。");
}

function confirmNode(nodeKey, confirmed) {
  continueNode(nodeKey, { confirmed });
}

function authNode(nodeKey, challengeId, authorizationId, operationId) {
  const code = $(`otp-${nodeKey}`).value;
  continueNode(nodeKey, { challenge_id: challengeId, challenge_code: code,
    operation_id: operationId, authorization_id: authorizationId });
}

async function refreshSpace() {
  if (!state.currentSpaceId) return;
  const r = await fetch(`/api/space/${state.currentSpaceId}/progress`, { headers: authHeaders() });
  const data = await r.json();
  const names = { 1: "王淑芬", 2: "李伟", 3: "陈晨" };
  let html = `<div class="card"><h3>本月结算汇总（合计 ${yuan(data.total_cents)}）</h3>
    <table><tr><th>成员</th><th>应付</th><th>已付</th><th>剩余</th></tr>`;
  for (const [uid, m] of Object.entries(data.members)) {
    html += `<tr><td>${names[uid] || "成员" + uid}</td><td>${yuan(m.due_cents)}</td>
      <td>${yuan(m.paid_cents)}</td><td>${yuan(m.remaining_cents)}</td></tr>`;
  }
  html += `</table></div>`;
  $("space-progress").innerHTML = html;
}

async function refreshPrivate() {
  const s = state.sub;
  const el = $("private-content");
  if (s === "tx") {
    const r = await fetch("/api/tools/run", { method: "POST", headers: authHeaders(),
      body: JSON.stringify({ tool_name: "transaction_list", params: {} }) });
    const data = await r.json();
    const txs = data.result.transactions;
    el.innerHTML = `<div class="card"><table><tr><th>类型</th><th>金额</th><th>商户/备注</th><th>状态</th></tr>` +
      txs.map(t => `<tr><td>${t.tx_type}</td><td>${yuan(t.amount_cents)}</td>
        <td>${t.merchant || t.memo}</td><td>${t.status}</td></tr>`).join("") + `</table></div>`;
  } else if (s === "sub") {
    const r = await fetch("/api/tools/run", { method: "POST", headers: authHeaders(),
      body: JSON.stringify({ tool_name: "list_subscriptions", params: {} }) });
    const data = await r.json();
    const subs = data.result.subscriptions || [];
    el.innerHTML = subs.map(s => `<div class="card"><h3>${s.merchant}</h3>
      <div class="kv"><span class="k">金额</span><span>${yuan(s.amount_cents)}/${s.cycle}</span>
      <span class="k">状态</span><span>${s.status}</span></div></div>`).join("") || "<p>无订阅</p>";
  } else if (s === "card") {
    const r = await fetch("/api/tools/run", { method: "POST", headers: authHeaders(),
      body: JSON.stringify({ tool_name: "card_list", params: {} }) });
    const data = await r.json();
    const cards = data.result.cards || [];
    el.innerHTML = cards.map(c => `<div class="card"><h3>${c.masked_number}</h3>
      <div class="kv"><span class="k">状态</span><span>${c.status}</span>
      <span class="k">限额</span><span>${yuan(c.limit_cents)}</span></div></div>`).join("") || "<p>无卡片</p>";
  } else if (s === "wealth") {
    const r = await fetch("/api/tools/run", { method: "POST", headers: authHeaders(),
      body: JSON.stringify({ tool_name: "holding_query", params: {} }) });
    const r2 = await fetch("/api/tools/run", { method: "POST", headers: authHeaders(),
      body: JSON.stringify({ tool_name: "product_list", params: {} }) });
    const holdings = (await r.json()).result.holdings;
    const products = (await r2.json()).result.products;
    el.innerHTML = `<div class="card"><h3>持仓</h3>` + (holdings.map(h => {
      const p = products.find(x => x.id === h.product_id);
      return `<div>${p ? p.name : h.product_id}：${yuan(h.amount_cents)}</div>`;
    }).join("") || "无持仓") + `</div>
      <div class="card"><h3>产品（合成模拟）</h3><table><tr><th>产品</th><th>风险</th><th>期限</th><th>到账</th></tr>` +
      products.map(p => `<tr><td>${p.name}</td><td>${p.risk_level}</td><td>${p.term_days}天</td>
        <td>${p.redemption_rule}</td></tr>`).join("") + `</table></div>`;
  } else if (s === "evidence") {
    const r = await fetch("/api/tools/run", { method: "POST", headers: authHeaders(),
      body: JSON.stringify({ tool_name: "list_evidence", params: {} }) });
    const data = await r.json();
    const cards = data.result.evidence || [];
    el.innerHTML = cards.map(c => `<div class="card"><h3>${c.merchant_alias} · ${yuan(c.amount_cents)}</h3>
      <div class="kv"><span class="k">状态</span><span>${c.status}</span>
      <span class="k">说明</span><span>${c.resolution}</span>
      <span class="k">反馈</span><span>${c.user_feedback || "—"}</span></div></div>`).join("") || "<p>无证据卡</p>";
  }
}

async function refreshAudit() {
  const r = await fetch("/api/audit", { headers: authHeaders() });
  const data = await r.json();
  const logs = data.audit || [];
  $("audit-list").innerHTML = logs.map(l => `<div class="card">
    <div class="kv"><span class="k">动作</span><span>${l.action}</span>
    <span class="k">意图</span><span>${l.intent_summary || "—"}</span>
    <span class="k">结果</span><span>${l.success ? "成功" : "失败"}</span>
    <span class="k">时间</span><span>${l.created_at}</span></div></div>`).join("") || "<p>暂无审计记录</p>";
}
