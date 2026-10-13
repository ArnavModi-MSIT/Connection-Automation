# Local connection queue for Chrome and Firefox

Paste profile links into a simple HTML page and process them in your **existing signed-in browser**. The main UI has no login button, saved-session step, or browser selector. It runs as a local browser extension; Python and a server are not required to use it.

## One-time browser setup

Open `INSTALL.html` (or double-click `start-ui.cmd`) for the setup guide.

**Chrome:** Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked**, and select this project's **`extension`** folder.

**Firefox:** Open `about:debugging#/runtime/this-firefox`, click **Load Temporary Add-on**, and select **`extension/manifest.json`**. Firefox removes temporary add-ons when it restarts, so load it again after a restart. Grant LinkedIn host access if Firefox requests it. A signed permanent Firefox add-on has not been produced.

Then click **Local Connection Queue** in the browser's Extensions menu. It opens the HTML page in that browser profile. Use the profile where you are already signed in to LinkedIn. Chrome 121+ and Firefox 121+ are required for the shared Manifest V3 configuration.

## Use

1. Paste LinkedIn `/in/` profile URLs, one per line.
2. Click **Preview** to check without sending, or **Send requests** to send one by one.
3. Watch the results. **Stop after current** stops before the next profile.

Connect can be either a button on the main profile card or an item inside **More**. The extension checks both locations. With the note field empty, it clicks **Send without a note** in the **Add a note to your invitation?** prompt and confirms **Pending** before reporting success. **Preview** only finds Connect; it does not open or submit that invitation prompt.

If the requested profile already shows **Pending**, the result is **Skipped (pending)** and the queue moves to the next link. It does not click Pending, More, Connect, or Send for that profile. Saved pending results are also skipped on later runs.

An optional note, delay, and profile limit are under **Options**. Empty limit means all remaining links. The extension opens one dedicated LinkedIn tab, brings it forward while checking the profile, and returns to the queue page after each result. It reuses that LinkedIn tab and leaves it open afterward. Keep both tabs open while it runs. Closing or reloading the queue page interrupts the queue; an in-flight invitation may still complete and its durable `sending` record stays skipped for manual review.

If LinkedIn needs fresh authentication or verification, the queue stops. Resolve that normally in your browser before retrying. It never guesses an email address, bypasses verification, or reports success just because it clicked Send. `sent` requires Pending to appear in the requested profile's action area.

## Progress and privacy

Results are stored locally in this extension's browser storage. Duplicate URLs, existing connections, pending invitations, completed sends, and uncertain attempts are skipped. Chrome and Firefox keep separate histories. Keep each installed extension profile tied to one LinkedIn account; if you change accounts, do not reuse the old history without reviewing it. Firefox container or private-window sessions have not been verified; this implementation uses the browser's normal tab context.

The extension does not read or export cookies or passwords. It uses `scripting` and `storage` permissions and host access limited to LinkedIn. It adds no telemetry, CDN, cloud API, or paid service. There is no permission to read all websites. A background lock prevents two queue pages from starting concurrent runs.

An interrupted attempt is deliberately left as `sending` or `review_required`. Do not clear history to blindly retry it; first check that profile manually. Removing/reinstalling a Chrome extension can clear its storage. Firefox temporary-add-on storage persistence across restarts is not guaranteed. Check pending requests manually after a reinstall.

**LinkedIn prohibits automated invitations and can restrict or close your account.** Running locally or adding delays does not remove that risk. [Official guidance](https://www.linkedin.com/help/linkedin/answer/a1341387). There is no guaranteed safe quota or delay.

## Source and validation

`web/` contains the HTML/CSS/JS source. `extension/` is the installable folder. Rebuild after editing UI source:

```powershell
python build_extension.py
```

The builder copies an explicit asset allowlist and creates `connection-queue.zip` containing only extension files. It excludes private data, browser profiles, logs, and Python code. After rebuilding, reload the extension in its browser's extension manager.

**Updating an existing installation:** Stop the queue, reload **Local Connection Queue** at `chrome://extensions` (or Firefox's debugging page), close the old queue page, then reopen the extension toolbar icon. The footer should show **v0.2.6**. Use **Preview** first. Reloading the existing installation keeps Chrome's saved request history; do not remove and reinstall it to update.

If you see **No tab with id**, the browser no longer recognizes the tab ID used by the old version. Version 0.2.1 tracks tab replacements and can recover a single exact-profile tab in the same window before sending. Closing the LinkedIn tab stops the queue. A failed or interrupted send remains reserved for manual review and is never automatically retried.

```powershell
python -m unittest discover -s tests -v
```

Tests use isolated browser profiles and local fixtures; no real LinkedIn invitations are sent. Real signed-in sending has not been verified. UI selectors require English LinkedIn and a recognizable profile heading/action section. Unsupported layouts and visible account restrictions stop the queue.

Version 0.2.5 checks the whole semantic profile card, including layouts where the name and action buttons are sibling sections. It recognizes the visible button label as well as accessibility labels, waits for actions and menu contents, and refreshes the profile scope when checking Pending after a render. Both classic `h1` and SDUI `h2` profile headings are supported.

Invitation prompts are recognized through their dialog semantics or the exact **Add a note to your invitation?** heading and buttons. Native dialogs and LinkedIn's known open interoperability shadow root are supported. **Send without a note** takes priority over generic Send controls when no note is requested. A preexisting dialog, an ambiguous action, or an uncertain send stops the queue.

The current fixes are verified using local fixtures with the supplied screenshot labels and multiple possible DOM structures. They have not been verified against the user's current live LinkedIn page. Unavailable results now include the detected action labels to help diagnose any remaining mismatch. No normal browser session or live invitation is used by the test suite.

`web_ui.py` now provides a **read-only HTML preview** (`python web_ui.py`). It has no login or sending API. Opening `web/index.html` directly also shows the page and setup instructions; use the installed extension to control LinkedIn tabs.

The original `linkconnect.py` remains as an optional separate Playwright CLI. Its isolated-profile login is unrelated to the new extension UI. It can still be used with `python linkconnect.py --help`; its dependency is in `requirements.txt`.

Browser references: [Chrome remote debugging limitations](https://developer.chrome.com/blog/remote-debugging-port), [cross-browser extension backgrounds](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/background), [Firefox temporary installation](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/).
