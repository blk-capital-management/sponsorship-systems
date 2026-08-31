const SESSION_KEY = "blk_bridge_session";
const LANE_KEY = "blk_bridge_lane";

let config = null;
let session = null;
let activeLane = null;
let state = null;
let selectedTargets = new Set();
let selectedDraftId = null;
let selectedFirmId = null;
let firmDetail = null;
let pipelineSearch = "";
let pipelineStatus = "all";
let pipelineOutreach = "all";
let librarySearch = "";
const libraryFilters = {
  relationship: "all", stage: "all", owner: "all",
  assetClass: "all", region: "all", tier: "all",
};

const VIEW_META = {
  overview: ["Overview", "See what needs attention and choose the next best action."],
  pipeline: ["Firm pipeline", "Move selected firms through status, research, contacts, and drafting."],
  library: ["Firm library", "Search every firm and open its complete CRM history."],
  firm: ["Firm record", "Review contacts, research, partnership facts, drafts, notes, and activity."],
  drafts: ["Draft review", "Inspect the complete email record before making a human approval decision."],
  manual: ["Manual queue", "Resolve missing facts and weak evidence that Bridge will not guess."],
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

// ── Lead intake ───────────────────────────────────────────────────────────────
// Sourcing rarely produces a full record in one sitting. You find a website, then
// a LinkedIn page, and sometimes an email format. Only the firm name is required;
// everything else can arrive later.

let intakeMode = "single";

// Columns the bulk paste understands when a header line names them. The
// positional order below is also the legacy pipe format, kept working.
const INTAKE_COLUMNS = [
  "firm", "domain", "region", "firm_type", "priority",
  "website", "linkedin_url", "email_format", "email_format_source_url",
  "tier_target", "notes",
];
const INTAKE_ALIASES = {
  category: "firm_type", type: "firm_type", "firm type": "firm_type",
  linkedin: "linkedin_url", site: "website", url: "website",
  "email format": "email_format", pattern: "email_format",
  source: "email_format_source_url", "source url": "email_format_source_url",
  tier: "tier_target",
};

const EMAIL_FORMAT_TOKENS = /\{(first|last|f|l|First|Last)\}/g;

function selectIntakeTab(mode) {
  intakeMode = mode;
  $$(".intake-tab").forEach((tab) => {
    const active = tab.dataset.intakeTab === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $("#intake-pane-single").hidden = mode !== "single";
  $("#intake-pane-bulk").hidden = mode !== "bulk";
}

/** Preview the address a format produces, and say what it saves. */
function renderFormatNote() {
  const note = $("#intake-format-note");
  const format = $("#intake-email-format").value.trim();
  const source = $("#intake-email-source").value.trim();
  if (!format) { note.hidden = true; return; }

  note.hidden = false;
  note.className = "format-note";
  const domain = ($("#intake-domain").value.trim()
    || $("#intake-website").value.trim().replace(/^https?:\/\//, "").split("/")[0]
    || "example.com").replace(/^www\./, "");

  const tokens = format.match(EMAIL_FORMAT_TOKENS);
  if (!tokens) {
    note.classList.add("is-bad");
    note.innerHTML = `<strong>Not a usable pattern</strong><span>Use tokens such as {first}, {last}, {f} or {l}. For example {f}{last}@${escapeHtml(domain)}</span>`;
    return;
  }
  if (!source) {
    note.innerHTML = `<strong>Add where you saw it</strong><span>A format without a source is not a format. Paste the public page you read it off.</span>`;
    return;
  }
  const preview = format
    .replace(/\{first\}/g, "jane").replace(/\{last\}/g, "doe")
    .replace(/\{First\}/g, "Jane").replace(/\{Last\}/g, "Doe")
    .replace(/\{f\}/g, "j").replace(/\{l\}/g, "d");
  const address = preview.includes("@") ? preview : `${preview}@${domain}`;
  note.classList.add("is-good");
  note.innerHTML = `<strong>Jane Doe becomes ${escapeHtml(address)}</strong><span>Bridge will use this instead of asking Hunter for the pattern, so this firm costs no lookup.</span>`;
}

function readSingleIntake() {
  const firm = $("#intake-firm").value.trim();
  if (!firm) return [];
  return [{
    firm,
    domain: $("#intake-domain").value.trim(),
    website: $("#intake-website").value.trim(),
    linkedin_url: $("#intake-linkedin").value.trim(),
    region: $("#intake-region").value,
    firm_type: $("#intake-category").value,
    tier_target: $("#intake-tier").value,
    priority: Number($("#intake-priority").value) || 3,
    notes: $("#intake-notes").value.trim(),
    email_format: $("#intake-email-format").value.trim(),
    email_format_source_url: $("#intake-email-source").value.trim(),
  }];
}

/** Parse pasted lines. A header line names the columns; otherwise order applies. */
function readBulkIntake() {
  const lines = $("#intake-lines").value.split(/\n+/)
    .map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return [];

  let columns = INTAKE_COLUMNS;
  const head = lines[0].split("|").map((part) => part.trim().toLowerCase());
  const named = head.map((part) => INTAKE_ALIASES[part] || part);
  if (named.every((part) => INTAKE_COLUMNS.includes(part))) {
    columns = named;
    lines.shift();
  }

  return lines.map((line) => {
    const parts = line.split("|").map((part) => part.trim());
    const firm = {};
    columns.forEach((column, index) => {
      if (parts[index]) firm[column] = parts[index];
    });
    firm.region = firm.region || "US";
    firm.priority = Number(firm.priority) || 3;
    return firm;
  }).filter((firm) => firm.firm);
}

/** Check pasted lines against what is already loaded, before anything is sent.
 *
 * The server still has the final say, because only it can see the whole lane.
 * This exists so an obvious duplicate or a malformed line is caught while you
 * are still looking at the text you pasted.
 */
function checkBulkIntake() {
  const box = $("#intake-results");
  let firms;
  try {
    firms = readBulkIntake();
  } catch (error) {
    box.hidden = false;
    box.innerHTML = `<div class="intake-result is-skipped"><b>Cannot read that</b><span>${escapeHtml(error.message)}</span></div>`;
    return;
  }
  if (!firms.length) { box.hidden = true; return; }

  const known = state.targets || [];
  const knownDomains = new Set(known.map((t) => String(t.domain || "").toLowerCase()).filter(Boolean));
  const knownFirms = new Set(known.map((t) => String(t.firm || "").toLowerCase()));
  const categories = new Set((config?.firm_categories || []).map((c) => c.toLowerCase()));
  const seen = new Set();

  const rows = firms.map((firm) => {
    const name = firm.firm;
    const domain = String(firm.domain || "").toLowerCase();
    const key = name.toLowerCase();

    if (seen.has(key)) return row("is-skipped", name, "Listed twice in this paste.");
    seen.add(key);
    if (knownFirms.has(key)) return row("is-skipped", name, "Already in your pipeline.");
    if (domain && knownDomains.has(domain)) {
      return row("is-skipped", name, `Domain ${domain} is already on another firm.`);
    }
    if (firm.email_format && !firm.email_format_source_url) {
      return row("is-skipped", name, "Email format needs the source URL it was read off.");
    }
    if (!domain && !firm.website) {
      return row("is-warned", name, "No domain. It will be added, then routed to manual review.");
    }
    if (firm.firm_type && !categories.has(String(firm.firm_type).toLowerCase())) {
      return row("is-warned", name, `Category "${firm.firm_type}" is not on the known list. It will be stored as typed.`);
    }
    return row("", name, "Looks good.");
  });

  box.hidden = false;
  box.innerHTML = rows.join("");

  function row(css, name, message) {
    return `<div class="intake-result${css ? ` ${css}` : ""}"><b>${escapeHtml(name)}</b><span>${escapeHtml(message)}</span></div>`;
  }
}

function clearIntakeForm() {
  ["#intake-firm", "#intake-domain", "#intake-website", "#intake-linkedin",
   "#intake-notes", "#intake-email-format", "#intake-email-source", "#intake-lines"]
    .forEach((selector) => { $(selector).value = ""; });
  $("#intake-format-note").hidden = true;
}

/** One line per firm, so a skipped row names itself instead of sinking the batch. */
function renderIntakeResults(result) {
  const box = $("#intake-results");
  const warnings = result.warnings || [];
  const skipped = result.skipped || [];
  const warnedFirms = new Set(warnings.map((item) => item.firm));

  const rows = [
    ...result.targets.map((row) => {
      const warning = warnings.find((item) => item.firm === row.firm);
      return `<div class="intake-result${warning ? " is-warned" : ""}"><b>${escapeHtml(row.firm)}</b><span>${escapeHtml(warning ? warning.warning : "Added to your lane.")}</span></div>`;
    }),
    ...skipped.map((item) => `<div class="intake-result is-skipped"><b>${escapeHtml(item.firm)}</b><span>${escapeHtml(item.reason)}</span></div>`),
    ...warnings.filter((item) => !warnedFirms.has(item.firm)).map((item) =>
      `<div class="intake-result is-warned"><b>${escapeHtml(item.firm)}</b><span>${escapeHtml(item.warning)}</span></div>`),
  ];

  const summary = `<div class="intake-summary"><strong>${result.targets.length} added</strong>`
    + `<span>${skipped.length} skipped</span><span>${warnings.length} flagged</span></div>`;
  box.innerHTML = (rows.length ? summary : "") + rows.join("");
  box.hidden = !rows.length;
}
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

/** Only http(s) may reach an href. escapeHtml stops markup, not a javascript: scheme. */
function safeHttpUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "#";
  } catch {
    return "#";
  }
}

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
  renderCategoryOptions(data.firm_categories || []);
  renderCrmOptions();
  $("#google-signin").disabled = false;
  $("#google-signin-label").textContent = "Continue with Google";
}

/** Fill the category dropdown from config so it cannot drift from the validator. */
function renderCategoryOptions(labels) {
  const select = $("#intake-category");
  if (!select) return;
  select.innerHTML = [
    '<option value="">Not set</option>',
    ...labels.map((label) => `<option value="${escapeHtml(label)}">${escapeHtml(label)}</option>`),
  ].join("");
}

function optionMarkup(values) {
  return values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
}

function renderCrmOptions() {
  const relationships = config?.relationship_statuses || [];
  const stages = config?.pipeline_stages || [];
  $("#pipeline-filter").innerHTML = '<option value="all">All relationships</option>' + optionMarkup(relationships);
  $("#batch-relationship-status").innerHTML = '<option value="">Choose status</option><option value="__automatic__">Return to automatic</option>' + optionMarkup(relationships);
  $("#batch-pipeline-stage").innerHTML = '<option value="">Choose stage</option><option value="__automatic__">Return to automatic</option>' + optionMarkup(stages);
  $("#library-relationship").innerHTML = '<option value="all">All</option>' + optionMarkup(relationships);
  $("#library-stage").innerHTML = '<option value="all">All</option>' + optionMarkup(stages);
}

function saveSession(value) {
  session = value;
  if (value) localStorage.setItem(SESSION_KEY, JSON.stringify(value));
  else localStorage.removeItem(SESSION_KEY);
}

function startGoogleSignIn() {
  if (!config) throw new Error("Dashboard configuration has not loaded. Refresh this page and try again.");
  const redirectTo = window.location.origin + window.location.pathname;
  const url = `${config.supabase_url}/auth/v1/authorize?provider=google&redirect_to=${encodeURIComponent(redirectTo)}`;
  window.location.href = url;
}

// Supabase's /authorize redirect lands back here with tokens in the URL hash
// (implicit flow), e.g. #access_token=...&refresh_token=...&expires_in=3600.
// Parsed by hand, not URLSearchParams: that class turns "+" into a literal
// space, which silently corrupts a base64url refresh_token containing "+".
// Returns true if a session was captured, so boot() can skip stale localStorage.
function consumeGoogleRedirect() {
  const hash = window.location.hash;
  if (!hash.includes("access_token=") && !hash.includes("error=")) return false;
  const fields = Object.fromEntries(
    hash
      .slice(1)
      .split("&")
      .filter(Boolean)
      .map((pair) => {
        const [key, value = ""] = pair.split("=");
        return [decodeURIComponent(key), decodeURIComponent(value)];
      })
  );
  history.replaceState(null, "", window.location.pathname);
  if (fields.error_description) {
    $("#login-error").textContent = fields.error_description;
    return false;
  }
  if (!fields.access_token || !fields.refresh_token) return false;
  saveSession({
    access_token: fields.access_token,
    refresh_token: fields.refresh_token,
    expires_in: Number(fields.expires_in || 3600),
    token_type: fields.token_type || "bearer",
  });
  return true;
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
      ...(activeLane ? { "X-Blk-Lane": activeLane } : {}),
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
    try {
      state = await api("/api/state");
    } catch (error) {
      // A stale locally-stored lane the account no longer holds must not lock
      // the workspace out; fall back to the account's default lane once.
      if (!activeLane || !/not permitted/i.test(error.message)) throw error;
      activeLane = null;
      localStorage.removeItem(LANE_KEY);
      state = await api("/api/state");
    }
    activeLane = state.user.owner;
    localStorage.setItem(LANE_KEY, activeLane);
    renderAll();
    $("#sync-status").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  } finally {
    setLoading(false);
  }
}

/** Switch which lane's data this session reads and writes. */
async function setLane(lane) {
  if (!lane || lane === activeLane) return;
  const previous = activeLane;
  activeLane = lane;
  localStorage.setItem(LANE_KEY, lane);
  try {
    await loadState();
    showToast(`Switched to ${lane}'s lane.`);
  } catch (error) {
    activeLane = previous;
    if (previous) localStorage.setItem(LANE_KEY, previous);
    showToast(error.message, true);
  }
}

function pill(value, label = null) {
  const css = String(value || "").toLowerCase().replaceAll(" ", "_");
  return `<span class="pill ${escapeHtml(css)}">${escapeHtml(label || humanize(value))}</span>`;
}

const GROUNDING_LABELS = {
  grounded: "Grounded in stored sources",
  ungrounded: "Not grounded in a stored source",
  no_research_available: "No stored research available",
};

function groundingStatusOf(draft) {
  return draft?.fields?.firm_paragraph_provenance?.grounding_status
    || draft?.validator_results?.grounding_status
    || null;
}

function groundingNotice(status) {
  return status === "no_research_available"
    ? "No stored research was available for this firm."
    : "This paragraph is not grounded in a stored source.";
}

/** Informational label shown at approval time. Never a pass or fail. */
function groundingPill(status) {
  if (!status) return "";
  const label = GROUNDING_LABELS[status] || humanize(status);
  return `<span class="pill grounding ${escapeHtml(status)}">${escapeHtml(label)}</span>`;
}

function statusControl(target, field) {
  const relationship = field === "relationship_status";
  const effective = relationship
    ? target.relationship_status_effective : target.pipeline_stage_effective;
  const overridden = relationship
    ? target.relationship_status_is_overridden : target.pipeline_stage_is_overridden;
  const values = relationship
    ? (config?.relationship_statuses || []) : (config?.pipeline_stages || []);
  const automatic = relationship
    ? target.relationship_status_auto : target.pipeline_stage_auto;
  return `<div class="status-control${overridden ? " is-manual" : ""}">
    <select class="row-status-select" data-id="${target.id}" data-field="${field}" aria-label="${relationship ? "Relationship status" : "Pipeline stage"} for ${escapeHtml(target.firm)}">
      <option value="__automatic__" ${overridden ? "" : "selected"}>Automatic: ${escapeHtml(automatic)}</option>
      ${values.map((value) => `<option value="${escapeHtml(value)}" ${overridden && value === effective ? "selected" : ""}>${escapeHtml(value)}</option>`).join("")}
    </select>
    ${overridden ? '<span class="override-marker" title="Manual override is taking precedence over automation">● Manual</span>' : '<span class="auto-marker">Automatic</span>'}
  </div>`;
}

function researchByTarget() {
  return new Map((state?.research || []).map((item) => [item.target_id, item]));
}

function contactsByTarget() {
  const grouped = new Map();
  for (const item of state?.contacts || []) {
    if (item.dropped) continue;
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
    ? `<strong>Unavailable</strong><span>Contact discovery is paused.</span>${
        balance.error ? `<span class="balance-error">${escapeHtml(balance.error)}</span>` : ""}`
    : `<strong>${balance.remaining}</strong><span>search credits remaining · ${balance.used} of ${balance.available} used</span>`;

  const research = researchByTarget();
  const top = [...state.targets].filter((target) => target.pipeline_visible)
    .sort((left, right) => Number(left.priority || 99) - Number(right.priority || 99)).slice(0, 6);
  $("#overview-targets").innerHTML = top.length ? top.map((target) => {
    const artifact = research.get(target.id);
    return `<div class="compact-row"><div><strong>${escapeHtml(target.firm)}</strong><span>${escapeHtml(target.domain || "Domain required")} · Priority ${escapeHtml(target.priority || "not set")}</span></div><div class="compact-actions">${pill(target.relationship_status_effective)} ${artifact ? pill(artifact.confidence) : pill("not researched")}</div></div>`;
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

// Why this firm cannot produce a draft yet, or null when it can. The server
// enforces all of this too; surfacing it here turns a dead-end click and a 400
// into a row that says what is missing.
function draftBlocker(target, artifact, targetContacts) {
  if ((target.contact_status || "unknown") === "unknown") {
    return "Derive this firm's relationship status first, step 1 in the toolbar.";
  }
  if (target.contact_status !== "cold_prospect") return null;
  if (!artifact) return "Cold prospects need research before drafting. Run step 2.";
  if (!targetContacts.length) {
    return "Cold prospects need a verified contact. Run step 3, find contacts.";
  }
  return null;
}

function renderPipeline() {
  const research = researchByTarget();
  const contacts = contactsByTarget();
  const drafts = draftsByTarget();
  const query = pipelineSearch.trim().toLowerCase();
  const pipelineTargets = state.targets.filter((target) => target.pipeline_visible);
  const outreachCounts = {
    all: state.counts?.pipeline_targets || pipelineTargets.length,
    not_sent: state.counts?.outreach_not_sent || 0,
    awaiting_response: state.counts?.outreach_awaiting_response || 0,
  };
  $$('[data-outreach-filter]').forEach((button) => {
    const selected = button.dataset.outreachFilter === pipelineOutreach;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", String(selected));
    const count = button.querySelector("b");
    if (count) count.textContent = outreachCounts[button.dataset.outreachFilter] ?? 0;
  });
  const pipelineIds = new Set(pipelineTargets.map((target) => target.id));
  for (const targetId of selectedTargets) {
    if (!pipelineIds.has(targetId)) selectedTargets.delete(targetId);
  }
  const visibleTargets = pipelineTargets.filter((target) => {
    const matchesQuery = !query || `${target.firm} ${target.domain || ""}`.toLowerCase().includes(query);
    const status = target.relationship_status_effective || "Cold Prospect";
    const matchesOutreach = pipelineOutreach === "all"
      || target.outreach_queue_state === pipelineOutreach;
    return matchesQuery
      && (pipelineStatus === "all" || status === pipelineStatus)
      && matchesOutreach;
  });
  $("#pipeline-body").innerHTML = visibleTargets.length ? visibleTargets.map((target) => {
    const artifact = research.get(target.id);
    const targetContacts = contacts.get(target.id) || [];
    const targetDrafts = drafts.get(target.id) || [];
    const latestDraft = targetDrafts[0];
    const gate = target.hunter_gate || { status: "unknown", reason: "Gate not evaluated." };
    const outreachTitle = target.outreach_queue_state === "awaiting_response"
      && target.last_outreach_at
      ? `Last outreach ${String(target.last_outreach_at).slice(0, 10)}`
      : "";
    return `<tr>
      <td><input class="target-check" type="checkbox" data-id="${target.id}" ${selectedTargets.has(target.id) ? "checked" : ""}></td>
      <td class="firm-cell">
        <strong>${escapeHtml(target.firm)}</strong>
        <div class="domain-row">
          <span>${target.domain ? escapeHtml(target.domain) : "Domain required"} · ${escapeHtml(target.owner)}</span>
          <button class="text-button edit-domain" type="button" data-id="${target.id}" data-firm="${escapeHtml(target.firm)}" data-domain="${escapeHtml(target.domain || "")}">${target.domain ? "Edit" : "Add domain"}</button>
        </div>
      </td>
      <td>${statusControl(target, "relationship_status")}</td>
      <td>${statusControl(target, "pipeline_stage")}</td>
      <td>${artifact ? pill(artifact.confidence) : pill("not researched")}</td>
      <td><span title="${escapeHtml(gate.reason)}">${pill(gate.status)}</span></td>
      <td class="contacts-cell">
        <span>${targetContacts.length ? `${targetContacts.length} verified` : `<span class="quiet">None yet</span>`}</span>
        <button class="text-button add-contact" type="button" data-id="${target.id}" data-firm="${escapeHtml(target.firm)}">Add contact</button>
      </td>
      <td><span${outreachTitle ? ` title="${escapeHtml(outreachTitle)}"` : ""}>${latestDraft ? pill(latestDraft.status) : "None"}</span></td>
      <td>${draftBlocker(target, artifact, targetContacts)
        ? `<span class="quiet" title="${escapeHtml(draftBlocker(target, artifact, targetContacts))}">Not ready</span>`
        : `<button class="text-button create-draft" data-id="${target.id}">Create draft</button>`}</td>
      <td><button class="text-button danger remove-target" type="button" data-id="${target.id}" data-firm="${escapeHtml(target.firm)}">Remove from pipeline</button></td>
    </tr>`;
  }).join("") : `<tr><td colspan="10">${emptyState(pipelineTargets.length ? "No matching firms" : "Your pipeline is empty", pipelineTargets.length ? "Clear the search or change a pipeline filter." : "Add or reopen a firm from Firm Library.")}</td></tr>`;

  $$(".target-check").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) selectedTargets.add(input.dataset.id);
    else selectedTargets.delete(input.dataset.id);
    updateSelectionControls(visibleTargets);
  }));
  $$(".create-draft").forEach((button) => button.addEventListener("click", () => openDraftDialog(button.dataset.id)));
  $$(".add-contact").forEach((button) => button.addEventListener("click", () =>
    openContactFormDialog(button.dataset.id, button.dataset.firm)));
  $$(".edit-domain").forEach((button) => button.addEventListener("click", () =>
    promptTargetDomain(button.dataset.id, button.dataset.firm, button.dataset.domain)));
  $$(".remove-target").forEach((button) => button.addEventListener("click", () =>
    removeTarget(button.dataset.id, button.dataset.firm)));
  $$(".row-status-select").forEach((select) => select.addEventListener("change", () =>
    changeTargetStatus(select.dataset.id, select.dataset.field, select.value)));
  $("#select-all").onchange = (event) => {
    for (const target of visibleTargets) {
      if (event.target.checked) selectedTargets.add(target.id);
      else selectedTargets.delete(target.id);
    }
    renderPipeline();
  };
  const outreachLabel = pipelineOutreach === "all" ? "all outreach states"
    : pipelineOutreach === "not_sent" ? "not sent" : "awaiting response";
  $("#pipeline-results-summary").textContent = `Showing ${visibleTargets.length} of ${pipelineTargets.length} active workflow firms · ${outreachLabel} · ${state.targets.length} total in Firm Library`;
  updateSelectionControls(visibleTargets);
}

/** Add or correct a target's domain in place, so research can run without re-adding the firm. */
async function promptTargetDomain(targetId, firmName, currentDomain) {
  const input = window.prompt(`Domain for ${firmName} (e.g. generalatlantic.com)`, currentDomain || "");
  if (input === null) return;
  const domain = input.trim();
  if (!domain) return showToast("A domain is required.", true);
  setLoading(true);
  try {
    await api(`/api/targets/${targetId}`, {
      method: "PATCH",
      body: JSON.stringify({ domain }),
    });
    showToast(`${firmName} now has a domain. Run research when ready.`);
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

async function removeTarget(targetId, firmName) {
  if (!window.confirm(`Remove ${firmName} from Firm Pipeline? Its firm record, contacts, research, drafts, notes, and history will remain in Firm Library.`)) return;
  setLoading(true);
  try {
    await api(`/api/targets/${targetId}`, { method: "DELETE" });
    selectedTargets.delete(targetId);
    showToast(`${firmName} removed from Firm Pipeline and retained in Firm Library.`);
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

async function changeTargetStatus(targetId, field, selectedValue) {
  const clear = selectedValue === "__automatic__";
  setLoading(true);
  try {
    await api(`/api/targets/${targetId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ field, value: clear ? null : selectedValue, clear }),
    });
    showToast(clear ? "Manual override cleared. Automation is in control again." : "Manual status saved and audit event recorded.");
    await loadState();
    if (selectedFirmId === targetId && $("#view-firm").classList.contains("active")) {
      firmDetail = await api(`/api/firms/${targetId}`);
      renderFirmDetail();
    }
  } catch (error) {
    showToast(error.message, true);
    await loadState().catch(() => {});
  } finally {
    setLoading(false);
  }
}

function syncLibraryFilter(selector, values, selected) {
  const control = $(selector);
  const unique = [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))].sort();
  control.innerHTML = '<option value="all">All</option>' + optionMarkup(unique);
  control.value = unique.includes(selected) ? selected : "all";
}

function renderLibrary() {
  const targets = state?.targets || [];
  syncLibraryFilter("#library-owner", targets.map((target) => target.assigned_owner_effective || target.owner), libraryFilters.owner);
  syncLibraryFilter("#library-asset-class", targets.map((target) => target.firm_type), libraryFilters.assetClass);
  syncLibraryFilter("#library-region", targets.map((target) => target.region), libraryFilters.region);
  syncLibraryFilter("#library-tier", targets.map((target) => target.effective_sponsorship_tier), libraryFilters.tier);
  $("#library-relationship").value = libraryFilters.relationship;
  $("#library-stage").value = libraryFilters.stage;

  const query = librarySearch.trim().toLowerCase();
  const rows = targets.filter((target) => {
    const tier = target.effective_sponsorship_tier || "";
    return (!query || `${target.firm} ${target.domain || ""}`.toLowerCase().includes(query))
      && (libraryFilters.relationship === "all" || target.relationship_status_effective === libraryFilters.relationship)
      && (libraryFilters.stage === "all" || target.pipeline_stage_effective === libraryFilters.stage)
      && (libraryFilters.owner === "all" || (target.assigned_owner_effective || target.owner) === libraryFilters.owner)
      && (libraryFilters.assetClass === "all" || target.firm_type === libraryFilters.assetClass)
      && (libraryFilters.region === "all" || target.region === libraryFilters.region)
      && (libraryFilters.tier === "all" || tier === libraryFilters.tier);
  });
  $("#library-summary").textContent = `${rows.length} of ${targets.length} permanent firm record(s)`;
  $("#library-body").innerHTML = rows.length ? rows.map((target) => {
    const tier = target.effective_sponsorship_tier || "";
    return `<tr>
      <td class="firm-cell"><button class="library-firm-link" type="button" data-id="${target.id}"><strong>${escapeHtml(target.firm)}</strong><span>${escapeHtml(target.domain || "No domain")}</span></button></td>
      <td>${statusControl(target, "relationship_status")}</td>
      <td>${statusControl(target, "pipeline_stage")}</td>
      <td>${escapeHtml(target.firm_type || "Not set")}</td>
      <td>${escapeHtml(target.region || "Not set")}</td>
      <td>${escapeHtml(target.assigned_owner_effective || target.owner || "Not set")}</td>
      <td>${escapeHtml(tier || "Not set")}</td>
      <td>${escapeHtml(target.relationship_expiration || "Not set")}</td>
      <td>${escapeHtml(target.primary_contact || "Not set")}</td>
      <td>${escapeHtml(String(target.last_activity || "Not set").slice(0, 10))}</td>
      <td>${target.pipeline_visible ? pill("active", "Active") : pill("inactive", "Library only")}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="11">${emptyState("No matching firms", "Clear one or more filters to see the permanent firm universe.")}</td></tr>`;
  $$(".library-firm-link").forEach((button) => button.addEventListener("click", () => openFirmRecord(button.dataset.id)));
  $$("#library-body .row-status-select").forEach((select) => select.addEventListener("change", () =>
    changeTargetStatus(select.dataset.id, select.dataset.field, select.value)));
}

function detailValue(label, value) {
  return `<div class="crm-field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "Not set")}</strong></div>`;
}

async function openFirmRecord(targetId) {
  selectedFirmId = targetId;
  firmDetail = null;
  switchView("firm");
  $("#firm-detail").innerHTML = emptyState("Loading firm record", "Contacts, research, history, notes, and drafts are being assembled.");
  setLoading(true);
  try {
    firmDetail = await api(`/api/firms/${targetId}`);
    renderFirmDetail();
  } catch (error) {
    $("#firm-detail").innerHTML = emptyState("Firm record unavailable", error.message);
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

function renderFirmDetail() {
  if (!firmDetail?.target) return;
  const target = firmDetail.target;
  $("#page-title").textContent = target.firm;
  const contacts = firmDetail.contacts || [];
  const research = firmDetail.research || [];
  const drafts = firmDetail.drafts || [];
  const notes = firmDetail.meeting_notes || [];
  const activity = firmDetail.activity || [];
  const hooks = research.flatMap((item) => item.artifact?.alignment_hooks || []);
  $("#firm-detail").innerHTML = `
    <article class="firm-hero">
      <div><p class="eyebrow light">Firm Library record</p><h3>${escapeHtml(target.firm)}</h3><p>${escapeHtml(target.domain || "No domain on file")} · ${escapeHtml(target.owner)} lane</p></div>
      <div class="firm-hero-status">${statusControl(target, "relationship_status")}${statusControl(target, "pipeline_stage")}</div>
    </article>
    <div class="firm-detail-grid">
      <article class="panel firm-section span-2"><div class="detail-section-heading"><h4>Overview</h4></div><div class="crm-field-grid">
        ${detailValue("Firm", target.firm)}${detailValue("Domain", target.domain)}${detailValue("Asset class", target.firm_type)}${detailValue("Region", target.region)}
        ${detailValue("Assigned owner", target.assigned_owner || target.owner)}${detailValue("Access lane", target.owner)}${detailValue("Relationship status", target.relationship_status_effective)}${detailValue("Pipeline stage", target.pipeline_stage_effective)}${detailValue("Sponsorship tier", target.effective_sponsorship_tier)}
        ${detailValue("Expiration / renewal date", target.relationship_expiration)}${detailValue("Partnership scope", target.partnership_scope)}${detailValue("Partnership type", target.partnership_type)}${detailValue("Next step", target.next_step)}${detailValue("Next-step due", target.next_step_due)}
      </div></article>
      <article class="panel firm-section"><div class="detail-section-heading"><h4>Contacts</h4></div><div class="crm-list">${contacts.length ? contacts.map((contact) => `
        <div class="crm-list-row"><div><strong>${escapeHtml(contact.name || "Unnamed contact")}${contact.dropped ? " (inactive)" : ""}</strong><span>${escapeHtml(contact.title || "No title")} · ${escapeHtml(contact.email || "No email")}</span>${contact.contact_provenance?.discovery_url ? `<span>Source: ${escapeHtml(contact.contact_provenance.discovery_url)}</span>` : ""}</div>${pill(contact.verification_status || "unverified")}</div>`).join("") : emptyState("No contacts", "Add a sourced contact from the pipeline or firm record.")}</div></article>
      <article class="panel firm-section"><div class="detail-section-heading"><h4>Partnership</h4></div><div class="crm-field-grid single">
        ${detailValue("Tier", target.effective_sponsorship_tier)}${detailValue("Scope", target.partnership_scope || target.region)}${detailValue("Type", target.partnership_type)}${detailValue("Expiration", target.relationship_expiration)}${detailValue("Historical status", target.relationship_status)}${detailValue("Renewal notes", target.renewal_notes || target.relationship_decline_reason)}${detailValue("Last touchpoint", target.last_touchpoint)}${detailValue("Email-chain notes", target.email_chain_notes)}${detailValue("Contact verification", target.contact_verified_status)}
      </div></article>
      <article class="panel firm-section span-2"><div class="detail-section-heading"><h4>Research</h4></div><div class="research-records">${hooks.length ? hooks.map((hook) => `<div class="research-record"><p>${escapeHtml(hook.text || hook.claim || "Sourced research finding")}</p><a href="${escapeHtml(safeHttpUrl(hook.firm_claim_source || hook.source_url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(hook.firm_claim_source || hook.source_url || "Source unavailable")}</a></div>`).join("") : emptyState("No sourced research", "Unsourced meeting notes stay separate and do not appear here.")}</div></article>
      <article class="panel firm-section span-2"><div class="detail-section-heading"><h4>Meeting notes</h4><button id="add-meeting-note" class="button secondary" type="button">+ Add Meeting Note</button></div><div class="meeting-note-list">${notes.length ? notes.map((note) => `<article class="meeting-note-card"><div><strong>${escapeHtml(note.interaction_type || "Meeting")} · ${escapeHtml(note.interaction_date)}</strong><span>${escapeHtml((note.participants || []).join(", ") || "Participants not recorded")}</span></div><p>${escapeHtml(note.notes)}</p>${note.next_step ? `<p><strong>Next step:</strong> ${escapeHtml(note.next_step)}${note.follow_up_date ? ` · ${escapeHtml(note.follow_up_date)}` : ""}</p>` : ""}<button class="text-button edit-meeting-note" type="button" data-id="${note.id}">Edit</button></article>`).join("") : emptyState("No meeting notes", "Add a meeting, call, or interaction note without inventing missing details.")}</div></article>
      <article class="panel firm-section"><div class="detail-section-heading"><h4>Drafts</h4></div><div class="crm-list">${drafts.length ? drafts.map((draft) => `<div class="crm-list-row"><div><strong>${escapeHtml(draft.subject || "Draft")}</strong><span>${escapeHtml(String(draft.generated_at || "").slice(0, 10))}</span></div>${pill(draft.status)}</div>`).join("") : emptyState("No drafts", "Generated and reviewed drafts will appear here; generation never counts as sending.")}</div></article>
      <article class="panel firm-section"><div class="detail-section-heading"><h4>Activity</h4></div><div class="activity-timeline">${activity.length ? activity.map((event) => `<div class="activity-event"><span>${escapeHtml(String(event.occurred_at || "").slice(0, 10))}</span><div><strong>${escapeHtml(event.title)}</strong><p>${escapeHtml(event.detail || "")}</p>${event.reason ? `<p>${escapeHtml(event.reason)}</p>` : ""}</div></div>`).join("") : emptyState("No activity", "Factual CRM events will appear as Bridge records them.")}</div></article>
    </div>`;
  $("#add-meeting-note").addEventListener("click", () => openMeetingNoteDialog());
  $$(".edit-meeting-note").forEach((button) => button.addEventListener("click", () => openMeetingNoteDialog(button.dataset.id)));
  $$("#firm-detail .row-status-select").forEach((select) => select.addEventListener("change", () =>
    changeTargetStatus(select.dataset.id, select.dataset.field, select.value)));
}

function todayForDateInput() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function openMeetingNoteDialog(noteId = null) {
  const note = noteId ? (firmDetail.meeting_notes || []).find((item) => item.id === noteId) : null;
  $("#meeting-note-id").value = note?.id || "";
  $("#meeting-note-target-id").value = selectedFirmId;
  $("#meeting-note-heading").textContent = note ? "Edit meeting note" : "Add meeting note";
  $("#meeting-note-date").value = note?.interaction_date || todayForDateInput();
  $("#meeting-note-type").value = note?.interaction_type || "Meeting";
  $("#meeting-note-participants").value = (note?.participants || []).join(", ");
  $("#meeting-note-notes").value = note?.notes || "";
  $("#meeting-note-next-step").value = note?.next_step || "";
  $("#meeting-note-follow-up").value = note?.follow_up_date || "";
  $("#meeting-note-dialog").showModal();
}

function updateSelectionControls(visibleTargets = state?.targets || []) {
  const count = selectedTargets.size;
  $("#selection-count").textContent = `${count} selected`;
  ["#derive-selected", "#research-selected", "#contacts-selected", "#apply-batch-relationship", "#apply-batch-stage"].forEach((selector) => { $(selector).disabled = count === 0; });

  // Say what will happen and roughly how long, before it is clicked.
  const hint = $("#batch-hint");
  if (!count) {
    hint.textContent = "Select firms to enable these actions.";
    hint.classList.remove("is-ready");
  } else {
    const minutes = Math.max(1, Math.round((count * 12) / 60));
    hint.textContent = `${count} firm(s) selected. Research runs in batches of 5 and takes roughly ${minutes} minute(s) for this many. You can watch it below.`;
    hint.classList.add("is-ready");
  }

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
      ${groundingPill(groundingStatusOf(draft))}
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
  const advisories = draft.fields?.firm_paragraph_provenance?.advisories || [];
  const advisoryPanel = advisories.length ? `
    <div class="detail-section-heading"><h4>Grounding notes</h4><span class="quiet">Advisory, non-blocking</span></div>
    <ul class="grounding-notes">${advisories.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>` : "";
  const recipient = draft.contact?.email || "";
  const subject = draftSubject(draft);
  const reviewButtons = draft.status === "pending_review" ? `
    <div class="review-actions">
      <button class="button danger" id="reject-draft">Reject with reason</button>
      <button class="button primary" id="approve-draft">Approve draft</button>
    </div>` : "";
  $("#draft-detail").innerHTML = `
    <div class="draft-detail-header"><div><p class="eyebrow">${escapeHtml(humanize(draft.contact_status))}</p><h3>${escapeHtml(draft.firm)}</h3><div class="draft-meta"><span><strong>Recipient:</strong> ${escapeHtml(draft.contact?.name || "Relationship contact")}</span><span><strong>Email:</strong> ${escapeHtml(recipient || "not on file")}</span></div></div><div class="draft-detail-pills">${pill(draft.status)}${groundingPill(groundingStatusOf(draft))}</div></div>
    <div class="review-guide"><strong>Review order:</strong> Read the email, confirm the cited evidence supports every firm-specific claim, then make the approval decision.</div>
    <div class="detail-section-heading"><h4>Subject</h4><span class="quiet">${escapeHtml(humanize(draft.subject_status))}, editable before you copy</span></div>
    <input id="draft-subject-line" class="subject-input" type="text" value="${escapeHtml(subject)}" aria-label="Email subject line">
    <div class="detail-section-heading"><h4>Email body</h4><span class="quiet">Reviewable draft only. Nothing is sent.</span></div><div class="document">${escapeHtml(draft.email_body)}</div>
    <div class="detail-section-heading"><h4>Validator results</h4><div class="validator-list">${checks.map((check) => `<span class="validator">✓ ${escapeHtml(humanize(check))}</span>`).join("")}</div></div>
    <div class="detail-section-heading"><h4>Evidence and provenance</h4><span class="quiet">Internal review record</span></div><div class="evidence">${escapeHtml(draft.evidence_block)}</div>
    ${advisoryPanel}
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
      ${sendCapNotice()}
      ${sent ? "" : `<div class="review-actions"><button class="button primary" id="mark-sent">I sent this</button></div>`}
    </div>`;
}

// Sending past the per-mailbox daily cap damages domain reputation, which costs
// deliverability on live sponsor threads. Advisory, because the send happens in
// Gmail where Bridge cannot enforce anything.
function sendCapNotice() {
  const { sent_today: today = 0, daily_send_cap: cap = 40 } = state?.counts || {};
  if (today >= cap) {
    return `<p class="send-warning">You have recorded ${today} sends today, at the ${cap} daily cap for this mailbox. Continue tomorrow to protect deliverability.</p>`;
  }
  if (today >= cap * 0.8) {
    return `<p class="send-note">${today} of ${cap} recorded sends used today.</p>`;
  }
  return "";
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
      // encodeURIComponent, not URLSearchParams: the latter encodes a space as
      // "+", and a body rendered with literal plus signs would be unusable.
      const query = [
        "view=cm", "fs=1",
        `to=${encodeURIComponent(recipient)}`,
        `su=${encodeURIComponent(values.subject)}`,
        `body=${encodeURIComponent(draft.email_body)}`,
      ].join("&");
      compose.href = `https://mail.google.com/mail/?${query}`;
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
      <div class="manual-next-step"><strong>What to resolve</strong><p>${escapeHtml((row.gaps || []).length ? row.gaps.join(" · ") : "Review the reason, correct the source-stage issue, and rerun the eligible pipeline action.")}</p>
      <button class="button secondary resolve-manual" type="button" data-id="${row.id}" data-firm="${escapeHtml(row.firm)}">Mark resolved</button></div>
    </article>
  `).join("") : emptyState("Manual queue is clear", "Every visible firm can continue through the standard workflow.");

  $$(".resolve-manual").forEach((button) => button.addEventListener("click", () => {
    const note = window.prompt(`How was ${button.dataset.firm} resolved? This is kept as the audit note.`)?.trim();
    if (!note) return showToast("A resolution note is required.", true);
    resolveManualItem(button.dataset.id, note);
  }));
}

async function resolveManualItem(itemId, note) {
  setLoading(true);
  try {
    await api(`/api/manual-queue/${itemId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ note }),
    });
    showToast("Item resolved and note recorded.");
    await loadState();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
}

/** The lane switcher lists every lane this account may act in (own lane plus
 * any granted by profile_lane_access) and lets a trusted account toggle
 * between them, replacing the old one-off cross-owner confirmation flow. */
function renderLaneSwitcher() {
  const lanes = state.user.available_lanes?.length ? state.user.available_lanes : [state.user.owner];
  const switcher = $("#lane-switcher");
  switcher.innerHTML = lanes.map((lane) =>
    `<option value="${escapeHtml(lane)}" ${lane === state.user.owner ? "selected" : ""}>${escapeHtml(lane)} lane</option>`
  ).join("");
  switcher.disabled = lanes.length < 2;
}

function renderIdentity() {
  $("#user-name").textContent = state.user.actor_display_name || state.user.display_name;
  $("#user-email").textContent = state.user.email;
  renderLaneSwitcher();
  const initialsSource = state.user.actor_display_name || state.user.display_name;
  $("#user-avatar").textContent = initialsSource.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function renderAll() {
  renderIdentity();
  renderOverview();
  renderPipeline();
  renderLibrary();
  renderDrafts();
  renderManualQueue();
  const counts = state.counts || {};
  $("#nav-target-count").textContent = counts.pipeline_targets ?? 0;
  $("#nav-library-count").textContent = counts.targets ?? 0;
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

// ── Batch actions ─────────────────────────────────────────────────────────────
// The API caps a request at 25 target ids, and research crawls each firm's site
// at one request per second, so a large selection has to be sent in chunks. That
// is also what makes real progress possible: without it a thirty firm research
// run is several minutes of a page that looks frozen.

const BATCH_ACTIONS = {
  "/api/derive-status": {
    title: "Deriving contact status",
    detail: "Matching each firm against the CRM snapshot. No credits, no web requests.",
    done: "Contact statuses derived from the CRM snapshot.",
    chunk: 25,
  },
  "/api/research": {
    title: "Researching firms",
    detail: "Crawling each firm's own site at one request per second. This takes a while.",
    done: "Research completed. Review confidence and manual routing.",
    chunk: 5,
  },
};

function showBatchProgress(config, total) {
  const panel = $("#batch-progress");
  panel.hidden = false;
  panel.classList.remove("is-done", "is-failed");
  $("#batch-progress-title").textContent = config.title;
  $("#batch-progress-detail").textContent = config.detail;
  $("#batch-progress-count").textContent = `0 of ${total}`;
  $("#batch-bar-fill").style.width = "0%";
  $("#batch-progress-log").innerHTML = "";
}

function updateBatchProgress(done, total) {
  $("#batch-progress-count").textContent = `${done} of ${total}`;
  $("#batch-bar-fill").style.width = `${Math.round((done / total) * 100)}%`;
}

/** Append one firm's outcome as it lands, so progress is visible, not inferred. */
function logBatchRow(name, message, failed = false) {
  const row = document.createElement("div");
  row.className = `batch-log-row${failed ? " is-failed" : ""}`;
  row.innerHTML = `<b>${escapeHtml(name)}</b><span>${escapeHtml(message)}</span>`;
  $("#batch-progress-log").append(row);
  $("#batch-progress-log").scrollTop = $("#batch-progress-log").scrollHeight;
}

function finishBatchProgress(total, errorCount) {
  const panel = $("#batch-progress");
  panel.classList.add(errorCount ? "is-failed" : "is-done");
  $("#batch-progress-title").textContent = errorCount
    ? `Finished with ${errorCount} problem(s)`
    : "Finished";
  $("#batch-progress-detail").textContent = errorCount
    ? `${total - errorCount} of ${total} firm(s) succeeded. The rest are in the manual queue with a reason.`
    : `All ${total} firm(s) completed.`;
  $("#batch-bar-fill").style.width = "100%";
}

function describeBatchResult(row) {
  if (row.error) return row.error;
  if (row.confidence) {
    return `research ${row.confidence}, ${row.hooks ?? 0} hook(s)`;
  }
  if (row.contact_status) return `status: ${humanize(row.contact_status)}`;
  return "done";
}

function firmNameFor(targetId, row) {
  if (row && row.firm) return row.firm;
  const target = (state.targets || []).find((item) => item.id === targetId);
  return target ? target.firm : targetId;
}

async function runBatch(path, ids) {
  const config = BATCH_ACTIONS[path];
  const total = ids.length;
  const chunks = [];
  for (let i = 0; i < total; i += config.chunk) chunks.push(ids.slice(i, i + config.chunk));

  setBatchButtonsDisabled(true);
  setLoading(true);
  showBatchProgress(config, total);

  let done = 0;
  const errors = [];
  try {
    for (const chunk of chunks) {
      let result;
      try {
        result = await api(path, {
          method: "POST",
          body: JSON.stringify({ target_ids: chunk }),
        });
      } catch (error) {
        // A whole chunk failing must not abandon the chunks after it.
        chunk.forEach((id) => {
          errors.push(id);
          logBatchRow(firmNameFor(id), error.message, true);
        });
        done += chunk.length;
        updateBatchProgress(done, total);
        continue;
      }
      for (const row of result.results || []) {
        const failed = Boolean(row.error);
        if (failed) errors.push(row.target_id);
        logBatchRow(firmNameFor(row.target_id, row), describeBatchResult(row), failed);
      }
      done += chunk.length;
      updateBatchProgress(done, total);
    }

    finishBatchProgress(total, errors.length);
    showToast(
      errors.length
        ? `${errors.length} firm(s) need manual review. Successful firms were preserved.`
        : config.done,
      errors.length > 0,
    );
    await loadState();
  } catch (error) {
    finishBatchProgress(total, total - done);
    showToast(error.message, true);
  } finally {
    setBatchButtonsDisabled(false);
    setLoading(false);
    updateSelectionControls();
  }
}

async function applyBatchStatus(field, selectedValue) {
  const ids = selectedIds();
  if (!selectedValue) return showToast("Choose a status first.", true);
  const clear = selectedValue === "__automatic__";
  const label = field === "relationship_status" ? "relationship status" : "pipeline stage";
  const change = clear ? "return to automatic" : `change to ${selectedValue}`;
  if (!window.confirm(`Apply this ${label} change to ${ids.length} selected firm(s): ${change}? Every firm will receive its own audit event.`)) return;

  const progress = {
    title: `Updating ${label}`,
    detail: "Applying owner-scoped manual overrides through the existing partial-success batch framework.",
  };
  setBatchButtonsDisabled(true);
  setLoading(true);
  showBatchProgress(progress, ids.length);
  try {
    const result = await api("/api/status-overrides/batch", {
      method: "POST",
      body: JSON.stringify({ target_ids: ids, field, value: clear ? null : selectedValue, clear }),
    });
    let done = 0;
    let errors = 0;
    for (const row of result.results || []) {
      const failed = Boolean(row.error);
      if (failed) errors += 1;
      logBatchRow(firmNameFor(row.target_id || row.id, row), failed ? row.error : `${label}: ${clear ? "automatic" : selectedValue}`, failed);
      done += 1;
      updateBatchProgress(done, ids.length);
    }
    finishBatchProgress(ids.length, errors);
    showToast(errors ? `${errors} firm(s) need manual review; successful changes were preserved.` : `${ids.length} audited status change(s) completed.`, errors > 0);
    await loadState();
  } catch (error) {
    finishBatchProgress(ids.length, ids.length);
    showToast(error.message, true);
  } finally {
    setBatchButtonsDisabled(false);
    setLoading(false);
  }
}

function setBatchButtonsDisabled(disabled) {
  ["#derive-selected", "#research-selected", "#contacts-selected", "#apply-batch-relationship", "#apply-batch-stage"]
    .forEach((selector) => { $(selector).disabled = disabled; });
}

function openDraftDialog(targetId) {
  const target = state.targets.find((item) => item.id === targetId);
  const contacts = (state.contacts || []).filter((item) => item.target_id === targetId && !item.dropped);
  $("#draft-target-id").value = targetId;
  $("#draft-dialog-title").textContent = `Generate for ${target.firm}`;
  const cold = target.contact_status === "cold_prospect";
  $("#draft-contact-label").classList.toggle("hidden", !cold);
  $("#draft-contact").classList.toggle("hidden", !cold);
  $("#draft-add-contact").classList.toggle("hidden", !cold);
  $("#draft-paragraph-label").classList.toggle("hidden", !cold);
  $("#draft-paragraph").classList.toggle("hidden", !cold);
  $("#draft-paragraph-help").classList.toggle("hidden", !cold);
  $("#draft-paragraph").required = cold;
  $("#draft-paragraph").value = "";
  $("#draft-validation-error").textContent = "";
  $("#draft-contact").innerHTML = contacts.length
    ? contacts.map((contact) => `<option value="${contact.id}">${escapeHtml(contact.name)} · ${escapeHtml(contact.title)} · ${escapeHtml(contact.email)}</option>`).join("")
    : `<option value="">No contact yet, add one</option>`;
  renderDraftHooks(targetId);
  renderDraftPreview();
  $("#draft-dialog").showModal();
}

/** Show the sourced alignment hooks for a target, the only facts a firm-specific paragraph may draw on. */
function renderDraftHooks(targetId) {
  const panel = $("#draft-hooks-panel");
  const target = state.targets?.find((item) => item.id === targetId);
  const artifact = (researchByTarget().get(targetId) || {}).artifact;
  const hooks = artifact?.alignment_hooks || [];
  if (target?.contact_status !== "cold_prospect") {
    panel.classList.add("hidden");
    $("#draft-hooks-list").innerHTML = "";
    $("#draft-provenance-summary").textContent = "";
    return;
  }
  panel.classList.remove("hidden");
  if (!hooks.length) {
    // Research legitimately returns nothing for firms with a thin public
    // footprint. Neutral status, not an error state.
    $("#draft-hooks-list").innerHTML = `<p class="quiet">No stored research for this firm. Write the paragraph as usual.</p>`;
    updateDraftProvenanceSummary();
    return;
  }
  $("#draft-hooks-list").innerHTML = hooks.map((hook) => {
    const basis = String(hook.basis || "stored research").replaceAll("_", " ");
    const hookId = hook.research_hook_id || "";
    return `
      <label class="hook-item">
        <input class="draft-hook-checkbox" type="checkbox" value="${escapeHtml(hookId)}" ${hookId ? "checked" : "disabled"}>
        <span>
          <span class="hook-source-label">${escapeHtml(basis)}</span>
          <p>${escapeHtml(hook.text)}</p>
          <a href="${escapeHtml(safeHttpUrl(hook.firm_claim_source))}" target="_blank" rel="noopener noreferrer">${escapeHtml(hook.firm_claim_source)}</a>
        </span>
      </label>`;
  }).join("");
  $$(".draft-hook-checkbox").forEach((checkbox) => checkbox.addEventListener("change", () => {
    $("#draft-validation-error").textContent = "";
    updateDraftProvenanceSummary();
  }));
  updateDraftProvenanceSummary();
}

function selectedDraftHookIds() {
  return $$(".draft-hook-checkbox:checked").map((checkbox) => checkbox.value).filter(Boolean);
}

function updateDraftProvenanceSummary() {
  const selected = selectedDraftHookIds();
  const count = selected.length;
  const message = count
    ? `${count} stored research hook${count === 1 ? "" : "s"} selected. Bridge will map each supported sentence to these sources.`
    : "No stored research selected. The draft will be recorded as ungrounded for the reviewer.";
  $("#draft-provenance-summary").textContent = message;
  $("#draft-preview-provenance").textContent = count
    ? `${count} source${count === 1 ? "" : "s"} selected`
    : "No stored source";
}

/** Live-fill the locked template with the paragraph being written, so it reads like a finished email while drafting. */
function renderDraftPreview() {
  const panel = $("#draft-preview-panel");
  const targetId = $("#draft-target-id").value;
  const target = state.targets?.find((item) => item.id === targetId);
  if (!target || target.contact_status !== "cold_prospect" || !config?.cold_prospect_template) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  updateDraftProvenanceSummary();
  const contactId = $("#draft-contact").value;
  const contact = (state.contacts || []).find((item) => item.id === contactId);
  const firstName = contact?.name ? contact.name.trim().split(/\s+/)[0] : "[Contact]";
  const paragraph = $("#draft-paragraph").value.trim()
    || "[Your firm-specific paragraph will appear here.]";

  let filled = escapeHtml(config.cold_prospect_template);
  filled = filled.replaceAll("{contact_first_name}", escapeHtml(firstName));
  filled = filled.replaceAll("{firm_name}", escapeHtml(target.firm));
  filled = filled.replaceAll("{firm_specific_paragraph}", `<mark>${escapeHtml(paragraph)}</mark>`);
  filled = filled.replace(/\{[a-z_]+\}/g, (token) => `<span class="preview-auto">${token}</span>`);
  $("#draft-preview").innerHTML = filled.replaceAll("\n", "<br>");
}

/** Open the manual-contact dialog, listing what's already on file for this target. */
function openContactFormDialog(targetId, firmName) {
  $("#contact-form-target-id").value = targetId;
  $("#contact-form-heading").textContent = firmName ? `Add a contact for ${firmName}` : "Add a contact";
  $("#contact-form-name").value = "";
  $("#contact-form-title").value = "";
  $("#contact-form-email").value = "";
  $("#contact-form-source").value = "";
  renderContactFormExisting(targetId);
  $("#contact-form-dialog").showModal();
}

function renderContactFormExisting(targetId) {
  const existing = (state.contacts || []).filter((item) => item.target_id === targetId);
  $("#contact-form-existing").innerHTML = existing.length ? `
    <label>Already on file</label>
    <ul class="contact-list">
      ${existing.map((contact) => `
        <li>
          <span>${escapeHtml(contact.name)} · ${escapeHtml(contact.title || "no title")} · ${escapeHtml(contact.email || "no email")}
            ${contact.verification_provider === "human" ? '<span class="pill manual">manual</span>' : ""}</span>
          <button class="text-button danger remove-contact" type="button" data-id="${contact.id}">Remove</button>
        </li>`).join("")}
    </ul>` : "";
  $$(".remove-contact").forEach((button) => button.addEventListener("click", () => removeContactRow(button.dataset.id, targetId)));
}

async function removeContactRow(contactId, targetId) {
  if (!window.confirm("Remove this contact?")) return;
  setLoading(true);
  try {
    await api(`/api/contacts/${contactId}`, { method: "DELETE" });
    showToast("Contact removed.");
    await loadState();
    renderContactFormExisting(targetId);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setLoading(false);
  }
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
    selectedFirmId = null;
    firmDetail = null;
    setAuthenticated(false);
    history.replaceState(null, "", window.location.pathname);
  }
}

function bindEvents() {
  $$('[data-close]').forEach((button) => button.addEventListener("click", () => {
    document.getElementById(button.dataset.close).close();
  }));
  $("#google-signin").addEventListener("click", () => {
    $("#login-error").textContent = "";
    try {
      startGoogleSignIn();
    } catch (error) {
      $("#login-error").textContent = error.message;
    }
  });
  $("#logout-button").addEventListener("click", logout);
  $("#refresh-button").addEventListener("click", () => loadState().catch((error) => showToast(error.message, true)));
  $("#lane-switcher").addEventListener("change", (event) => setLane(event.target.value));
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
  $$('[data-outreach-filter]').forEach((button) => button.addEventListener("click", () => {
    pipelineOutreach = button.dataset.outreachFilter;
    renderPipeline();
  }));
  $("#apply-batch-relationship").addEventListener("click", () => {
    try { applyBatchStatus("relationship_status", $("#batch-relationship-status").value); }
    catch (error) { showToast(error.message, true); }
  });
  $("#apply-batch-stage").addEventListener("click", () => {
    try { applyBatchStatus("pipeline_stage", $("#batch-pipeline-stage").value); }
    catch (error) { showToast(error.message, true); }
  });
  $("#library-search").addEventListener("input", (event) => {
    librarySearch = event.target.value;
    renderLibrary();
  });
  const libraryBindings = [
    ["#library-relationship", "relationship"], ["#library-stage", "stage"],
    ["#library-owner", "owner"], ["#library-asset-class", "assetClass"],
    ["#library-region", "region"], ["#library-tier", "tier"],
  ];
  libraryBindings.forEach(([selector, key]) => $(selector).addEventListener("change", (event) => {
    libraryFilters[key] = event.target.value;
    renderLibrary();
  }));
  $("#library-clear-filters").addEventListener("click", () => {
    librarySearch = "";
    $("#library-search").value = "";
    Object.keys(libraryFilters).forEach((key) => { libraryFilters[key] = "all"; });
    renderLibrary();
  });
  $("#back-to-library").addEventListener("click", () => switchView("library"));
  $("#derive-selected").addEventListener("click", () => {
    try { runBatch("/api/derive-status", selectedIds()); }
    catch (error) { showToast(error.message, true); }
  });
  $("#research-selected").addEventListener("click", () => {
    try { runBatch("/api/research", selectedIds()); }
    catch (error) { showToast(error.message, true); }
  });
  $("#contacts-selected").addEventListener("click", async () => {
    const ids = selectedIds();
    // Contact runs are not chunked: the credit cap you confirm applies to one
    // run, so splitting the selection would split the cap you agreed to.
    if (ids.length > 25) {
      return showToast(
        `Finding contacts runs 25 firms at a time because you confirm one credit cap per run. `
        + `Narrow the selection from ${ids.length} to 25 or fewer.`,
        true,
      );
    }
    try {
      setLoading(true);
      const preview = await api("/api/contacts/preview", { method: "POST", body: JSON.stringify({ target_ids: ids }) });
      $("#contact-run-id").value = preview.run_id;
      const remaining = preview.hunter_balance?.remaining;
      $("#contact-cap").max = preview.credits_max;
      $("#contact-cap").value = Math.min(preview.credits_max, remaining ?? preview.credits_max);
      $("#contact-preview").innerHTML = `
        <p><strong>${preview.eligible.length}</strong> eligible firm(s), <strong>${preview.skipped.length}</strong> skipped by the gate.</p>
        <p>Live Hunter balance: <strong>${remaining ?? "unavailable"}</strong>. This run can spend between ${preview.credits_min} and ${preview.credits_max} credits, but it will hard stop at the cap you confirm.</p>
        ${preview.skipped.length ? `<details><summary>Skipped reasons</summary><ul>${preview.skipped.map((item) => `<li>${escapeHtml(item.reason)}</li>`).join("")}</ul></details>` : ""}
        ${(preview.notices || []).length ? `<p class="quiet">${escapeHtml(preview.notices[0].notice)} Applies to ${preview.notices.length} firm(s).</p>` : ""}`;
      $("#contact-dialog").showModal();
    } catch (error) { showToast(error.message, true); }
    finally { setLoading(false); }
  });

  $$(".intake-tab").forEach((tab) => {
    tab.addEventListener("click", () => selectIntakeTab(tab.dataset.intakeTab));
  });
  ["#intake-email-format", "#intake-email-source", "#intake-domain", "#intake-website"]
    .forEach((selector) => $(selector).addEventListener("input", renderFormatNote));
  $("#intake-check").addEventListener("click", checkBulkIntake);

  $("#intake-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    let firms;
    try {
      firms = intakeMode === "bulk" ? readBulkIntake() : readSingleIntake();
    } catch (error) { return showToast(error.message, true); }
    if (!firms.length) {
      return showToast(
        intakeMode === "bulk" ? "Paste at least one firm." : "Enter a firm name.", true,
      );
    }
    setLoading(true);
    try {
      const result = await api("/api/intake", {
        method: "POST",
        body: JSON.stringify({ firms, run_research: $("#intake-research").checked }),
      });
      renderIntakeResults(result);
      const added = result.targets.length;
      const skipped = (result.skipped || []).length;
      showToast(
        skipped
          ? `${added} firm(s) added, ${skipped} skipped. See the results below.`
          : `${added} firm(s) added to ${state.user.owner}'s lane.`,
        added === 0,
      );
      if (added) clearIntakeForm();
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

  $("#draft-paragraph").addEventListener("input", () => {
    $("#draft-validation-error").textContent = "";
    renderDraftPreview();
  });
  $("#draft-contact").addEventListener("change", renderDraftPreview);
  $("#draft-add-contact").addEventListener("click", () => {
    const targetId = $("#draft-target-id").value;
    const target = state.targets.find((item) => item.id === targetId);
    openContactFormDialog(targetId, target?.firm || "");
  });

  $("#contact-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const targetId = $("#contact-form-target-id").value;
    const name = $("#contact-form-name").value.trim();
    if (!name) return showToast("A name is required.", true);
    setLoading(true);
    try {
      await api(`/api/targets/${targetId}/contacts`, {
        method: "POST",
        body: JSON.stringify({
          name,
          title: $("#contact-form-title").value.trim(),
          email: $("#contact-form-email").value.trim(),
          source_note: $("#contact-form-source").value.trim(),
        }),
      });
      showToast(`${name} added.`);
      await loadState();
      renderContactFormExisting(targetId);
      $("#contact-form-name").value = "";
      $("#contact-form-title").value = "";
      $("#contact-form-email").value = "";
      $("#contact-form-source").value = "";
      // The draft dialog may be open underneath this one; keep its contact
      // picker and preview in sync rather than making the operator reopen it.
      if ($("#draft-dialog").open && $("#draft-target-id").value === targetId) {
        const contacts = (state.contacts || []).filter((item) => item.target_id === targetId);
        $("#draft-contact").innerHTML = contacts.length
          ? contacts.map((contact) => `<option value="${contact.id}">${escapeHtml(contact.name)} · ${escapeHtml(contact.title)} · ${escapeHtml(contact.email)}</option>`).join("")
          : `<option value="">No contact yet, add one</option>`;
        renderDraftPreview();
      }
    } catch (error) {
      showToast(error.message, true);
    } finally {
      setLoading(false);
    }
  });

  $("#draft-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const targetId = $("#draft-target-id").value;
    const target = state.targets.find((item) => item.id === targetId);
    if (target.contact_status === "cold_prospect" && !$("#draft-paragraph").value.trim()) {
      return showToast("Write the firm-specific paragraph before generating.", true);
    }
    const supportingHookIds = target.contact_status === "cold_prospect"
      ? selectedDraftHookIds()
      : [];
    $("#draft-validation-error").textContent = "";
    setLoading(true);
    try {
      const result = await api("/api/drafts", {
        method: "POST",
        body: JSON.stringify({
          target_id: targetId,
          contact_id: $("#draft-contact").value || null,
          firm_specific_paragraph: $("#draft-paragraph").value.trim() || null,
          supporting_hook_ids: supportingHookIds,
        }),
      });
      selectedDraftId = result.id;
      $("#draft-dialog").close();
      const grounding = result.fields?.firm_paragraph_provenance?.grounding_status;
      showToast(grounding && grounding !== "grounded"
        ? `Draft added to review. ${groundingNotice(grounding)}`
        : "Validator-passing draft added to review.");
      await loadState();
      switchView("drafts");
    } catch (error) {
      $("#draft-validation-error").textContent = error.message;
      showToast("Draft validation failed. Review the sentence-level provenance message.", true);
    }
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

  $("#meeting-note-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const noteId = $("#meeting-note-id").value;
    const targetId = $("#meeting-note-target-id").value;
    const payload = {
      interaction_date: $("#meeting-note-date").value,
      interaction_type: $("#meeting-note-type").value.trim() || "Meeting",
      participants: $("#meeting-note-participants").value.split(",").map((value) => value.trim()).filter(Boolean),
      notes: $("#meeting-note-notes").value.trim(),
      next_step: $("#meeting-note-next-step").value.trim(),
      follow_up_date: $("#meeting-note-follow-up").value || null,
    };
    if (!payload.interaction_date || !payload.notes) return showToast("A date and meeting notes are required.", true);
    setLoading(true);
    try {
      await api(noteId ? `/api/meeting-notes/${noteId}` : `/api/firms/${targetId}/meeting-notes`, {
        method: noteId ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      $("#meeting-note-dialog").close();
      showToast(noteId ? "Meeting note updated." : "Meeting note added.");
      await openFirmRecord(targetId);
    } catch (error) { showToast(error.message, true); }
    finally { setLoading(false); }
  });
}

async function boot() {
  bindEvents();
  try {
    await loadConfig();
    const gotRedirectSession = consumeGoogleRedirect();
    if (!gotRedirectSession) {
      const stored = localStorage.getItem(SESSION_KEY);
      if (stored) session = JSON.parse(stored);
    }
    activeLane = localStorage.getItem(LANE_KEY) || null;
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
