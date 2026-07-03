/*
 * Shared routing-log renderer for the travel assistant pages.
 * --------------------------------------------------------------------------
 * Renders an orchestration response as a timestamped, collapsible workflow
 * tree (request -> orchestrator -> specialist agents -> outcome leaves).
 * Used by both the chat UI (travel-chat.html) and the voice UI
 * (travel-support.html) so the implementation lives in exactly one place.
 *
 * Public API (window.RoutingLog):
 *   render(logEl, data, userText, memoryTurns) -> append a new log entry
 *   reset(logEl, emptyText)                    -> clear the log + show empty state
 */
(function (global) {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // --- Shared leaf/branch builders (module scope so every tree builder uses them) ---
  // Child list that hangs under a node.
  const sub = (rows) =>
    rows.length ? `<ul class="route-tree route-children">${rows.join("")}</ul>` : "";

  const metaLeaf = (label, value, cls = "") =>
    `<li class="route-node route-meta-leaf ${cls}"><span class="route-key">${escapeHtml(label)}:</span> ${value}</li>`;

  const textLeaf = (label, value, cls = "") =>
    `<li class="route-node route-meta-leaf ${cls}">` +
    `<span class="route-key">${escapeHtml(label)}:</span>` +
    `<div class="route-text">${escapeHtml(value)}</div></li>`;

  // Shared "Request" header rendered identically on every log entry (orchestrator
  // and each specialist agent) so the request is shown consistently everywhere.
  const requestLeaf = (userText, memoryTurns) => {
    if (!userText) return "";
    const memoryLeaf = memoryTurns
      ? `<div class="route-meta-leaf route-memory"><span class="route-key">context:</span> ${memoryTurns} prior turn${memoryTurns === 1 ? "" : "s"} remembered</div>`
      : "";
    return (
      `<li class="route-node route-request">` +
      `<span class="route-label">Request</span>` +
      `<div class="route-message">"${escapeHtml(userText)}"</div>` +
      memoryLeaf +
      `</li>`
    );
  };

  // Body for the orchestrator's own log entry: the request, the routing decision,
  // and turn-level outcomes. Specialist agents get their own separate entries.
  function buildOrchestratorTree(orchestrator, data, userText, memoryTurns) {
    const rows = [];

    const request = requestLeaf(userText, memoryTurns);
    if (request) rows.push(request);

    const orchRows = [];
    if (orchestrator.route) orchRows.push(metaLeaf("route", `<code>${escapeHtml(orchestrator.route)}</code>`));
    if (Array.isArray(orchestrator.routes)) orchRows.push(metaLeaf("routes", `<code>${escapeHtml(orchestrator.routes.join(" + "))}</code>`));
    if (typeof orchestrator.parallel_agents === "number") orchRows.push(metaLeaf("parallel", String(orchestrator.parallel_agents)));
    if (orchestrator.rationale) orchRows.push(textLeaf("why", orchestrator.rationale, "route-rationale"));
    const agents = Array.isArray(data.selected_agents) ? data.selected_agents : [];
    if (agents.length) orchRows.push(metaLeaf("routed to", escapeHtml(agents.join(", "))));

    rows.push(
      `<li class="route-node route-root">` +
      `<span class="route-label">${escapeHtml(orchestrator.node || "Orchestrator")}</span>` +
      sub(orchRows) +
      `</li>`
    );

    // --- Turn-level outcome leaves ---
    const specialistCount = (Array.isArray(data.workflow_trace) ? data.workflow_trace.length - 1 : 0);
    // Only show the merged reply here when more than one agent contributed; a
    // single agent's reply lives in its own entry instead.
    if (data.spoken_reply && specialistCount > 1) rows.push(textLeaf("combined reply", data.spoken_reply, "route-reply"));
    if (data.clarification_question) rows.push(textLeaf("clarification", data.clarification_question, "route-clar"));
    if (data.orchestrator_mode) rows.push(metaLeaf("mode", `<code>${escapeHtml(data.orchestrator_mode)}</code>`));

    return `<ul class="route-tree">${rows.join("")}</ul>`;
  }

  // Body for a single specialist agent's log entry: the request (shown the same
  // way as on the orchestrator entry), a real derived status, its own response,
  // and any missing fields / errors.
  function buildAgentTree(node, output, data, isOnlyAgent, userText, memoryTurns) {
    const rows = [];

    const request = requestLeaf(userText, memoryTurns);
    if (request) rows.push(request);

    const missing = Array.isArray(output.missing_fields) ? output.missing_fields : [];
    const errText = node.error || output.error;
    const summary = output.summary || (isOnlyAgent ? data.spoken_reply : "");
    const children = [];

    // Real status derived from the agent's actual outcome — replaces the previous
    // synthetic confidence score, which was a fixed constant and not meaningful.
    let status = "responded";
    let statusCls = "route-ok";
    if (errText) {
      status = "failed";
      statusCls = "route-error";
    } else if (!summary) {
      status = "no response";
      statusCls = "route-missing";
    } else if (missing.length) {
      status = "needs more info";
      statusCls = "route-missing";
    }
    children.push(metaLeaf("status", escapeHtml(status), statusCls));

    if (summary) children.push(textLeaf("response", summary, "route-reply"));
    if (missing.length) children.push(metaLeaf("missing", escapeHtml(missing.join(", ")), "route-missing"));
    if (errText) children.push(metaLeaf("error", escapeHtml(errText), "route-error"));

    const labelCls = errText ? "route-label route-failed" : "route-label";
    rows.push(
      `<li class="route-node route-root">` +
      `<span class="${labelCls}">${escapeHtml(node.node)}</span>` +
      sub(children) +
      `</li>`
    );
    return `<ul class="route-tree">${rows.join("")}</ul>`;
  }

  function render(logEl, data, userText, memoryTurns) {
    if (!logEl) return;

    const empty = logEl.querySelector(".orch-empty");
    if (empty) empty.remove();

    const trace = Array.isArray(data.workflow_trace) ? data.workflow_trace : [];
    const outputs = Array.isArray(data.specialist_outputs) ? data.specialist_outputs : [];
    const outputByAgent = {};
    outputs.forEach((o) => {
      if (o && o.agent) outputByAgent[o.agent] = o;
    });

    const orchestrator = trace[0] || { node: "Orchestrator" };
    const specialists = trace.slice(1);
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

    // Collapse older entries so only the newest turn's entries stay expanded.
    logEl.querySelectorAll("details.route-entry[open]").forEach((el) => el.removeAttribute("open"));

    const appendEntry = (label, bodyHtml, open) => {
      const entry = document.createElement("details");
      entry.className = "route-entry";
      entry.open = open;
      entry.innerHTML =
        `<summary class="route-entry-head">` +
        `<span class="route-time">${escapeHtml(time)}</span>` +
        `<span class="route-head-agent">${escapeHtml(label)}</span>` +
        `</summary>` +
        `<div class="route-entry-body">${bodyHtml}</div>`;
      logEl.appendChild(entry);
    };

    // One entry for the orchestrator (request + routing decision)...
    appendEntry(
      orchestrator.node || "Orchestrator",
      buildOrchestratorTree(orchestrator, data, userText, memoryTurns),
      true
    );

    // ...then a separate entry for each specialist agent. Every entry from the
    // current turn stays expanded; only previous turns are collapsed above.
    specialists.forEach((s) => {
      appendEntry(
        s.node,
        buildAgentTree(s, outputByAgent[s.node] || {}, data, specialists.length === 1, userText, memoryTurns),
        true
      );
    });

    logEl.scrollTop = logEl.scrollHeight;
  }

  function reset(logEl, emptyText) {
    if (!logEl) return;
    const message = emptyText || "No routing data yet.";
    const div = document.createElement("div");
    div.className = "orch-empty";
    div.textContent = message;
    logEl.innerHTML = "";
    logEl.appendChild(div);
  }

  global.RoutingLog = { render, reset, escapeHtml, buildOrchestratorTree, buildAgentTree };
})(window);
