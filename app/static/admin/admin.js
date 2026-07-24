(() => {
  "use strict";

  const ids = [
    "loginView", "dashboardView", "username", "password", "signin", "loginError",
    "logout", "content", "globalError", "toastStack", "confirmDialog",
    "confirmTitle", "confirmMessage", "confirmForm", "confirmComment",
    "confirmResumeRow", "confirmResumeFrom", "confirmCancel", "confirmSubmit",
    "sourcesN", "degradedN", "reviewsN", "eventsN", "runsN",
  ];
  const el = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));
  const missing = ids.filter((id) => !el[id]);
  if (missing.length) {
    throw new Error(`管理后台缺少 DOM 节点: ${missing.join(", ")}`);
  }

  const state = {
    token: sessionStorage.getItem("token") || "",
    role: null,
    tab: "sources",
    sources: [],
    reviews: [],
    events: [],
    workflows: [],
    reports: [],
    reviewFilters: { status: "pending", objectType: "", reason: "", query: "" },
    confirmAction: null,
    busy: false,
  };

  const decisionNames = {
    approve: "批准",
    return: "退回",
    return_for_supplement: "退回补充",
    downgrade_to_fact_card: "降级为事实卡片",
    reject: "拒绝",
  };
  const transitionNames = {
    approved: "批准",
    published: "发布",
    withdrawn: "撤回",
    needs_revision: "要求修订",
    needs_review: "提交审核",
  };
  const statusNames = {
    active: "正常", disabled: "已禁用", degraded: "降级",
    pending: "待处理", decided: "已决定", running: "运行中",
    waiting_review: "等待审核", failed: "失败", succeeded: "成功",
    verified: "已验证", conflicted: "有冲突", unverified: "未验证",
    needs_review: "待审核", review_required: "要求审核",
    approved: "已批准", published: "已发布", withdrawn: "已撤回",
    needs_revision: "待修订", rejected: "已拒绝",
  };

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
    })[char]);
  }

  function attr(value) {
    return esc(value);
  }

  function textStatus(value) {
    return statusNames[value] || value || "–";
  }

  function statusClass(value) {
    if (["active", "succeeded", "published", "verified", "approved"].includes(value)) return "ok";
    if (["failed", "withdrawn", "rejected", "conflicted", "disabled"].includes(value)) return "bad";
    return "warn";
  }

  function tag(value, cssClass = "") {
    return `<span class="tag ${esc(cssClass)}">${esc(textStatus(value))}</span>`;
  }

  function table(headers, rows) {
    return `<table><thead><tr>${headers.map((header) => `<th>${esc(header)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
  }

  function json(value) {
    return esc(JSON.stringify(value ?? null, null, 2));
  }

  function listData(value) {
    if (Array.isArray(value)) return value;
    if (value && Array.isArray(value.items)) return value.items;
    return [];
  }

  function setLoading(label = "加载中…") {
    el.content.innerHTML = `<div class="sub" role="status">${esc(label)}</div>`;
  }

  function empty(label) {
    return `<div class="sub">${esc(label)}</div>`;
  }

  function clearError() {
    el.globalError.textContent = "";
  }

  function showError(error, prefix = "") {
    const message = error instanceof Error ? error.message : String(error);
    el.globalError.textContent = `${prefix}${message}`;
  }

  function toast(message, kind = "ok") {
    const item = document.createElement("div");
    item.className = `panel ${kind === "error" ? "error" : ""}`;
    item.setAttribute("role", kind === "error" ? "alert" : "status");
    item.textContent = message;
    el.toastStack.appendChild(item);
    window.setTimeout(() => item.remove(), 4500);
  }

  function parseRole(token) {
    try {
      const part = token.split(".")[1];
      if (!part) return null;
      const normalized = part.replace(/-/g, "+").replace(/_/g, "/");
      const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
      const payload = JSON.parse(decodeURIComponent(Array.from(
        atob(padded),
        (char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`,
      ).join("")));
      return typeof payload.role === "string" ? payload.role : null;
    } catch (_) {
      return null;
    }
  }

  function showLogin(message = "") {
    state.token = "";
    state.role = null;
    sessionStorage.removeItem("token");
    el.loginView.classList.remove("hidden");
    el.dashboardView.classList.add("hidden");
    el.logout.classList.add("hidden");
    el.loginError.textContent = message;
  }

  function showDashboard() {
    el.loginView.classList.add("hidden");
    el.dashboardView.classList.remove("hidden");
    el.logout.classList.remove("hidden");
    el.loginError.textContent = "";
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
    const response = await fetch(path, { ...options, headers });
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      body = null;
    }
    if (response.status === 401) {
      showLogin("登录已过期，请重新登录");
      throw new Error("AUTH_REQUIRED");
    }
    if (!response.ok) {
      const code = body?.error?.code || body?.detail || `HTTP_${response.status}`;
      const message = body?.error?.message || code;
      const error = new Error(message);
      error.code = code;
      error.status = response.status;
      error.requestId = body?.meta?.request_id;
      throw error;
    }
    if (!body || !Object.prototype.hasOwnProperty.call(body, "data")) {
      throw new Error("API_RESPONSE_INVALID");
    }
    return body.data;
  }

  function send(path, method, body) {
    return api(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  const post = (path, body) => send(path, "POST", body);
  const patch = (path, body) => send(path, "PATCH", body);

  function formatDate(value) {
    if (!value) return "–";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN");
  }

  function taskAge(value) {
    const created = new Date(value).getTime();
    if (!Number.isFinite(created)) return "–";
    const seconds = Math.max(0, Math.floor((Date.now() - created) / 1000));
    if (seconds < 60) return `${seconds} 秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`;
    return `${Math.floor(seconds / 86400)} 天`;
  }

  function canManageSources() {
    return state.role === "admin";
  }

  function allowedReportTransitions(report) {
    const transitions = {
      needs_review: ["approved"],
      review_required: ["approved"],
      approved: ["published", "needs_revision"],
      published: ["withdrawn"],
      needs_revision: ["approved"],
    };
    return (transitions[report.status] || []).filter((target) => {
      if (["approved", "needs_revision"].includes(target)) return ["reviewer", "admin"].includes(state.role);
      if (["published", "withdrawn"].includes(target)) return ["publisher", "admin"].includes(state.role);
      return false;
    });
  }

  function actionButton(action, label, data = {}, cssClass = "ghost small") {
    const attrs = Object.entries(data).map(([key, value]) => ` data-${key}="${attr(value)}"`).join("");
    return `<button type="button" class="${attr(cssClass)}" data-action="${attr(action)}"${attrs}>${esc(label)}</button>`;
  }

  async function loadOverview() {
    const [sourcesResult, reviewsResult, eventsResult, workflowsResult] = await Promise.allSettled([
      api("/api/v1/sources"),
      api("/api/v1/reviews?status_filter=pending"),
      api("/api/v1/events"),
      api("/api/v1/workflows?limit=200"),
    ]);
    if (sourcesResult.status === "fulfilled") state.sources = listData(sourcesResult.value);
    if (reviewsResult.status === "fulfilled") state.reviews = listData(reviewsResult.value);
    if (eventsResult.status === "fulfilled") state.events = listData(eventsResult.value);
    if (workflowsResult.status === "fulfilled") state.workflows = listData(workflowsResult.value);
    el.sourcesN.textContent = String(state.sources.length);
    el.degradedN.textContent = String(state.sources.filter((item) => item.status !== "active").length);
    el.reviewsN.textContent = String(state.reviews.length);
    el.eventsN.textContent = String(state.events.length);
    el.runsN.textContent = String(state.workflows.filter(
      (item) => ["running", "pending", "waiting_review"].includes(item.status),
    ).length);
  }

  async function refresh() {
    clearError();
    setLoading();
    try {
      await loadOverview();
      await renderTab(state.tab);
    } catch (error) {
      if (error.message !== "AUTH_REQUIRED") {
        showError(error, "加载失败：");
        el.content.innerHTML = empty("无法加载当前模块");
      }
    }
  }

  async function renderTab(tab) {
    state.tab = tab;
    document.querySelectorAll("[data-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === tab);
    });
    if (tab === "sources") renderSources();
    else if (tab === "events") renderEvents();
    else if (tab === "reviews") await renderReviews();
    else if (tab === "reports") await renderReports();
    else if (tab === "workflows") renderWorkflows();
    else if (tab === "briefs") renderBriefs();
    else if (tab === "audit") await renderAudit();
    else el.content.innerHTML = empty("未知模块");
  }

  function renderSources() {
    const sourceActions = canManageSources()
      ? `<div class="actions">${actionButton("seed-sources", "写入种子源", {}, "")}${actionButton("toggle-source-create", "新建来源")}</div>
        <div id="sourceCreateBox" class="panel hidden">
          <form id="sourceCreateForm">
            <div class="form-row">
              <input name="code" required pattern="[a-z0-9_-]+" placeholder="code (a-z0-9_-)">
              <input name="name" required placeholder="名称">
              <select name="trust_tier"><option>S</option><option selected>A</option><option>B</option><option>C</option></select>
              <input name="feed_url" required placeholder="feed_url">
              <input name="allowed_domains" required placeholder="domains，逗号分隔">
              <input name="rate_limit_per_minute" type="number" min="1" max="600" value="10">
              <button type="submit">创建</button>
            </div>
          </form>
        </div>`
      : "";
    const rows = state.sources.map((source) => `<tr>
      <td>${esc(source.name)}<div class="sub small">${esc(source.code)} · ${esc(source.id)}</div><div class="sub small">${esc(source.feed_url)}</div></td>
      <td>${tag(source.trust_tier)} ${tag(source.adapter_type || "rss")}</td>
      <td>${tag(source.status, statusClass(source.status))}</td>
      <td>${esc(source.consecutive_failures ?? 0)}<div class="sub small">${esc(source.last_error_code || "")}</div></td>
      <td>${esc(source.rate_limit_per_minute ?? "–")}/m</td>
      <td class="small">${esc(formatDate(source.next_retry_at))}</td>
      <td><div class="actions">${canManageSources() ? `${actionButton("sync-source", "同步", { id: source.id })}
        ${source.status === "active" ? actionButton("set-source-status", "禁用", { id: source.id, status: "disabled" }) : actionButton("set-source-status", "启用", { id: source.id, status: "active" })}` : "–"}</div></td>
    </tr>`);
    el.content.innerHTML = `<h2>来源健康</h2><div class="sub">同步、启停、种子源与适配器配置</div>
      ${sourceActions}${rows.length ? table(["名称", "等级/适配器", "状态", "失败", "限速", "下次重试", "操作"], rows) : empty("暂无来源")}`;
  }

  function renderEvents() {
    const rows = state.events.map((event) => `<tr class="row" data-action="open-event" data-id="${attr(event.id)}">
      <td>${tag(event.event_type)}</td><td>${esc(event.title)}</td>
      <td class="small">${(event.entity_ids || []).map(esc).join(", ") || "–"}</td>
      <td>${tag(event.status, statusClass(event.status))}</td>
      <td>${esc(Number(event.importance || 0).toFixed(2))}</td>
      <td class="small">${esc(formatDate(event.occurred_at))}</td>
    </tr>`);
    el.content.innerHTML = `<h2>事件列表</h2><div class="sub">查看按验证状态分组的 Claim 与 Evidence 轨道</div>
      ${rows.length ? table(["类型", "标题", "实体", "状态", "重要度", "发生时间"], rows) : empty("暂无事件")}`;
  }

  async function showEvent(id) {
    setLoading("加载事件详情…");
    try {
      const event = await api(`/api/v1/events/${encodeURIComponent(id)}`);
      const claims = event.claims || [];
      const order = ["verified", "conflicted", "unverified"];
      const groups = order.map((status) => {
        const group = claims.filter((claim) => claim.status === status);
        const cards = group.map((claim) => `<div class="panel" style="margin-top:8px">
          <div>${tag(claim.status, statusClass(claim.status))} <strong>${esc(claim.subject_text)}</strong> · ${esc(claim.predicate)}</div>
          <div class="sub small">对象：${json(claim.object_value)} · 置信度 ${esc(Number(claim.confidence || 0).toFixed(2))} · as_of ${esc(formatDate(claim.as_of))}</div>
          <div class="actions">${(claim.evidence_ids || []).length
            ? actionButton("toggle-evidence-track", `展开 Evidence（${claim.evidence_ids.length}）`, { claim: claim.id, evidence: claim.evidence_ids.join(",") })
            : '<span class="sub">无关联 Evidence</span>'}</div>
          <div id="evidence-${attr(claim.id)}" class="hidden"></div>
        </div>`).join("");
        return `<section><h3>${esc(textStatus(status))}（${group.length}）</h3>${cards || empty("此分组暂无 Claim")}</section>`;
      }).join("");
      el.content.innerHTML = `${actionButton("back-events", "← 返回事件列表", {}, "ghost")}
        <h2>${esc(event.title)}</h2>
        <div class="sub">${tag(event.event_type)} ${tag(event.status, statusClass(event.status))} 重要度 ${esc(Number(event.importance || 0).toFixed(2))} · 版本 ${esc(event.version)}</div>
        <div style="margin-top:12px">${groups}</div>
        <div class="panel" style="margin-top:12px"><h3>关联报告</h3>
          ${event.fact_card_id ? actionButton("open-report", event.fact_card_id, { id: event.fact_card_id }) : empty("无已生成报告")}
        </div>`;
    } catch (error) {
      showError(error, "加载事件详情失败：");
      el.content.innerHTML = empty("事件详情不可用");
    }
  }

  async function toggleEvidenceTrack(button) {
    const target = document.getElementById(`evidence-${button.dataset.claim}`);
    if (!target) return;
    if (target.dataset.loaded === "true") {
      target.classList.toggle("hidden");
      button.textContent = target.classList.contains("hidden") ? `展开 Evidence（${target.children.length}）` : "收起 Evidence";
      return;
    }
    target.classList.remove("hidden");
    target.innerHTML = empty("加载 Evidence…");
    button.disabled = true;
    try {
      const idsToLoad = (button.dataset.evidence || "").split(",").filter(Boolean);
      const results = await Promise.allSettled(idsToLoad.map((id) => api(`/api/v1/evidence/${encodeURIComponent(id)}`)));
      target.innerHTML = results.map((result, index) => {
        if (result.status === "rejected") {
          return `<div class="panel error" style="margin-top:8px">Evidence ${esc(idsToLoad[index])}：${esc(result.reason.message)}</div>`;
        }
        const evidence = result.value;
        const content = String(evidence.document_content || "");
        const excerpt = String(evidence.excerpt || "");
        let highlighted = esc(content);
        if (excerpt && content.includes(excerpt)) {
          highlighted = content.split(excerpt).map(esc).join(`<span class="highlight">${esc(excerpt)}</span>`);
        }
        return `<article class="panel" style="margin-top:8px">
          <h4>Evidence ${esc(evidence.id)}</h4>
          <div class="sub">${esc(evidence.document_title || "无标题")} · ${esc(evidence.document_url || evidence.document_id)}</div>
          <div class="sub small">locator：${json(evidence.locator)} · ${esc(evidence.locator_type)} / ${esc(evidence.extraction_version)}</div>
          <h4>摘录</h4><pre style="white-space:pre-wrap">${esc(excerpt)}</pre>
          <h4>来源正文（高亮摘录）</h4><pre style="white-space:pre-wrap">${highlighted || '<span class="muted">无正文</span>'}</pre>
        </article>`;
      }).join("");
      target.dataset.loaded = "true";
      button.textContent = "收起 Evidence";
    } finally {
      button.disabled = false;
    }
  }

  function reviewFilterUrl() {
    const params = new URLSearchParams();
    if (state.reviewFilters.status) params.set("status_filter", state.reviewFilters.status);
    return `/api/v1/reviews${params.size ? `?${params}` : ""}`;
  }

  async function renderReviews(reload = true) {
    setLoading("加载审核队列…");
    try {
      if (reload) state.reviews = listData(await api(reviewFilterUrl()));
      const filters = state.reviewFilters;
      const query = filters.query.toLowerCase();
      const reviews = state.reviews.filter((task) =>
        (!filters.objectType || task.object_type === filters.objectType)
        && (!filters.reason || task.reason_code === filters.reason)
        && (!query || [task.id, task.object_id, task.reason_code].some((value) => String(value || "").toLowerCase().includes(query))));
      const reasons = [...new Set(state.reviews.map((task) => task.reason_code).filter(Boolean))].sort();
      const rows = reviews.map((task) => `<tr class="row" data-action="open-review" data-id="${attr(task.id)}">
        <td>${tag(task.object_type)}<div class="sub small">${esc(task.object_id)}</div></td>
        <td>${tag(task.reason_code, "warn")}${task.resume_from ? `<div class="sub small">resume: ${esc(task.resume_from)}</div>` : ""}</td>
        <td class="small">${(task.allowed_decisions || []).map((decision) => tag(decisionNames[decision] || decision)).join(" ")}</td>
        <td class="small">${esc(taskAge(task.created_at))}<div class="sub">${esc(formatDate(task.created_at))}</div></td>
        <td><div class="actions">${(task.allowed_decisions || []).map((decision) =>
          actionButton("review-decision", decisionNames[decision] || decision, { id: task.id, decision })).join("")}</div></td>
      </tr>`);
      el.content.innerHTML = `<h2>审核队列</h2><div class="sub">筛选任务、查看上下文并作出决定</div>
        <form id="reviewFilterForm"><div class="form-row">
          <select name="status"><option value="">全部状态</option><option value="pending" ${filters.status === "pending" ? "selected" : ""}>待处理</option><option value="decided" ${filters.status === "decided" ? "selected" : ""}>已决定</option></select>
          <select name="objectType"><option value="">全部对象</option><option value="report" ${filters.objectType === "report" ? "selected" : ""}>报告</option><option value="workflow" ${filters.objectType === "workflow" ? "selected" : ""}>工作流</option></select>
          <select name="reason"><option value="">全部原因</option>${reasons.map((reason) => `<option value="${attr(reason)}" ${filters.reason === reason ? "selected" : ""}>${esc(reason)}</option>`).join("")}</select>
          <input name="query" value="${attr(filters.query)}" placeholder="任务、对象或原因">
          <button type="submit" class="ghost">筛选</button>
        </div></form>
        ${rows.length ? table(["对象", "原因", "允许决定", "任务年龄", "操作"], rows) : empty("没有符合条件的审核任务")}`;
    } catch (error) {
      showError(error, "加载审核队列失败：");
      el.content.innerHTML = empty("审核队列不可用");
    }
  }

  function reportSummary(report) {
    return `<h3>报告上下文</h3>
      <div><strong>${esc(report.title)}</strong> ${tag(report.status, statusClass(report.status))} ${tag(report.report_type)}</div>
      <p style="white-space:pre-wrap">${esc(report.summary)}</p>
      <div class="sub">事件：${esc(report.event_id)} · 版本 v${esc(report.version)} · as_of ${esc(formatDate(report.as_of))}</div>
      <div class="sub">Claims：${(report.claim_ids || []).map(esc).join(", ") || "–"}</div>
      ${report.disclaimer ? `<div class="sub">${esc(report.disclaimer)}</div>` : ""}`;
  }

  function workflowSummary(workflow) {
    return `<h3>工作流上下文</h3>
      <div>${tag(workflow.status, statusClass(workflow.status))} 当前节点：${esc(workflow.current_node || "–")} · 状态版本 v${esc(workflow.state_version)}</div>
      <div class="sub">事件：${esc(workflow.event_id)} · as_of ${esc(formatDate(workflow.as_of))} · 错误：${esc(workflow.error_code || "–")}</div>
      <h4>Blackboard</h4><pre>${json(workflow.blackboard)}</pre>`;
  }

  async function showReview(id) {
    setLoading("加载审核详情与对象上下文…");
    try {
      const task = await api(`/api/v1/reviews/${encodeURIComponent(id)}`);
      let object = null;
      let contextError = "";
      try {
        if (task.object_type === "report") object = await api(`/api/v1/reports/${encodeURIComponent(task.object_id)}`);
        else if (task.object_type === "workflow") object = await api(`/api/v1/workflows/${encodeURIComponent(task.object_id)}`);
      } catch (error) {
        contextError = error.message;
      }
      const context = contextError
        ? `<div class="error">对象上下文加载失败：${esc(contextError)}</div>`
        : task.object_type === "report" && object
          ? `${reportSummary(object)}${actionButton("open-report", "打开完整报告", { id: object.id })}`
          : task.object_type === "workflow" && object
            ? `${workflowSummary(object)}${actionButton("open-workflow", "打开完整工作流", { id: object.id })}`
            : empty("不支持的审核对象类型");
      el.content.innerHTML = `${actionButton("back-reviews", "← 返回审核队列", {}, "ghost")}
        <h2>审核任务 ${esc(task.id)}</h2>
        <div class="sub">${tag(task.object_type)} ${tag(task.status, statusClass(task.status))} ${tag(task.reason_code, "warn")} · 任务年龄 ${esc(taskAge(task.created_at))}</div>
        <div class="panel" style="margin-top:12px">
          <div>对象：${esc(task.object_id)}</div>
          <div class="sub">创建：${esc(formatDate(task.created_at))} · resume_from：${esc(task.resume_from || "–")} · blackboard_v：${esc(task.blackboard_version ?? "–")}</div>
          <div class="actions">${task.status === "pending" ? (task.allowed_decisions || []).map((decision) =>
            actionButton("review-decision", decisionNames[decision] || decision, { id: task.id, decision })).join("") : empty("该任务已处理")}</div>
        </div>
        <div class="panel" style="margin-top:12px">${context}</div>`;
    } catch (error) {
      showError(error, "加载审核详情失败：");
      el.content.innerHTML = empty("审核详情不可用");
    }
  }

  async function renderReports() {
    setLoading("加载报告列表…");
    try {
      state.reports = listData(await api("/api/v1/reports"));
      const eventTitles = new Map(state.events.map((event) => [event.id, event.title]));
      const rows = state.reports.map((report) => `<tr class="row" data-action="open-report" data-id="${attr(report.id)}">
        <td class="small">${esc(report.id)}</td><td>${esc(report.title)}</td>
        <td>${tag(report.status, statusClass(report.status))}</td><td>${tag(report.report_type)}</td>
        <td>v${esc(report.version)}</td><td class="small">${esc(eventTitles.get(report.event_id) || report.event_id)}</td>
        <td class="small">${esc(formatDate(report.as_of))}</td>
      </tr>`);
      el.content.innerHTML = `<h2>报告版本</h2><div class="sub">结构化详情、角色受控状态流转与版本差异</div>
        ${rows.length ? table(["报告 ID", "标题", "状态", "类型", "版本", "事件", "as_of"], rows) : empty("暂无报告")}`;
    } catch (error) {
      showError(error, "加载报告列表失败：");
      el.content.innerHTML = empty("报告列表不可用");
    }
  }

  async function showReport(id) {
    setLoading("加载报告详情…");
    try {
      const report = await api(`/api/v1/reports/${encodeURIComponent(id)}`);
      let siblings = state.reports.filter((item) => item.event_id === report.event_id);
      if (!siblings.length) {
        try {
          siblings = listData(await api(`/api/v1/events/${encodeURIComponent(report.event_id)}/reports`));
        } catch (_) {
          siblings = [report];
        }
      }
      const transitions = allowedReportTransitions(report);
      const options = siblings.filter((item) => item.id !== report.id).map(
        (item) => `<option value="${attr(item.id)}">v${esc(item.version)} · ${esc(textStatus(item.status))} · ${esc(item.id)}</option>`,
      ).join("");
      el.content.innerHTML = `${actionButton("back-reports", "← 返回报告列表", {}, "ghost")}
        <h2>${esc(report.title)}</h2>
        <div class="sub">${tag(report.status, statusClass(report.status))} ${tag(report.report_type)} v${esc(report.version)} · ${esc(report.id)}</div>
        <div class="panel" style="margin-top:12px">${reportSummary(report)}
          <div class="sub">取代版本：${esc(report.supersedes_report_id || "–")} · 变更原因：${esc(report.change_reason || "–")}</div>
        </div>
        <div class="panel" style="margin-top:12px"><h3>状态流转</h3>
          <div class="actions">${transitions.length ? transitions.map((target) =>
            actionButton("report-transition", transitionNames[target] || target, { id: report.id, status: target }, "")).join("") : empty("当前角色和状态无可用流转")}</div>
        </div>
        <div class="panel" style="margin-top:12px"><h3>版本差异</h3>
          <div class="form-row"><select id="reportDiffOther">${options || '<option value="">无其他版本</option>'}</select>
          ${actionButton("run-report-diff", "对比", { id: report.id }, "ghost")}</div>
          <div id="reportDiffOutput"></div>
        </div>`;
      const diffButton = el.content.querySelector('[data-action="run-report-diff"]');
      if (diffButton && !options) diffButton.disabled = true;
    } catch (error) {
      showError(error, "加载报告详情失败：");
      el.content.innerHTML = empty("报告详情不可用");
    }
  }

  async function runReportDiff(id) {
    const select = document.getElementById("reportDiffOther");
    const output = document.getElementById("reportDiffOutput");
    if (!select?.value || !output) return;
    output.innerHTML = empty("对比中…");
    try {
      const diff = await api(`/api/v1/reports/${encodeURIComponent(id)}/diff/${encodeURIComponent(select.value)}`);
      const rows = Object.entries(diff.changes || {}).map(([field, change]) =>
        `<tr><td>${esc(field)}</td><td class="diff-from"><pre>${json(change.from)}</pre></td><td class="diff-to"><pre>${json(change.to)}</pre></td></tr>`);
      output.innerHTML = rows.length ? table(["字段", "From", "To"], rows) : empty("两个版本无差异");
    } catch (error) {
      output.innerHTML = `<div class="error">${esc(error.message)}</div>`;
    }
  }

  function renderWorkflows() {
    const rows = state.workflows.map((workflow) => `<tr class="row" data-action="open-workflow" data-id="${attr(workflow.id)}">
      <td class="small">${esc(workflow.id)}</td><td>${tag(workflow.status, statusClass(workflow.status))}</td>
      <td>${esc(workflow.current_node || "–")}</td><td>v${esc(workflow.state_version)}</td>
      <td class="small">${esc(workflow.event_id)}</td><td class="small">${esc(workflow.error_code || "–")}</td>
    </tr>`);
    el.content.innerHTML = `<h2>研究工作流</h2><div class="sub">预算账本、节点尝试与恢复</div>
      ${rows.length ? table(["Workflow", "状态", "当前节点", "版本", "事件", "错误"], rows) : empty("暂无工作流运行")}`;
  }

  async function showWorkflow(id) {
    setLoading("加载工作流详情…");
    try {
      const [workflow, budgetResult, attemptsResult] = await Promise.all([
        api(`/api/v1/workflows/${encodeURIComponent(id)}`),
        api(`/api/v1/workflows/${encodeURIComponent(id)}/budget`).catch(() => []),
        api(`/api/v1/workflows/${encodeURIComponent(id)}/attempts`).catch(() => []),
      ]);
      const budget = listData(budgetResult);
      const attempts = listData(attemptsResult);
      const canResume = ["reviewer", "admin"].includes(state.role) && ["waiting_review", "failed"].includes(workflow.status);
      el.content.innerHTML = `${actionButton("back-workflows", "← 返回工作流", {}, "ghost")}
        <h2>工作流 ${esc(workflow.id)}</h2>
        <div class="sub">${tag(workflow.status, statusClass(workflow.status))} 节点 ${esc(workflow.current_node || "–")} · v${esc(workflow.state_version)} · ${esc(workflow.budget_profile || "")}</div>
        ${canResume ? `<div class="panel" style="margin-top:12px"><h3>恢复</h3><div class="actions">
          ${actionButton("workflow-resume", "恢复运行", { id: workflow.id, mode: "resume" }, "")}
          ${actionButton("workflow-resume", "降级事实卡片", { id: workflow.id, mode: "fact" })}
        </div></div>` : ""}
        <div class="split" style="margin-top:12px">
          <div class="panel"><h3>预算账本（${budget.length}）</h3>${budget.length ? table(["节点", "维度", "类型", "量"], budget.slice().reverse().slice(0, 40).map((entry) =>
            `<tr><td>${esc(entry.node_name || "–")}</td><td>${esc(entry.dimension)}</td><td>${tag(entry.entry_type)}</td><td>${esc(entry.amount)}</td></tr>`)) : empty("无记录")}</div>
          <div class="panel"><h3>节点尝试（${attempts.length}）</h3>${attempts.length ? table(["节点", "#", "状态", "错误"], attempts.map((attempt) =>
            `<tr><td>${esc(attempt.node_name)}</td><td>${esc(attempt.attempt_no)}</td><td>${tag(attempt.status, statusClass(attempt.status))}</td><td>${esc(attempt.error_code || "–")}</td></tr>`)) : empty("无记录")}</div>
        </div>
        <div class="panel" style="margin-top:12px">${workflowSummary(workflow)}</div>`;
    } catch (error) {
      showError(error, "加载工作流失败：");
      el.content.innerHTML = empty("工作流详情不可用");
    }
  }

  function renderBriefs() {
    const today = new Date().toISOString().slice(0, 10);
    el.content.innerHTML = `<h2>每日 Top-N 简报</h2>
      <form id="briefForm"><div class="form-row"><input type="date" name="date" value="${attr(today)}" required><button type="submit" class="ghost">加载</button></div></form>
      <div id="briefBody">${empty("请选择日期")}</div>`;
  }

  async function loadBrief(date) {
    const body = document.getElementById("briefBody");
    if (!body) return;
    body.innerHTML = empty("加载简报…");
    try {
      const brief = await api(`/api/v1/briefs/daily?date=${encodeURIComponent(date)}`);
      const rows = (brief.entries || []).map((entry) => `<tr class="row" data-action="open-report" data-id="${attr(entry.report_id)}">
        <td>${esc(entry.rank)}</td><td>${esc(entry.title)}</td><td>${(entry.entity_ids || []).map(esc).join(", ") || "–"}</td>
        <td>${tag(entry.urgency)}</td><td>${esc(Number(entry.score || 0).toFixed(3))}</td><td>${esc(Number(entry.importance || 0).toFixed(2))}</td>
      </tr>`);
      body.innerHTML = `<div class="sub">${esc(brief.brief_date)} · 候选 ${esc(brief.candidate_count)} · 规则 ${esc(brief.rule_version)}</div>
        ${rows.length ? table(["排名", "标题", "实体", "紧迫度", "分数", "重要度"], rows) : empty("该日无简报条目")}`;
    } catch (error) {
      body.innerHTML = `<div class="error">简报加载失败：${esc(error.message)}</div>`;
    }
  }

  async function renderAudit() {
    setLoading("加载审计记录…");
    try {
      const logs = listData(await api("/api/v1/audit-logs"));
      const rows = logs.map((log) => `<tr><td>${esc(formatDate(log.created_at))}</td><td>${tag(log.action)}</td>
        <td>${esc(log.object_type)} · ${esc(log.object_id || "–")}</td><td><pre style="max-height:80px">${json(log.details)}</pre></td></tr>`);
      el.content.innerHTML = `<h2>审计记录</h2><div class="sub">登录、审核、发布、来源与工作流操作轨迹</div>
        ${rows.length ? table(["时间", "操作", "对象", "详情"], rows) : empty("暂无审计记录")}`;
    } catch (error) {
      showError(error, "加载审计记录失败：");
      el.content.innerHTML = empty("审计记录不可用");
    }
  }

  function openConfirm(config) {
    state.confirmAction = config;
    el.confirmTitle.textContent = config.title;
    el.confirmMessage.textContent = config.message;
    el.confirmComment.value = config.defaultComment || "";
    el.confirmComment.required = config.commentRequired !== false;
    el.confirmComment.closest("label")?.classList.toggle("hidden", config.hideComment === true);
    el.confirmResumeRow.classList.toggle("hidden", !config.showResume);
    el.confirmResumeFrom.value = config.resumeFrom || "";
    el.confirmSubmit.textContent = config.submitLabel || "确认";
    if (typeof el.confirmDialog.showModal === "function") el.confirmDialog.showModal();
    else el.confirmDialog.setAttribute("open", "");
  }

  function closeConfirm() {
    state.confirmAction = null;
    if (typeof el.confirmDialog.close === "function") el.confirmDialog.close();
    else el.confirmDialog.removeAttribute("open");
  }

  async function submitConfirm() {
    const action = state.confirmAction;
    if (!action || state.busy) return;
    const comment = el.confirmComment.value.trim();
    if (action.commentRequired !== false && !comment) {
      el.confirmComment.setCustomValidity("请填写备注");
      el.confirmComment.reportValidity();
      return;
    }
    el.confirmComment.setCustomValidity("");
    state.busy = true;
    el.confirmSubmit.disabled = true;
    try {
      if (action.type === "review") {
        const body = { decision: action.decision, comment };
        if (action.showResume && el.confirmResumeFrom.value.trim()) body.resume_from = el.confirmResumeFrom.value.trim();
        await post(`/api/v1/reviews/${encodeURIComponent(action.id)}/decision`, body);
        closeConfirm();
        toast(`审核决定已提交：${decisionNames[action.decision] || action.decision}`);
        await refresh();
      } else if (action.type === "transition") {
        await post(`/api/v1/reports/${encodeURIComponent(action.id)}/transition`, { status: action.status });
        closeConfirm();
        toast(`报告已${transitionNames[action.status] || action.status}`);
        await showReport(action.id);
      } else if (action.type === "workflow") {
        const factOnly = action.mode === "fact";
        await post(`/api/v1/workflows/${encodeURIComponent(action.id)}/resume`, {
          trigger: factOnly ? "downgrade_fact_only" : "budget_resume",
          resume_from: factOnly ? null : (el.confirmResumeFrom.value.trim() || null),
          budget_adjust: factOnly ? null : { model_calls: 10, tool_calls: 20 },
          force_fact_only: factOnly,
          reason: comment,
        });
        closeConfirm();
        toast(factOnly ? "已提交降级事实卡片" : "工作流已恢复");
        await showWorkflow(action.id);
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      state.busy = false;
      el.confirmSubmit.disabled = false;
    }
  }

  function requestReviewDecision(id, decision) {
    const task = state.reviews.find((item) => item.id === id);
    const needsResume = ["return", "return_for_supplement"].includes(decision) && task?.object_type === "workflow";
    openConfirm({
      type: "review",
      id,
      decision,
      title: `审核决定：${decisionNames[decision] || decision}`,
      message: "该操作会立即提交到后端并受后端状态与权限校验约束。",
      defaultComment: `admin-ui: ${decision}`,
      showResume: needsResume,
      resumeFrom: task?.resume_from || "",
      submitLabel: decisionNames[decision] || "提交决定",
    });
  }

  async function handleContentClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target || !el.content.contains(target)) return;
    const action = target.dataset.action;
    try {
      if (action === "open-event") await showEvent(target.dataset.id);
      else if (action === "back-events") renderEvents();
      else if (action === "toggle-evidence-track") await toggleEvidenceTrack(target);
      else if (action === "open-review") await showReview(target.dataset.id);
      else if (action === "back-reviews") await renderReviews(false);
      else if (action === "review-decision") requestReviewDecision(target.dataset.id, target.dataset.decision);
      else if (action === "open-report") await showReport(target.dataset.id);
      else if (action === "back-reports") await renderReports();
      else if (action === "run-report-diff") await runReportDiff(target.dataset.id);
      else if (action === "report-transition") openConfirm({
        type: "transition",
        id: target.dataset.id,
        status: target.dataset.status,
        title: `确认${transitionNames[target.dataset.status] || target.dataset.status}`,
        message: "状态流转将创建新报告版本；后端会再次校验当前状态和角色。",
        commentRequired: false,
        hideComment: true,
        submitLabel: transitionNames[target.dataset.status] || "确认流转",
      });
      else if (action === "open-workflow") await showWorkflow(target.dataset.id);
      else if (action === "back-workflows") {
        state.workflows = listData(await api("/api/v1/workflows?limit=200"));
        renderWorkflows();
      } else if (action === "workflow-resume") openConfirm({
        type: "workflow",
        id: target.dataset.id,
        mode: target.dataset.mode,
        title: target.dataset.mode === "fact" ? "确认降级为事实卡片" : "确认恢复工作流",
        message: "恢复操作会沿用后端预算、节点和状态约束。",
        defaultComment: target.dataset.mode === "fact" ? "admin-ui-fact-only" : "admin-ui-resume",
        showResume: target.dataset.mode !== "fact",
        submitLabel: "确认执行",
      });
      else if (action === "seed-sources") {
        target.disabled = true;
        const result = await post("/api/v1/sources/seed", {});
        toast(`新增种子源：${result.inserted ?? 0}`);
        await refresh();
      } else if (action === "toggle-source-create") {
        document.getElementById("sourceCreateBox")?.classList.toggle("hidden");
      } else if (action === "sync-source") {
        target.disabled = true;
        target.textContent = "同步中…";
        await post(`/api/v1/sources/${encodeURIComponent(target.dataset.id)}/sync`, {});
        toast("来源同步完成");
        await refresh();
      } else if (action === "set-source-status") {
        await patch(`/api/v1/sources/${encodeURIComponent(target.dataset.id)}`, { status: target.dataset.status });
        toast(target.dataset.status === "active" ? "来源已启用" : "来源已禁用");
        await refresh();
      }
    } catch (error) {
      if (error.message !== "AUTH_REQUIRED") {
        showError(error, "操作失败：");
        toast(error.message, "error");
      }
      target.disabled = false;
    }
  }

  async function handleContentSubmit(event) {
    event.preventDefault();
    const form = event.target;
    try {
      if (form.id === "sourceCreateForm") {
        const data = new FormData(form);
        await post("/api/v1/sources", {
          code: String(data.get("code") || "").trim(),
          name: String(data.get("name") || "").trim(),
          trust_tier: data.get("trust_tier"),
          feed_url: String(data.get("feed_url") || "").trim(),
          allowed_domains: String(data.get("allowed_domains") || "").split(/[,，\s]+/).filter(Boolean),
          rate_limit_per_minute: Number(data.get("rate_limit_per_minute")) || 10,
        });
        toast("来源已创建");
        await refresh();
      } else if (form.id === "reviewFilterForm") {
        const data = new FormData(form);
        state.reviewFilters = {
          status: String(data.get("status") || ""),
          objectType: String(data.get("objectType") || ""),
          reason: String(data.get("reason") || ""),
          query: String(data.get("query") || "").trim(),
        };
        await renderReviews(true);
      } else if (form.id === "briefForm") {
        await loadBrief(String(new FormData(form).get("date") || ""));
      }
    } catch (error) {
      if (error.message !== "AUTH_REQUIRED") {
        showError(error, "提交失败：");
        toast(error.message, "error");
      }
    }
  }

  async function signIn() {
    el.signin.disabled = true;
    el.loginError.textContent = "";
    try {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ username: el.username.value, password: el.password.value }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.error?.message || body?.error?.code || body?.detail || `HTTP_${response.status}`);
      if (!body?.data?.access_token) throw new Error("LOGIN_RESPONSE_INVALID");
      state.token = body.data.access_token;
      state.role = parseRole(state.token);
      sessionStorage.setItem("token", state.token);
      showDashboard();
      await refresh();
    } catch (error) {
      el.loginError.textContent = error.message;
    } finally {
      el.signin.disabled = false;
    }
  }

  el.signin.addEventListener("click", signIn);
  el.password.addEventListener("keydown", (event) => {
    if (event.key === "Enter") signIn();
  });
  el.logout.addEventListener("click", () => showLogin());
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      state.tab = button.dataset.tab;
      refresh();
    });
  });
  el.content.addEventListener("click", handleContentClick);
  el.content.addEventListener("submit", handleContentSubmit);
  el.confirmCancel.addEventListener("click", closeConfirm);
  el.confirmForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitConfirm();
  });
  el.confirmDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeConfirm();
  });
  window.addEventListener("error", (event) => showError(event.error || event.message, "JS 错误："));
  window.addEventListener("unhandledrejection", (event) => showError(event.reason, "未捕获错误："));

  if (state.token) {
    state.role = parseRole(state.token);
    showDashboard();
    refresh();
  } else {
    showLogin();
  }
})();
