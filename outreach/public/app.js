const SESSION_KEY = "blk_bridge_session";

let config = null;
let session = null;
let state = null;
let selectedTargets = new Set();
let selectedDraftId = null;
let pipelineSearch = "";
let pipelineStatus = "all";

const VIEW_META = {
  overview: ["Overview", "See what needs attention and choose the next best action."],
  pipeline: ["Firm pipeline", "Move selected firms through status, research, contacts, and drafting."],
  drafts: ["Draft review", "Inspect the complete email record before making a human approval decision."],
  manual: ["Manual queue", "Resolve missing facts and weak evidence that Bridge will not guess."],
  "cross-owner": ["Cross-owner exception", "Create one logged, time-limited exception for another owner lane."],
};

// Mirrors SUBJECT_BY_STATUS in drafts/generate.py, used only for drafts stored
// before subjects were templated. tests/test_phase_g_dashboard.py asserts the
// two stay in step.
const SUBJECT_FALLBACK = {
  cold_prospect: "BLK Capital Management | Partnership for the 2026-27 Cycle",
  existing_partner: "BLK Capital Management | Renewing Our Partnership for 2026-27",
  lapsed_partner: "BLK Capital Management | Revisiting Our Partnership for 2026-27",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const humanize = (value) => String(value || "none").replaceAll("_", " ");

function emptyState(title, copy) {
  return `<div class="empty-state"><div class="empty-state-content"><span class="empty-icon">✦</span><strong>${escapeHtml(title)}</strong><p>${escapeHtml(copy)}</p></div></div>`;
}

function setAuthenticated(active) {
  const loginView = $("#login-view");
  const appView = $("#app-view");
  loginView.hidden = active;
  loginView.classList.toggle("hidden", active);
  appView.hidden = !active;
  appView.classList.toggle("hidden", !active);
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 5200);
}

function setLoading(active) {
  $("#loading-bar").classList.toggle("hidden", !active);
  document.body.setAttribute("aria-busy", String(active));
  $("#refresh-button").disabled = active;
}

async function loadConfig() {
  const response = await fetch("/api/config", { cache: "no-store" });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(data?.detail || "Dashboard configuration is unavailable.");
  }
  if (!data?.supabase_url || !data?.supabase_publishable_key) {
    throw new Error("Dashboard configuration is unavailable. Refresh this page or contact the administrator.");
  }
  config = data;
  $("#login-submit").disabled = false;
  $("#login-submit").textContent = "Sign in to Bridge";
}

function saveSession(value) {
  session = value;
  if (value) localStorage.setItem(SESSION_KEY, JSON.stringify(value));
  else localStorage.removeItem(SESSION_KEY);
}

async function signIn(email, password) {
  if (!config) throw new Error("Dashboard configuration has not loaded. Refresh this page and try again.");
  const response = await fetch(`${config.supabase_url}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: {
      apikey: config.supabase_publishable_key,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, password }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error_description || data.msg || "Sign in failed.");
  saveSession(data);
}

async function refreshSession() {
  if (!session?.refresh_token) throw new Error("Session expired.");
  const response = await fetch(`${config.supabase_url}/auth/v1/token?grant_type=refresh_token`, {
    method: "POST",
    headers: {
      apikey: config.supabase_publishable_key,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ refresh_token: session.refresh_token }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error("Session expired. Sign in again.");
  saveSession(data);
}

async function api(path, options = {}, retry = true) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session?.access_token || ""}`,
      ...(options.headers || {}),
    },
  });
  if (response.status === 401 && retry) {
    await refreshSession();
    return api(path, options, false);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status}).`);
  return data;
}

async function loadState() {
  setLoading(true);
  try {
    state = await api("/api/state");
    renderAll();
    $("#sync-status").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  } finally {
    setLoading(false);
  }
}

function pill(value, label = null) {
  const css = String(value || "").toLowerCase().replaceAll(" ", "_");
  return `<span class="pill ${escapeHtml(css)}">${escapeHtml(label || humanize(value))}</span>`;
}

function researchByTarget() {
  return new Map((state?.research || []).map((item) => [item.target_id, item]));
}

function contactsByTarget() {
  const grouped = new Map();
  for (const item of state?.contacts || []) {
    if (!grouped.has(item.target_id)) grouped.set(item.target_id, []);
    grouped.get(item.target_id).push(item);
  }
  return grouped;
}

function draftsByTarget() {
  const grouped = new Map();
  for (const item of state?.drafts || []) {
    if (!grouped.has(item.target_id)) grouped.set(item.target_id, []);
    grouped.get(item.target_id).push(item);
  }
  return grouped;
}

function renderMetrics() {
  const c = state.counts || {};
  const metrics = [
    [c.targets ?? 0, "Firms in your lane", "Targets", "01", ""],
    [(state.research || []).length, "Research artifacts", "Evidence", "02", "positive"],
    [(state.contacts || []).length, "Verified contacts", "Contacts", "03", "positive"],
    [c.pending_review ?? 0, "Drafts awaiting review", "Approval", "04", (c.pending_review ?? 0) ? "attention" : "positive"],
  ];
  $("#metrics").innerHTML = metrics.map(([value, label, eyebrow, icon, tone]) => `
    <article class="metric ${tone}"><div class="metric-head"><p class="eyebrow">${escapeHtml(eyebrow)}</p><span class="metric-icon">${icon}</span></div><strong>${value}</strong><span>${escapeHtml(label)}</span></article>
  `).join("");
}

function recommendedAction() {
  const c = state.counts || {};
  if ((c.pending_review ?? 0) > 0) {
    return {
      title: `${c.pending_review} draft${c.pending_review === 1 ? " is" : "s are"} waiting for your decision`,
      copy: "Review the email body and its evidence before approving or rejecting it.",
      label: "Review pending drafts",
      view: "drafts",
    };
  }
  if ((c.approved ?? 0) > 0) {
    return {
      title: `${c.approved} approved draft${c.approved === 1 ? " is" : "s are"} ready for you to send`,
      copy: "Open the draft, copy it into Gmail, send it, then record it as sent.",
      label: "Open approved drafts",
      view: "drafts",
    };
  }
  if ((c.manual_queue ?? 0) > 0) {
    return {
      title: `${c.manual_queue} item${c.manual_queue === 1 ? " needs" : "s need"} human follow-up`,
      copy: "Resolve the documented gaps so those firms can return to the standard workflow.",
      label: "Open manual queue",
      view: "manual",
    };
  }
  if ((state.targets || []).some((target) => !target.contact_status || target.contact_status === "unknown")) {
    return {
      title: "Start by deriving relationship status",
      copy: "Select the newly added firms and establish their relationship status before research and contact discovery.",
      label: "Open firm pipeline",
      view: "pipeline",
    };
  }
  return {
    title: "Your lane is organized and ready to advance",
    copy: "Open the firm pipeline to research targets, verify contacts, or generate the next review draft.",
    label: "Continue in the pipeline",
    view: "pipeline",
  };
}

function renderOverview() {
  renderMetrics();
  const c = state.counts || {};
  const action = recommendedAction();
  $("#welcome-title").textContent = action.title;
  $("#next-action-copy").textContent = action.copy;
  $("#primary-next-action").textContent = action.label;
  $("#primary-next-action").dataset.view = action.view;

  const stages = [
    ["01", "Targets", "Firms in lane", c.targets ?? 0],
    ["02", "Evidence", "Research artifacts", (state.research || []).length],
    ["03", "Contacts", "Verified people", (state.contacts || []).length],
    ["04", "Approved", "Ready to send", c.approved ?? 0],
    ["05", "Sent", "Emails out", c.sent ?? 0],
  ];
  $("#workflow-progress").innerHTML = stages.map(([number, name, label, count]) => `
    <div class="workflow-stage"><span class="stage-number">${number}</span><div><strong>${escapeHtml(name)}</strong><span>${escapeHtml(label)}</span></div><b>${count}</b></div>
  `).join("");

  const balance = state.hunter_balance || {};
  $("#hunter-balance").innerHTML = balance.remaining == null
    ? `<strong>Unavailable</strong><span>Contact discovery is paused until the provider responds.</span>`
    : `<strong>${balance.remaining}</strong><span>search credits remaining · ${balance.used} of ${balance.available} used</span>`;

  const research = researchByTarget();
  const top = [...state.targets].sort((left, right) => Number(left.priority || 99) - Number(right.priority || 99)).slice(0, 6);
  $("#overview-targets").innerHTML = top.length ? top.map((target) => {
    const artifact = research.get(target.id);
    return `<div class="compact-row"><div><strong>${escapeHtml(target.firm)}</strong><span>${escapeHtml(target.domain || "Domain required")} · Priority ${escapeHtml(target.priority || "not set")}</span></div><div class="compact-actions">${pill(target.contact_status)} ${artifact ? pill(artifact.confidence) : pill("not researched")}</div></div>`;
  }).join("") : emptyState("No firms yet", "Add your first batch to begin the evidence workflow.");

  const drafts = state.drafts.filter((draft) => draft.status === "pending_review").slice(0, 5);
  $("#overview-drafts").innerHTML = drafts.length ? drafts.map((draft) => `
    <div class="compact-row"><div><strong>${escapeHtml(draft.firm)}</strong><span>${escapeHtml(draft.contact?.name || "No contact")}</span></div><button class="text-button open-draft" data-id="${draft.id}">Review draft</button></div>
  `).join("") : emptyState("Review queue is clear", "New validator-passing drafts will appear here for a human decision.");
  $$(".open-draft").forEach((button) => button.addEventListener("click", () => {
    selectedDraftId = button.dataset.id;
    switchView("drafts");
    renderDrafts();
  }));
}

function renderPipeline() {
  const research = researchByTarget();
  const contacts = contactsByTarget();
  const drafts = draftsByTarget();
  const query = pipelineSearch.trim().toLowerCase();
  const visibleTargets = state.targets.filter((target) => {
    const matchesQuery = !query || `${target.firm} ${target.domain || ""}`.toLowerCase().includes(query);
    const status = target.contact_status || "unknown";
    return matchesQuery && (pipelineStatus === "all" || status === pipelineStatus);
  });
  $("#pipeline-body").innerHTML = visibleTargets.length ? visibleTargets.map((target) => {
    const artifact = research.get(target.id);
    const targetContacts = contacts.get(target.id) || [];
    const targetDrafts = drafts.get(target.id) || [];
    const latestDraft = targetDrafts[0];
    const gate = target.hunter_gate || { status: "unknown", reason: "Gate not evaluated." };
    return `<tr>
      <td><input class="target-check" type="checkbox" data-id="${target.id}" ${selectedTargets.has(target.id) ? "checked" : ""}></td>
      <td class="firm-cell"><strong>${escapeHtml(target.firm)}</strong><span>${escapeHtml(target.domain || "Domain required")} · ${escapeHtml(target.owner)}</span></td>
      <td>${pill(target.contact_status)}</td>
      <td>${artifact ? pill(artifact.confidence) : pill("not researched")}</td>
      <td><span title="${escapeHtml(gate.reason)}">${pill(gate.status)}</span></td>
      <td>${targetContacts.length ? `${targetContacts.length} verified` : `<span class="quiet">None yet</span>`}</td>
      <td>${latestDraft ? pill(latestDraft.status) : "None"}</td>
      <td><button class="text-button create-draft" data-id="${target.id}">Create draft</button></td>
    </tr>`;
  }).join("") : `<tr><td colspan="8">${emptyState(state.targets.length ? "No matching firms" : "Your pipeline is empty", state.targets.length ? "Clear the search or change the status filter." : "Add a batch above to begin.")}</td></tr>`;

  $$(".target-check").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) selectedTargets.add(input.dataset.id);
    else selectedTargets.delete(input.dataset.id);
    updateSelectionControls(visibleTargets);
  }));
  $$(".create-draft").forEach((button) => button.addEventListener("click", () => openDraftDialog(button.dataset.id)));
  $("#select-all").onchange = (event) => {
    for (const target of visibleTargets) {
      if (event.target.checked) selectedTargets.add(target.id);
      else selectedTargets.delete(target.id);
    }
    renderPipeline();
  };
  $("#pipeline-results-summary").textContent = `Showing ${visibleTargets.length} of ${state.targets.length} firms in your lane`;
  updateSelectionControls(visibleTargets);
}

function updateSelectionControls(visibleTargets = state?.targets || []) {
  const count = selectedTargets.size;
  $("#selection-count").textContent = `${count} selected`;
  ["#derive-selected", "#research-selected", "#contacts-selected"].forEach((selector) => { $(selector).disabled = count === 0; });
  const selectedVisible = visibleTargets.filter((target) => selectedTargets.has(target.id)).length;
  $("#select-all").checked = visibleTargets.length > 0 && selectedVisible === visibleTargets.length;
  $("#select-all").indeterminate = selectedVisible > 0 && selectedVisible < visibleTargets.length;
}

function renderDrafts() {
  const drafts = state.drafts || [];
  const pendingCount = drafts.filter((draft) => draft.status === "pending_review").length;
  $("#draft-queue-summary").textContent = `${pendingCount} pending decision${pendingCount === 1 ? "" : "s"} · ${drafts.length} total drafts`;
  if (!selectedDraftId && drafts.length) selectedDraftId = drafts[0].id;
  $("#draft-list").innerHTML = drafts.length ? drafts.map((draft) => `
    <button class="draft-card ${draft.id === selectedDraftId ? "active" : ""}" data-id="${draft.id}">
      <span class="draft-card-top"><strong>${escapeHtml(draft.firm)}</strong>${pill(draft.status)}</span>
      <span>${escapeHtml(draft.contact?.name || "Relationship contact")} · ${escapeHtml(humanize(draft.contact_status))}</span>
    </button>
  `).join("") : emptyState("No drafts yet", "Create a draft from an eligible firm in the pipeline.");
  $$(".draft-card").forEach((card) => card.addEventListener("click", () => {
    selectedDraftId = card.dataset.id;
    renderDrafts();
  }));

  const draft = drafts.find((item) => item.id === selectedDraftId);
  if (!draft) {
    $("#draft-detail").className = "panel draft-detail empty-state";
    $("#draft-detail").innerHTML = emptyState("Select a draft", "Its email body, evidence, validators, and approval controls will appear here.");
    return;
  }
  $("#draft-detail").className = "panel draft-detail";
  const checks = draft.validator_results?.checks || [];
  const recipient = draft.contact?.email || "";
  const subject = draftSubject(draft);
  const reviewButtons = draft.status === "pending_review" ? `
    <div class="review-actions">
      <button class="button danger" id="reject-draft">Reject with reason</button>
      <button class="button primary" id="approve-draft">Approve draft</button>
    </div>` : "";
  $("#draft-detail").innerHTML = `
    <div class="draft-detail-header"><div><p class="eyebrow">${escapeHtml(humanize(draft.contact_status))}</p><h3>${escapeHtml(draft.firm)}</h3><div class="draft-meta"><span><strong>Recipient:</strong> ${escapeHtml(draft.contact?.name || "Relationship contact")}</span><span><strong>Email:</strong> ${escapeHtml(recipient || "not on file")}</span></div></div>${pill(draft.status)}</div>
    <div class="review-guide"><strong>Review order:</strong> Read the email, confirm the cited evidence supports every firm-specific claim, then make the approval decision.</div>
    <div class="detail-section-heading"><h4>Subject</h4><span class="quiet">${escapeHtml(humanize(draft.subject_status))}, editable before you copy</span></div>
    <input id="draft-subject-line" class="subject-input" type="text" value="${escapeHtml(subject)}" aria-label="Email subject line">
    <div class="detail-section-heading"><h4>Email body</h4><span class="quiet">Reviewable draft only. Nothing is sent.</span></div><div class="document">${escapeHtml(draft.email_body)}</div>
    <div class="detail-section-heading"><h4>Validator results</h4><div class="validator-list">${checks.map((check) => `<span class="validator">✓ ${escapeHtml(humanize(check))}</span>`).join("")}</div></div>
    <div class="detail-section-heading"><h4>Evidence and provenance</h4><span class="quiet">Internal review record</span></div><div class="evidence">${escapeHtml(draft.evidence_block)}</div>
    ${reviewButtons}
    ${renderSendPanel(draft, recipient)}`;
  $("#approve-draft")?.addEventListener("click", () => reviewSelectedDraft("approved"));
  $("#reject-draft")?.addEventListener("click", () => reviewSelectedDraft("rejected"));
  bindSendPanel(draft, recipient);
}

// Drafts created before subjects were templated have subject === null. Fall back
// so that existing approved work stays sendable without regenerating it.
function draftSubject(draft) {
  if (draft.subject) return draft.subject;
  return SUBJECT_FALLBACK[draft.contact_status] || SUBJECT_FALLBACK.cold_prospect;
}

// Copy-out is deliberately gated on approval: a human decision comes before an
// email can leave the workspace, even by clipboard.
function renderSendPanel(draft, recipient) {
  if (draft.status === "rejected") return "";
  if (draft.status === "pending_review") {
    return `<div class="send-panel locked"><span class="send-lock">🔒</span><div><strong>Approve before sending</strong><p>Copy and compose actions unlock once you approve this draft.</p></div></div>`;
  }
  const sent = draft.status === "sent";
  return `
    <div class="send-panel">
      <div class="send-panel-heading">
        <div><p class="eyebrow">Step 4</p><h4>Send it yourself</h4><p class="section-copy">Bridge does not transmit email. Copy this into Gmail, send it, then record that you did.</p></div>
        ${sent ? `<span class="sent-badge">Sent${draft.sent_at ? ` ${escapeHtml(String(draft.sent_at).slice(0, 10))}` : ""}</span>` : ""}
      </div>
      <div class="send-actions">
        <button class="button secondary" type="button" data-copy="recipient">Copy address</button>
        <button class="button secondary" type="button" data-copy="subject">Copy subject</button>
        <button class="button secondary" type="button" data-copy="body">Copy body</button>
        <a class="button primary" id="gmail-compose" href="#" target="_blank" rel="noopener noreferrer">Open in Gmail</a>
      </div>
      ${recipient ? "" : `<p class="send-warning">No email address is on file for this contact. Run contact discovery for this firm before sending.</p>`}
      ${sent ? "" : `<div class="review-actions"><button class="button primary" id="mark-sent">I sent this</button></div>`}
    </div>`;
}

function bindSendPanel(draft, recipient) {
  const subjectInput = $("#draft-subject-line");
  const values = {
    recipient,
    get subject() { return subjectInput?.value ?? draftSubject(draft); },
    body: draft.email_body,
  };

  $$("#draft-detail [data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const key = button.dataset.copy;
      const value = values[key];
      if (!value) return showToast(`There is no ${key} to copy.`, true);
      try {
        await navigator.clipboard.writeText(value);
        showToast(`${key[0].toUpperCase()}${key.slice(1)} copied.`);
      } catch {
        showToast("Your browser blocked the clipboard. Select the text and copy manually.", true);
      }
    });
  });

  // A compose URL only prefills a window. It transmits nothing; a person still
  // has to press send inside Gmail.
  const compose = $("#gmail-compose");
  if (compose) {
    const updateHref = () => {
      const params = new URLSearchParams({
        view: "cm", fs: "1", to: recipient, su: values.subject, body: draft.email_body,
      });
      compose.href = `https://mail.google.com/mail/?${params.toString()}`;
    };
    updateHref();
    subjectInput?.addEventListener("input", updateHref);
  }

  $("#mark-sent")?.addEventListener("click", markSelectedDraftSent);
}

function renderManualQueue() {
  const rows = state.manual_queue || [];
  $("#manual-queue-summary").textContent = `${rows.length} item${rows.length === 1 ? "" : "s"} require a human decision or missing information.`;
  $("#manual-list").innerHTML = rows.length ? rows.map((row) => `
    <article class="manual-item">
      <div><header><div><strong>${escapeHtml(row.firm)}</strong><span class="quiet">${escapeHtml(row.owner)} lane · ${escapeHtml(humanize(row.source_stage))}</span></div>${pill(row.confidence)}</header><p><strong>Why it stopped:</strong> ${escapeHtml(row.reason)}</p></div>
      <div class="manual-next-step"><strong>What to resolve</strong><p>${escapeHtml((row.gaps || []).length ? row.gaps.join(" · ") : "Review the reason, correct the source-stage issue, and rerun the eligible pipeline action.")}</p></div>
    </article>
  `).join("") : emptyState("Manual queue is clear", "Every visible firm can continue through the standard workflow.");
}

function crossOwnerPrompt() {
  const targetOwner = $("#cross-owner-target").value;
  const slug = $("#cross-owner-slug").value.trim();
  return `I confirm that ${state.user.owner} is generating a draft for ${targetOwner}'s target ${slug}.`;
}

function renderCrossOwner() {
  const other = state.user.owner === "jamari" ? "fola" : "jamari";
  $("#cross-owner-target").innerHTML = `<option value="${other}">${other}</option>`;
  $("#cross-owner-prompt").textContent = crossOwnerPrompt();
  const confirmations = state.cross_owner_confirmations || [];
  $("#confirmation-list").innerHTML = confirmations.length ? confirmations.map((item) => `
    <div class="compact-row"><div><strong>${escapeHtml(item.target_slug)}</strong><span>${escapeHtml(item.actor_owner)} to ${escapeHtml(item.target_owner)} · ${new Date(item.confirmed_at).toLocaleString()}</span></div>${pill(item.consumed_at ? "used" : "open")}</div>
  `).join("") : emptyState("No exceptions recorded", "Confirmed cross-owner actions will appear here with their usage status.");
}

function renderIdentity() {
  $("#user-name").textContent = state.user.display_name;
  $("#user-email").textContent = state.user.email;
  $("#owner-badge").textContent = `${state.user.owner} lane`;
  $("#user-avatar").textContent = state.user.display_name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function renderAll() {
  renderIdentity();
  renderOverview();
  renderPipeline();
  renderDrafts();
  renderManualQueue();
  renderCrossOwner();
  const counts = state.counts || {};
  $("#nav-target-count").textContent = counts.targets ?? 0;
  $("#nav-draft-count").textContent = counts.pending_review ?? 0;
  $("#nav-manual-count").textContent = counts.manual_queue ?? 0;
}

function switchView(name) {
  if (!VIEW_META[name]) name = "overview";
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-item").forEach((button) => {
    const active = button.dataset.view === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("#page-title").textContent = VIEW_META[name][0];
  $("#page-description").textContent = VIEW_META[name][1];
  document.body.classList.remove("nav-open");
  $("#mobile-menu").setAttribute("aria-expanded", "false");
  if (session?.access_token) history.replaceState(null, "", `#${name}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function selectedIds() {
  const ids = [...selectedTargets];
  if (!ids.length) throw new Error("Select at least one firm first.");
  return ids;
}

async function runBatch(path, ids, successMessage) {
  setLoading(true);
  try {
    const result = await api(path, { method: "POST", body: JSON.stringify({ target_ids: ids }) });
    const errors = result.errors || [];
    showToast(
      errors.length
        ? `${errors.length} firm(s) need manual review. Successful firms were preserved.`
        : successMessage,
      errors.length > 0,
    );
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

function openDraftDialog(targetId) {
  const target = state.targets.find((item) => item.id === targetId);
  const contacts = (state.contacts || []).filter((item) => item.target_id === targetId);
  $("#draft-target-id").value = targetId;
  $("#draft-dialog-title").textContent = `Generate for ${target.firm}`;
  const cold = target.contact_status === "cold_prospect";
  $("#draft-contact-label").classList.toggle("hidden", !cold);
  $("#draft-contact").classList.toggle("hidden", !cold);
  $("#draft-paragraph-label").classList.toggle("hidden", !cold);
  $("#draft-paragraph").classList.toggle("hidden", !cold);
  $("#draft-paragraph-help").classList.toggle("hidden", !cold);
  $("#draft-paragraph").required = cold;
  $("#draft-paragraph").value = "";
  $("#draft-contact").innerHTML = contacts.length
    ? contacts.map((contact) => `<option value="${contact.id}">${escapeHtml(contact.name)} · ${escapeHtml(contact.title)} · ${escapeHtml(contact.email)}</option>`).join("")
    : `<option value="">No verified contact</option>`;
  $("#draft-dialog").showModal();
}

async function reviewSelectedDraft(action) {
  let reason = null;
  if (action === "rejected") {
    reason = window.prompt("Why is this draft being rejected?")?.trim();
    if (!reason) return showToast("A rejection reason is required.", true);
  }
  setLoading(true);
  try {
    await api(`/api/drafts/${selectedDraftId}/review`, {
      method: "POST",
      body: JSON.stringify({ action, reason }),
    });
    showToast(action === "approved" ? "Draft approved." : "Draft rejected and reason logged.");
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

async function markSelectedDraftSent() {
  if (!window.confirm("Confirm you have already sent this email from Gmail. This only records what you did; Bridge sends nothing.")) return;
  setLoading(true);
  try {
    await api(`/api/drafts/${selectedDraftId}/sent`, { method: "POST" });
    showToast("Recorded as sent.");
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

async function logout() {
  try {
    if (session?.access_token) {
      await fetch(`${config.supabase_url}/auth/v1/logout`, {
        method: "POST",
        headers: { apikey: config.supabase_publishable_key, Authorization: `Bearer ${session.access_token}` },
      });
    }
  } finally {
    saveSession(null);
    state = null;
    selectedTargets = new Set();
    selectedDraftId = null;
    setAuthenticated(false);
    $("#login-password").value = "";
    history.replaceState(null, "", window.location.pathname);
  }
}

function bindEvents() {
  $$('[data-close]').forEach((button) => button.addEventListener("click", () => {
    document.getElementById(button.dataset.close).close();
  }));
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("#login-error").textContent = "";
    const submit = $("#login-submit");
    submit.disabled = true;
    submit.textContent = "Signing in securely";
    try {
      await signIn($("#login-email").value.trim(), $("#login-password").value);
      setAuthenticated(true);
      await loadState();
      const requestedView = window.location.hash.slice(1);
      switchView(VIEW_META[requestedView] ? requestedView : "overview");
    } catch (error) {
      saveSession(null);
      setAuthenticated(false);
      $("#login-error").textContent = error.message;
    } finally {
      submit.disabled = !config;
      submit.textContent = "Sign in to Bridge";
    }
  });
  $("#logout-button").addEventListener("click", logout);
  $("#refresh-button").addEventListener("click", () => loadState().catch((error) => showToast(error.message, true)));
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$(".jump").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.jump)));
  $("#primary-next-action").addEventListener("click", (event) => switchView(event.currentTarget.dataset.view));
  $("#mobile-menu").addEventListener("click", () => {
    const open = document.body.classList.toggle("nav-open");
    $("#mobile-menu").setAttribute("aria-expanded", String(open));
  });
  $("#sidebar-scrim").addEventListener("click", () => {
    document.body.classList.remove("nav-open");
    $("#mobile-menu").setAttribute("aria-expanded", "false");
  });
  $("#pipeline-search").addEventListener("input", (event) => {
    pipelineSearch = event.target.value;
    renderPipeline();
  });
  $("#pipeline-filter").addEventListener("change", (event) => {
    pipelineStatus = event.target.value;
    renderPipeline();
  });
  $("#derive-selected").addEventListener("click", () => {
    try { runBatch("/api/derive-status", selectedIds(), "Contact statuses derived from the CRM snapshot."); }
    catch (error) { showToast(error.message, true); }
  });
  $("#research-selected").addEventListener("click", () => {
    try { runBatch("/api/research", selectedIds(), "Research completed. Review confidence and manual routing."); }
    catch (error) { showToast(error.message, true); }
  });
  $("#contacts-selected").addEventListener("click", async () => {
    try {
      setLoading(true);
      const preview = await api("/api/contacts/preview", { method: "POST", body: JSON.stringify({ target_ids: selectedIds() }) });
      $("#contact-run-id").value = preview.run_id;
      const remaining = preview.hunter_balance?.remaining;
      $("#contact-cap").max = preview.credits_max;
      $("#contact-cap").value = Math.min(preview.credits_max, remaining ?? preview.credits_max);
      $("#contact-preview").innerHTML = `
        <p><strong>${preview.eligible.length}</strong> eligible firm(s), <strong>${preview.skipped.length}</strong> skipped by the gate.</p>
        <p>Live Hunter balance: <strong>${remaining ?? "unavailable"}</strong>. This run can spend between ${preview.credits_min} and ${preview.credits_max} credits, but it will hard stop at the cap you confirm.</p>
        ${preview.skipped.length ? `<details><summary>Skipped reasons</summary><ul>${preview.skipped.map((item) => `<li>${escapeHtml(item.reason)}</li>`).join("")}</ul></details>` : ""}`;
      $("#contact-dialog").showModal();
    } catch (error) { showToast(error.message, true); }
    finally { setLoading(false); }
  });

  $("#intake-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const firms = $("#intake-lines").value.split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => {
      const [firm = "", domain = "", region = "US", firm_type = "", priority = "3"] = line.split("|").map((part) => part.trim());
      return { firm, domain, region, firm_type, priority: Number(priority) || 3 };
    });
    if (!firms.length) return showToast("Enter at least one firm.", true);
    setLoading(true);
    try {
      const result = await api("/api/intake", {
        method: "POST",
        body: JSON.stringify({ firms, run_research: $("#intake-research").checked }),
      });
      showToast(`${result.targets.length} firm(s) added to ${state.user.owner}'s lane.`);
      $("#intake-lines").value = "";
      if (result.research_target_ids.length) {
        const research = await api("/api/research", { method: "POST", body: JSON.stringify({ target_ids: result.research_target_ids }) });
        showToast(
          research.errors.length
            ? `${research.errors.length} firm(s) were routed to manual review. Successful research was preserved.`
            : "Batch added and research completed.",
          research.errors.length > 0,
        );
      }
      await loadState();
    } catch (error) { showToast(error.message, true); }
    finally { setLoading(false); }
  });

  $("#draft-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const targetId = $("#draft-target-id").value;
    const target = state.targets.find((item) => item.id === targetId);
    if (target.contact_status === "cold_prospect" && !$("#draft-paragraph").value.trim()) {
      return showToast("Write the firm-specific paragraph before generating.", true);
    }
    setLoading(true);
    try {
      const result = await api("/api/drafts", {
        method: "POST",
        body: JSON.stringify({
          target_id: targetId,
          contact_id: $("#draft-contact").value || null,
          firm_specific_paragraph: $("#draft-paragraph").value.trim() || null,
        }),
      });
      selectedDraftId = result.id;
      $("#draft-dialog").close();
      showToast("Validator-passing draft added to review.");
      await loadState();
      switchView("drafts");
    } catch (error) { showToast(error.message, true); }
    finally { setLoading(false); }
  });

  $("#contact-run-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    setLoading(true);
    try {
      const result = await api("/api/contacts/run", {
        method: "POST",
        body: JSON.stringify({ run_id: $("#contact-run-id").value, confirmed_credit_cap: Number($("#contact-cap").value) }),
      });
      $("#contact-dialog").close();
      showToast(`Contact run completed. ${result.credits_spent} credit(s) spent.`);
      await loadState();
    } catch (error) { showToast(error.message, true); }
    finally { setLoading(false); }
  });

  const updatePrompt = () => { if (state) $("#cross-owner-prompt").textContent = crossOwnerPrompt(); };
  $("#cross-owner-slug").addEventListener("input", updatePrompt);
  $("#cross-owner-target").addEventListener("change", updatePrompt);
  $("#cross-owner-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    setLoading(true);
    try {
      const result = await api("/api/drafts/cross-owner", {
        method: "POST",
        body: JSON.stringify({
          target_owner: $("#cross-owner-target").value,
          target_slug: $("#cross-owner-slug").value.trim(),
          firm_specific_paragraph: $("#cross-owner-paragraph").value.trim() || null,
          confirmation_text: $("#cross-owner-confirmation").value,
        }),
      });
      showToast(`${result.firm} draft saved into ${result.owner}'s review lane.`);
      $("#cross-owner-form").reset();
      await loadState();
    } catch (error) { showToast(error.message, true); }
    finally { setLoading(false); }
  });
}

async function boot() {
  bindEvents();
  try {
    await loadConfig();
    const stored = localStorage.getItem(SESSION_KEY);
    if (stored) session = JSON.parse(stored);
    if (session?.access_token) {
      setAuthenticated(true);
      try {
        await loadState();
        const requestedView = window.location.hash.slice(1);
        switchView(VIEW_META[requestedView] ? requestedView : "overview");
      }
      catch (error) {
        saveSession(null);
        setAuthenticated(false);
        $("#login-error").textContent = error.message;
      }
    }
  } catch (error) {
    $("#login-error").textContent = error.message;
  }
}

boot();
