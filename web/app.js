"use strict";

const $ = (id) => document.getElementById(id);
const api = globalThis.browser?.runtime?.id ? globalThis.browser : globalThis.chrome?.runtime?.id ? globalThis.chrome : null;
let queue = null;
let online = false;
let pendingRequest = false;
let state = { busy: false, phase: "idle", mode: "idle", results: [] };
const phaseNames = { idle: "Ready", running: "Running", stopping: "Stopping", complete: "Complete", stopped: "Stopped", error: "Needs attention" };
const statusNames = { queued: "Queued", sending: "Sending", sent: "Sent", pending: "Skipped (pending)", connected: "Connected", would_send: "Ready to send", unavailable: "Unavailable", review_required: "Review needed", halted: "Stopped", not_selected: "Outside limit" };

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

function countLinks() {
  const count = $("links").value.split(/\r?\n/).filter((line) => line.trim() && !line.trim().startsWith("#")).length;
  $("links-count").textContent = `${count} link${count === 1 ? "" : "s"}`;
  return count;
}

function controls() {
  const blocked = !online || pendingRequest || state.busy;
  $("preview").disabled = blocked || countLinks() === 0;
  $("send").disabled = blocked || countLinks() === 0;
  ["links", "note", "limit", "delay", "clear"].forEach((id) => { $(id).disabled = state.busy || pendingRequest; });
  $("stop").hidden = !state.busy;
  $("stop").disabled = pendingRequest || state.phase === "stopping" || !online;
}

function render(next) {
  state = next;
  if (state.busy) pendingRequest = false;
  $("phase").textContent = phaseNames[state.phase] || state.phase;
  $("phase").className = `phase-badge${state.phase === "error" ? " error" : state.busy ? " active" : ""}`;
  $("status").textContent = state.message;
  $("progress").value = state.total ? Math.min(100, 100 * state.processed / state.total) : state.phase === "complete" ? 100 : 0;
  $("progress-label").textContent = state.total ? `${state.processed} of ${state.total} profiles processed` : state.results.length ? "Existing and uncertain requests are skipped." : "Results will appear here as each profile finishes.";
  $("empty").hidden = state.results.length > 0;
  const rows = document.createDocumentFragment();
  state.results.forEach((result) => {
    const row = document.createElement("li");
    const top = document.createElement("div");
    top.className = "result-top";
    const link = document.createElement("a");
    link.href = result.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = decodeURIComponent(new URL(result.url).pathname.split("/")[2]);
    link.title = result.url;
    const status = document.createElement("span");
    const category = ["sent", "connected", "would_send"].includes(result.status) ? " success" : ["review_required", "halted"].includes(result.status) ? " problem" : "";
    status.className = `result-status${category}`;
    status.textContent = statusNames[result.status] || result.status;
    const detail = document.createElement("p");
    detail.className = "result-detail";
    detail.textContent = result.detail;
    top.append(link, status);
    row.append(top, detail);
    rows.append(row);
  });
  $("results").replaceChildren(rows);
  controls();
}

async function start(mode) {
  const limit = $("limit").value.trim();
  const delay = $("delay").value.trim();
  if ((limit && (!$("limit").checkValidity() || !Number.isInteger(Number(limit)))) || !delay || !$("delay").checkValidity()) {
    showError("Use a positive whole profile limit, and a delay from 0 to 3600 seconds.");
    return;
  }
  pendingRequest = true;
  showError("");
  controls();
  try {
    await queue.start({ links: $("links").value, mode, note: $("note").value,
      limit: limit ? Number(limit) : null, delay: Number(delay) });
  } catch (error) { showError(error.message); }
  finally { pendingRequest = false; controls(); }
}

$("links").addEventListener("input", () => { countLinks(); showError(""); controls(); });
$("clear").addEventListener("click", () => { $("links").value = ""; countLinks(); showError(""); controls(); $("links").focus(); });
$("preview").addEventListener("click", () => start("preview"));
$("send").addEventListener("click", () => start("send"));
$("stop").addEventListener("click", () => queue.stop());

if (api) {
  $("version").textContent = `v${api.runtime.getManifest().version}`;
  queue = new ExistingBrowserQueue(api, render);
  online = true;
  render(queue.state);
} else {
  $("setup-hint").hidden = false;
  $("status").textContent = "Install the extension once, then open its toolbar button in Chrome or Firefox.";
  controls();
}
