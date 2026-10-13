"use strict";

function canonicalProfileUrl(value) {
  let url;
  try { url = new URL(value.includes("://") ? value.trim() : "https://" + value.trim()); }
  catch { throw new Error("Expected a LinkedIn /in/ profile URL."); }
  if (url.protocol !== "https:" || !/^(?:linkedin\.com|(?:www|[a-z]{2})\.linkedin\.com)$/.test(url.hostname) || url.username || url.password || url.port) {
    throw new Error("Expected an HTTPS LinkedIn /in/ profile URL.");
  }
  const match = url.pathname.match(/^\/in\/([^/]+)\/?$/);
  if (!match) throw new Error("Expected /in/profile-slug/ with no extra path.");
  let slug;
  try { slug = decodeURIComponent(match[1]); } catch { throw new Error("Invalid profile slug."); }
  if (/[\s\x00-\x1f/\\?#]/.test(slug)) throw new Error("Invalid profile slug.");
  return "https://www.linkedin.com/in/" + encodeURIComponent(slug) + "/";
}

function parseProfileLinks(text) {
  const links = [], errors = [];
  text.replace(/^\uFEFF/, "").split(/\r?\n/).forEach((line, index) => {
    if (!line.trim() || line.trim().startsWith("#")) return;
    try { const url = canonicalProfileUrl(line); if (!links.includes(url)) links.push(url); }
    catch (error) { errors.push(`Line ${index + 1}: ${error.message}`); }
  });
  if (errors.length) throw new Error(errors.join("\n"));
  if (!links.length) throw new Error("Paste at least one profile link.");
  return links;
}

class ExistingBrowserQueue {
  constructor(api, onChange) {
    this.api = api;
    this.onChange = onChange;
    this.stopRequested = false;
    this.state = { busy: false, mode: "idle", phase: "idle", message: "Paste links to get started.", total: 0, processed: 0, results: [] };
    this.skip = new Set(["sent", "pending", "connected", "sending", "review_required"]);
    this.tabReplacements = new Map();
    // Chrome may swap a tab during prerendered navigation. Its old ID then
    // stops working even though the profile remains open in the browser.
    this.api.tabs.onReplaced?.addListener((addedId, removedId) => {
      this.tabReplacements.set(removedId, addedId);
    });
  }
  emit() { this.onChange({ ...this.state, results: this.state.results.map((row) => ({ ...row })) }); }
  stop() {
    if (!this.state.busy) return;
    this.stopRequested = true;
    this.state.phase = "stopping";
    this.state.message = "Stopping after the current profile finishes...";
    this.emit();
  }
  async result(url, status, detail, history) {
    history[url] = { url, status, detail, time: new Date().toISOString() };
    await this.api.storage.local.set({ connectionHistory: history });
    Object.assign(this.state.results.find((row) => row.url === url), { status, detail });
    this.emit();
  }
  resolveTabId(id) {
    const seen = new Set();
    while (this.tabReplacements.has(id) && !seen.has(id)) {
      seen.add(id);
      id = this.tabReplacements.get(id);
    }
    return id;
  }
  missingTab(error) { return /no tab with id|invalid tab id|tab (?:was |has been )?(?:closed|removed)|invalid tab/i.test(error.message); }
  async profileTab(reference, url, recover = true) {
    let id = this.resolveTabId(reference.id);
    try {
      const tab = await this.api.tabs.get(id);
      reference.id = tab.id;
      return tab;
    } catch (error) {
      if (!this.missingTab(error)) throw error;
    }
    // onReplaced can arrive just after the failed API call. Resolve again
    // before using the exact-profile fallback; never choose another profile.
    await new Promise((resolve) => setTimeout(resolve, 150));
    const replacement = this.resolveTabId(id);
    if (replacement !== id) {
      const tab = await this.api.tabs.get(replacement);
      reference.id = tab.id;
      return tab;
    }
    if (recover) {
      const tabs = await this.api.tabs.query({ windowId: reference.windowId, url: "https://*.linkedin.com/in/*" });
      const matching = tabs.filter((tab) => {
        try { return canonicalProfileUrl(tab.url || "") === url; } catch { return false; }
      });
      if (matching.length === 1) {
        this.tabReplacements.set(id, matching[0].id);
        reference.id = matching[0].id;
        return matching[0];
      }
      if (matching.length > 1) throw new Error("Several tabs show this profile. Close duplicate profile tabs, then use Preview again.");
    }
    throw new Error("The LinkedIn tab was closed or replaced and could not be recovered. Keep it open, then use Preview again.");
  }
  async inject(reference, url, send, note) {
    let results;
    try {
      results = await this.api.scripting.executeScript({ target: { tabId: this.resolveTabId(reference.id), frameIds: [0] },
        func: inspectOrSendProfile, args: [{ url, send, note }] });
    } catch (error) {
      // Retrying inspection is safe. Once a send is reserved, never repeat
      // executeScript: the previous execution may already have submitted it.
      if (send || !this.missingTab(error)) throw error;
      await this.profileTab(reference, url);
      results = await this.api.scripting.executeScript({ target: { tabId: reference.id, frameIds: [0] },
        func: inspectOrSendProfile, args: [{ url, send: false, note: null }] });
    }
    const answer = results.find((entry) => entry.frameId === 0);
    if (answer?.error || !answer?.result?.status) throw new Error("The LinkedIn tab could not be inspected. Review it manually.");
    return answer.result;
  }
  async loaded(reference, url) {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      const tab = await this.profileTab(reference, url);
      if (tab.status === "complete") return;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    throw new Error("Profile loading timed out. Queue stopped.");
  }
  async start(payload) {
    if (this.state.busy) throw new Error("A queue is already running.");
    const links = parseProfileLinks(payload.links);
    if (!["preview", "send"].includes(payload.mode)) throw new Error("Choose Preview or Send requests.");
    const note = (payload.note || "").trim();
    const limit = payload.limit;
    const delay = payload.delay ?? 5;
    if (note.length > 200 || (limit !== null && limit !== undefined && (!Number.isInteger(limit) || limit < 1)) || !Number.isFinite(delay) || delay < 0 || delay > 3600) {
      throw new Error("Check the note, profile limit, and delay.");
    }
    const owner = await this.api.tabs.getCurrent();
    if (!Number.isInteger(owner?.id)) throw new Error("Close this queue page and reopen it using the extension's toolbar button.");
    const runId = crypto.randomUUID();
    const claimed = await this.api.runtime.sendMessage({ type: "queue.claim", ownerTabId: owner.id, runId });
    if (claimed?.error) throw new Error(claimed.error);
    this.stopRequested = false;
    this.state = { busy: true, mode: payload.mode, phase: "running", message: "Opening a LinkedIn tab in this browser...", total: 0, processed: 0,
      results: links.map((url) => ({ url, status: "queued", detail: "Waiting." })) };
    this.emit();
    let current = null, attempted = false, history = {}, stage = "reading saved history";
    try {
      const stored = await this.api.storage.local.get("connectionHistory");
      history = stored.connectionHistory || {};
      if (typeof history !== "object" || Array.isArray(history)) throw new Error("Saved history is invalid. Review it before running.");
      let pending = links.filter((url) => {
        if (!this.skip.has(history[url]?.status)) return true;
        Object.assign(this.state.results.find((row) => row.url === url), { status: history[url].status, detail: "Skipped from saved history." });
        return false;
      });
      if (limit) {
        pending.slice(limit).forEach((url) => Object.assign(this.state.results.find((row) => row.url === url), { status: "not_selected", detail: "Outside this run's profile limit." }));
        pending = pending.slice(0, limit);
      }
      this.state.total = pending.length;
      this.emit();
      let profileTab = null;
      for (let index = 0; index < pending.length; index++) {
        if (this.stopRequested) break;
        current = pending[index]; attempted = false;
        this.state.message = `Processing profile ${index + 1} of ${pending.length}...`;
        this.emit();
        if (profileTab === null) {
          stage = "opening the LinkedIn tab";
          const tab = await this.api.tabs.create({ url: "about:blank", active: false });
          profileTab = { id: tab.id, windowId: tab.windowId };
          // Let the new tab attach before navigating it; navigation and inspection
          // then operate on a stable tab rather than its initial empty document.
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        stage = "opening the profile";
        if (profileTab.url) await this.profileTab(profileTab, profileTab.url);
        let navigated;
        try { navigated = await this.api.tabs.update(this.resolveTabId(profileTab.id), { url: current }); }
        catch (error) {
          if (!this.missingTab(error)) throw error;
          await this.profileTab(profileTab, profileTab.url || current);
          navigated = await this.api.tabs.update(this.resolveTabId(profileTab.id), { url: current });
        }
        if (Number.isInteger(navigated?.id)) profileTab.id = navigated.id;
        stage = "waiting for the profile";
        await this.loaded(profileTab, current);
        profileTab.url = current;
        // LinkedIn's popover/focus handling needs a visible, interactive tab.
        // Bring the profile forward while inspecting; show progress afterward.
        await this.api.tabs.update(this.resolveTabId(profileTab.id), { active: true });
        stage = "checking Connect";
        let answer = await this.inject(profileTab, current, false, null);
        if (payload.mode === "send" && answer.status === "would_send" && !this.stopRequested) {
          await this.profileTab(profileTab, current);
          stage = "saving the attempt";
          await this.result(current, "sending", "Attempt started; inspect manually if interrupted.", history);
          attempted = true;
          stage = "sending the invitation";
          answer = await this.inject(profileTab, current, true, note || null);
        }
        if (attempted && !["sent", "pending", "connected", "review_required"].includes(answer.status)) {
          answer = { status: "review_required", detail: answer.detail + " The attempt is reserved; review manually before retrying." };
        }
        await this.result(current, answer.status, answer.detail, history);
        this.state.processed++;
        this.emit();
        await this.api.tabs.update(this.resolveTabId(owner.id), { active: true });
        if (["halted", "review_required"].includes(answer.status)) throw new Error(answer.detail);
        if (index + 1 < pending.length) {
          const until = Date.now() + delay * 1000;
          while (Date.now() < until && !this.stopRequested) await new Promise((resolve) => setTimeout(resolve, Math.min(200, until - Date.now())));
        }
      }
      this.state.phase = this.stopRequested ? "stopped" : "complete";
      this.state.message = this.stopRequested ? "Stopped. Run the remaining links again when ready." : payload.mode === "preview" ? "Preview complete. No requests sent." : "Queue complete. Results are shown below.";
    } catch (error) {
      let detail = this.missingTab(error)
        ? `The LinkedIn tab became unavailable while ${stage}. Keep it open, then use Preview again.`
        : error.message;
      if (attempted && history[current]?.status !== "sent") detail += " A send attempt was reserved; check this profile manually. It will not be retried automatically.";
      this.state.phase = "error";
      this.state.message = detail;
      if (current && !["halted", "review_required", "sent", "pending", "connected"].includes(history[current]?.status)) {
        try { await this.result(current, attempted ? "review_required" : "halted", detail, history); }
        catch {
          Object.assign(this.state.results.find((row) => row.url === current), { status: attempted ? "review_required" : "halted", detail });
          this.state.message += " Local history could not be saved; review manually before restarting.";
        }
      }
    } finally {
      try { await this.api.runtime.sendMessage({ type: "queue.release", ownerTabId: owner.id, runId }); }
      catch { /* Persistent sending history still prevents an automatic retry. */ }
      this.state.busy = false;
      this.emit();
    }
  }
}
