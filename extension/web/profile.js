"use strict";

// Serialized into LinkedIn's isolated content-script world; no external state.
async function inspectOrSendProfile(request) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let attempted = false;
  const connectPattern = /^(?:Connect|Invite .+ to connect)$/i;
  const pendingPattern = /^(?:Pending(?:,.*)?|Invitation pending|Invitation sent(?: to .+)?)$/i;
  const actionSelector = 'button, [role="button"], [role="menuitem"], a[href], input[type="button"], input[type="submit"]';
  const cardSelector = '[componentkey^="com.linkedin.sdui.profile.card."][componentkey$="Topcard"], [id^="com.linkedin.sdui.profile.card."][id$="Topcard"]';
  const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
  function visible(element) {
    if (!element || element.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
    const style = getComputedStyle(element);
    if (style.visibility === "hidden" || style.display === "none") return false;
    return element.getClientRects().length > 0 ||
      (style.display === "contents" && Array.from(element.children).some(visible));
  }
  function labels(element) {
    const root = element.getRootNode();
    const labelled = (element.getAttribute("aria-labelledby") || "").split(/\s+/)
      .filter(Boolean).map((id) => root.getElementById?.(id)?.textContent || "").join(" ");
    // The visible Connect/Send text remains usable when LinkedIn adds a
    // descriptive aria-label such as "Connect with NAME" to the same button.
    const copy = element.cloneNode(true);
    for (const icon of copy.querySelectorAll('svg, [aria-hidden="true"], [hidden], script, style')) icon.remove();
    return [element.innerText, element.getAttribute("aria-label"), labelled, copy.textContent,
      element instanceof HTMLInputElement ? element.value : ""].map(normalize).filter(Boolean);
  }
  const disabled = (element) => element.matches(":disabled") || !!element.closest('[aria-disabled="true"]');
  function roots() {
    const found = [document];
    // LinkedIn's legacy dialogs may live in this open interoperability root.
    // Inspect only these known hosts, without reading unrelated shadow trees.
    for (const host of document.querySelectorAll('#interop-outlet, [data-testid="interop-shadowdom"]')) {
      if (host.shadowRoot && !found.includes(host.shadowRoot)) found.push(host.shadowRoot);
    }
    return found;
  }
  const all = (selector) => [...new Set(roots().flatMap((root) => Array.from(root.querySelectorAll(selector))))];
  function one(elements, description) {
    const matches = Array.from(elements).filter(visible);
    if (matches.length > 1) throw new Error(`Multiple visible ${description}; stopped to avoid a wrong action.`);
    return matches[0] || null;
  }
  function buttons(root, pattern) {
    const matches = Array.from(root.querySelectorAll(actionSelector)).filter((element) =>
      visible(element) && labels(element).some((label) => pattern.test(label)));
    // A menuitem wrapper and its button represent one action. Different
    // matching buttons still stop the run rather than choosing arbitrarily.
    return one(matches.filter((element) => !matches.some((other) => other !== element && element.contains(other))), "matching actions");
  }
  function scopes(elements, description) {
    const matches = elements.filter(visible);
    return one(matches.filter((element) => !matches.some((other) => other !== element && other.contains(element))), description);
  }
  function dialog() {
    const explicit = scopes(all('[role="dialog"], [aria-modal="true"], dialog[open]'), "dialogs");
    if (explicit) return explicit;
    // Also recognize the exact invitation prompt supplied in the screenshot
    // when its container has no dialog role. Never search the page for Send.
    const candidates = [];
    for (const heading of all('h1, h2, h3, [role="heading"]')) {
      if (!visible(heading) || !/^Add a note to your invitation\?$/i.test(normalize(heading.innerText))) continue;
      for (let parent = heading.parentElement; parent && !parent.matches("body, main"); parent = parent.parentElement) {
        if (buttons(parent, /^Send without a note$/i) && buttons(parent, /^Add a note$/i)) {
          candidates.push(parent);
          break;
        }
      }
    }
    return scopes([...new Set(candidates)], "invitation prompts");
  }
  function actionSummary(root) {
    if (!root) return "none";
    const names = Array.from(root.querySelectorAll(actionSelector)).filter(visible)
      .map((element) => (labels(element)[0] || "unlabelled").slice(0, 80) + (disabled(element) ? " (disabled)" : ""));
    return [...new Set(names)].slice(0, 8).join(" / ") || "none";
  }
  function guard() {
    if (location.origin !== "https://www.linkedin.com" || location.pathname.replace(/\/$/, "") !== new URL(request.url).pathname.replace(/\/$/, "")) {
      throw new Error("The requested profile did not load. Resolve login or verification in your normal LinkedIn tab.");
    }
    if (one(all('input[type="password"], iframe[src*="captcha"]'), "login or verification fields")) {
      throw new Error("Login or verification is required in your normal LinkedIn tab.");
    }
    const alerts = all('[role="alert"], [role="dialog"], [aria-modal="true"], dialog[open], .artdeco-toast-item')
      .filter(visible).map((element) => element.innerText).join("\n");
    if (/weekly invitation limit|(?:reached|exceeded).{0,60}invitation limit|too many invitations|out of invitations|can't send.{0,30}invitations|unable to send.{0,30}invitation|invitation.{0,40}(?:not sent|failed)/i.test(alerts)) {
      throw new Error("LinkedIn reported an invitation limit or sending error. Queue stopped.");
    }
    if (/let.s do a quick security check|verify your identity|security verification|(?:account|access).{0,30}(?:restricted|suspended)/i.test(alerts + "\n" + (document.body?.innerText || "").slice(0, 12000))) {
      throw new Error("LinkedIn reported a restriction or security check. Queue stopped.");
    }
  }
  async function waitFor(find, timeout = 12000) {
    const deadline = Date.now() + timeout;
    do {
      guard();
      const found = find();
      if (found) return found;
      await sleep(200);
    } while (Date.now() < deadline);
    return null;
  }
  function profileHeading() {
    // LinkedIn's SDUI profile has an h2 inside its Topcard, whereas the
    // classic profile uses main h1. Scope h2 to that semantic card so About,
    // Activity, and recommended people's headings cannot become the target.
    const cards = document.querySelectorAll('main [componentkey^="com.linkedin.sdui.profile.card."][componentkey$="Topcard"], main [id^="com.linkedin.sdui.profile.card."][id$="Topcard"]');
    const headings = Array.from(cards).flatMap((card) => Array.from(card.querySelectorAll('h1, h2, [role="heading"]')));
    const modern = one(new Set(headings), "profile headings");
    return modern || one(document.querySelectorAll("main h1"), "profile headings");
  }
  function profileArea() {
    const heading = profileHeading();
    if (!heading) return null;
    // The name and action row can be separate sections of the same Topcard.
    // Choosing the nearest section of the name alone excludes Connect/More.
    const header = heading.closest(cardSelector) || heading.closest(".pv-top-card") || heading.closest("section");
    return header ? { heading, header } : null;
  }
  const pendingIn = (header) => Array.from(header.querySelectorAll(actionSelector))
    .find((element) => visible(element) && labels(element).some((label) => pendingPattern.test(label))) || null;
  const pending = () => {
    const area = profileArea();
    return area && pendingIn(area.header);
  };
  const connected = (header) => Array.from(header.querySelectorAll("span, p"))
    .some((element) => visible(element) && /^(?:·\s*)?1st$/.test(normalize(element.textContent)));
  const skipPending = () => ({ status: "pending", detail: "Skipped: an invitation is already pending. No action clicked." });
  try {
    guard();
    const heading = await waitFor(profileHeading);
    if (!heading) throw new Error("No unique profile heading found. This layout is unsupported.");
    // Pending is read-only: skip even before action handlers finish loading,
    // and never click it (that control can withdraw the existing invitation).
    if (pending()) return skipPending();
    if (document.querySelector('[data-sdui-screen="com.linkedin.sdui.flagshipnav.profile.Profile"]')) {
      // SDUI renders the name before React attaches the action handlers.
      // A click on the initial server HTML otherwise disappears silently.
      const ready = await waitFor(() => document.body?.dataset.rehydrated === "true", 20000);
      if (!ready) throw new Error("LinkedIn's profile actions did not finish loading. Reload the profile, then use Preview again.");
    }
    const header = profileArea()?.header;
    if (!header) throw new Error("Cannot isolate the profile's action area.");
    if (dialog()) throw new Error("An unexpected dialog is open. Review it manually.");
    const actions = await waitFor(() => {
      const area = profileArea();
      if (!area) return null;
      const isPending = pendingIn(area.header);
      if (isPending) return { ...area, isPending };
      const isConnected = connected(area.header);
      if (isConnected) return { ...area, isConnected };
      const connect = buttons(area.header, connectPattern);
      const more = buttons(area.header, /^(?:More|More actions)$/i);
      return (isPending || isConnected || connect || more) && { ...area, isPending, isConnected, connect, more };
    });
    if (actions?.isPending) return skipPending();
    if (actions?.isConnected) return { status: "connected", detail: "Already a first-degree connection." };
    let connect = actions?.connect || null;
    let openedMore = null;
    let inspectedMenu = null;
    if (!connect) {
      const more = actions?.more;
      if (more && !disabled(more)) {
        if (more.getAttribute("aria-expanded") !== "true") more.click();
        openedMore = more;
        const currentMenu = () => {
          const ariaMenu = scopes(all('[role="menu"]'), "More menus");
          return ariaMenu || scopes(all('.artdeco-dropdown__content--is-open'), "More menus");
        };
        const menu = await waitFor(currentMenu, 12000);
        if (!menu) throw new Error("The More menu could not be identified.");
        // Menu contents arrive after the popover itself. Its first render can
        // contain only Share/PDF actions while Connect is still being loaded.
        connect = await waitFor(() => {
          const activeMenu = currentMenu();
          inspectedMenu = activeMenu;
          return activeMenu && buttons(activeMenu, connectPattern);
        }, 12000);
      }
    }
    if (!connect || disabled(connect)) {
      const detail = "No enabled Connect action was matched. Profile actions: " + actionSummary(actions?.header || header) +
        (openedMore ? ". More menu actions: " + actionSummary(inspectedMenu) : "") + ".";
      if (openedMore && openedMore.getAttribute("aria-expanded") !== "false") openedMore.click();
      return { status: "unavailable", detail };
    }
    guard();
    if (pending()) return skipPending();
    if (!request.send) {
      if (openedMore && openedMore.getAttribute("aria-expanded") !== "false") openedMore.click();
      return { status: "would_send", detail: "Connect is available; no request sent." };
    }
    if (!connect.isConnected || !visible(connect) || disabled(connect) || !labels(connect).some((label) => connectPattern.test(label))) {
      throw new Error("The Connect action changed before clicking. No action clicked; check this profile manually.");
    }
    attempted = true;
    connect.click();
    const response = await waitFor(() => pending() ? { pending: true } : dialog());
    if (!response) throw new Error("Connect was clicked but its result is unclear. Review manually before retrying.");
    let noteAdded = false;
    if (!response.pending) {
      let modal = response;
      if (!/\binvitation\b|\binvite\b|\bconnect\b/i.test(modal.innerText)) throw new Error("Unrecognized invitation dialog. No Send click attempted.");
      if (one(modal.querySelectorAll('input[type="email"]'), "email fields") || /(?:enter|provide).{0,40}email/i.test(modal.innerText)) {
        throw new Error("This profile requires an email address. Queue stopped.");
      }
      if (request.note) {
        const addNote = buttons(modal, /^Add a note$/i);
        if (addNote) addNote.click();
        const field = await waitFor(() => {
          modal = dialog();
          return modal && one(modal.querySelectorAll("textarea"), "note fields");
        }, 3000);
        if (!field) throw new Error("The invitation note field is unavailable.");
        if (field.maxLength > 0 && request.note.length > field.maxLength) throw new Error("The note exceeds this invitation's character limit.");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
        setter.call(field, request.note);
        field.dispatchEvent(new Event("input", { bubbles: true }));
        field.dispatchEvent(new Event("change", { bubbles: true }));
        noteAdded = true;
      }
      guard();
      const send = await waitFor(() => {
        modal = dialog();
        if (!modal) return null;
        // Prefer this exact CTA for the prompt in the user's screenshot, even
        // when the same dialog also has another button labelled Send.
        const withoutNote = !request.note && buttons(modal, /^Send without a note$/i);
        if (!request.note && !withoutNote && (buttons(modal, /^Add a note$/i) || /Add a note to your invitation\?/i.test(modal.innerText))) return null;
        const candidate = withoutNote || buttons(modal, /^(?:Send|Send invitation|Send now)$/i);
        return candidate && !disabled(candidate) ? candidate : null;
      });
      if (!send) throw new Error("No enabled " + (request.note ? "Send" : "Send without a note") + " button in the invitation dialog. Visible actions: " + actionSummary(modal) + ".");
      send.click();
    }
    if (!(await waitFor(pending))) throw new Error("Send was attempted but Pending was not confirmed. Review manually; no automatic retry.");
    return { status: "sent", detail: "Confirmed by Pending in the requested profile's action area." +
      (request.note && !noteAdded ? " LinkedIn submitted directly, so the note could not be added." : "") };
  } catch (error) {
    return { status: attempted ? "review_required" : "halted", detail: error.message };
  }
}
