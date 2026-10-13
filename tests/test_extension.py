from pathlib import Path
import tempfile
import unittest

import linkconnect as runner
from test_linkconnect import fixture, TARGET, SECOND


def sdui_fixture(action="connect", more=False):
    """Minimal structure observed in the signed-in Chrome SDUI profile."""
    html = fixture(action=action, more=more)
    html = html.replace('<html lang="en"><body>', '<html lang="en"><head><meta charset="utf-8"></head><body data-rehydrated="true"><div data-sdui-screen="com.linkedin.sdui.flagshipnav.profile.Profile"></div>')
    html = html.replace("<main><section><h1>Target Person</h1>",
        '<main id="workspace"><section aria-label="Primary content">'
        '<div id="com.linkedin.sdui.profile.card.refTargetTopcard" componentkey="com.linkedin.sdui.profile.card.refTargetTopcard">'
        '<section><a href="' + TARGET + '"><h2>Target Person</h2></a>')
    html = html.replace("</section></main>",
        '</section></div><section><h2>About</h2><p>About this person</p></section>'
        '<section><h2>Activity</h2><button onclick="window.wrongClicks++">Connect</button></section></section></main>')
    html = html.replace('<span>1st</span>', '<p>· 1st</p>')
    if more:
        html = html.replace("document.querySelector('#menu').hidden=false", "document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden")
        html = html.replace('<button aria-label="Invite Target Person to connect" onclick="connect()">Connect</button>',
            '<div role="menuitem" aria-label="Invite Target Person to connect" onclick="connect()"><span>Connect</span></div>')
    return html


def screenshot_flow_fixture(more=False, prompt="role"):
    """Screenshot labels with several valid HTML representations of the UI.

    These fixtures do not claim to be the current live LinkedIn DOM.
    """
    html = sdui_fixture(more=more)
    html = html.replace('</h2></a>', '</h2></a></section><section class="profile-actions">')
    html = html.replace('aria-label="Invite Target Person to connect"', 'aria-label="Connect with Target Person"')
    html = html.replace('<h2>Invite Target Person to connect</h2>',
        '<h2>Add a note to your invitation?</h2><p>Personalize your invitation to Target Person by adding a note.</p>')
    html = html.replace('onclick="submitInvite()">Send without a note',
        'aria-label="Confirm this connection request" onclick="window.withoutNoteCount++;submitInvite()">Send without a note')
    html = html.replace('window.connectCount = 0;', 'window.withoutNoteCount=0; window.addNoteCount=0; window.connectCount = 0;')
    html = html.replace('function addNote() {', 'function addNote() { window.addNoteCount++;')
    html = html.replace("const dialog = document.createElement('div'); dialog.setAttribute('role', 'dialog');",
        "const dialog=document.createElement('dialog'); dialog.setAttribute('open','');" if prompt == "native" else
        "const dialog=document.createElement('div'); dialog.setAttribute('aria-modal','true');" if prompt == "aria" else
        "const dialog=document.createElement('div');" if prompt == "roleless" else
        "const dialog=document.createElement('div'); dialog.setAttribute('role','dialog');")
    html = html.replace('dialog.innerHTML = dialogHtml;', 'window.inviteDialog=dialog; dialog.innerHTML = dialogHtml;')
    html = html.replace("document.querySelector('[role=dialog]')", "window.inviteDialog")
    if prompt == "shadow":
        html = html.replace('<body data-rehydrated="true">', '<body data-rehydrated="true"><div id="interop-outlet" data-testid="interop-shadowdom"></div>')
        html = html.replace('document.body.appendChild(dialog);',
            "const host=document.querySelector('#interop-outlet'); const root=host.shadowRoot || host.attachShadow({mode:'open'}); root.appendChild(dialog);")
    return html


class ExistingBrowserExtensionTests(unittest.TestCase):
    """Real extension APIs, a fresh test profile, and intercepted local HTML only."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.temp = tempfile.TemporaryDirectory()
        cls.playwright = sync_playwright().start()
        extension = runner.ROOT / "extension"
        cls.context = cls.playwright.chromium.launch_persistent_context(
            cls.temp.name, channel="chromium", headless=True,
            args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                  "--host-resolver-rules=MAP * ~NOTFOUND", "--no-proxy-server"],
            viewport={"width": 1280, "height": 1000},
        )
        worker = cls.context.service_workers[0] if cls.context.service_workers else cls.context.wait_for_event("serviceworker")
        cls.app_url = f"chrome-extension://{worker.url.split('/')[2]}/web/index.html"

    @classmethod
    def tearDownClass(cls):
        cls.context.close()
        cls.playwright.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.html = fixture()
        self.profile_fixtures = {}
        self.requests = []

        def route(request):
            self.requests.append(request.request.url)
            request.fulfill(status=200, content_type="text/html", body=self.profile_fixtures.get(request.request.url, self.html))

        self.handler = route
        self.context.route("https://www.linkedin.com/**", self.handler)
        # Synthetic cookie proves tabs created by the extension retain this browser
        # session. No real credentials or normal browser profiles are accessed.
        self.context.add_cookies([{"name": "fixtureSession", "value": "already-signed-in", "domain": ".linkedin.com", "path": "/", "secure": True}])
        self.ui = self.context.new_page()
        self.ui.goto(self.app_url)
        self.ui.evaluate("async () => { await chrome.storage.local.clear(); await chrome.storage.session.clear(); }")
        self.ui.locator("#options").evaluate("element => { element.open = true; }")
        self.ui.locator("#delay").fill("0")
        self.errors = []
        self.ui.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.unroute("https://www.linkedin.com/**", self.handler)
        for page in self.context.pages:
            if page.url != "about:blank":
                page.close()

    def start(self, links=TARGET, send=True):
        self.ui.locator("#links").fill(links)
        self.ui.get_by_role("button", name="Send requests" if send else "Preview", exact=not send).click()

    def phase(self, label="Complete", timeout=18000):
        self.ui.wait_for_function("label => document.getElementById('phase').textContent === label", arg=label, timeout=timeout)

    def history(self):
        return self.ui.evaluate("async () => (await chrome.storage.local.get('connectionHistory')).connectionHistory || {}")

    def profile(self):
        return next(page for page in self.context.pages if page.url.startswith("https://www.linkedin.com/in/"))

    def simulate_tab_failure(self, scenario):
        # Real Chrome tabs/injection, with a stale API ID or interrupted response.
        # Chrome's prerender replacement is nondeterministic, so simulate that
        # event boundary rather than depending on remote browsing to trigger it.
        self.ui.evaluate("""scenario => {
          let changed = false;
          const listeners = [];
          const tabs = {
            getCurrent: (...args) => chrome.tabs.getCurrent(...args),
            create: (...args) => chrome.tabs.create(...args),
            query: (...args) => chrome.tabs.query(...args),
            onReplaced: {addListener: callback => listeners.push(callback)},
            async update(id, options) {
              const tab = await chrome.tabs.update(id, options);
              if (!changed && ['replacement', 'fallback'].includes(scenario)) {
                changed = true;
                const staleId = 2147483000;
                if (scenario === 'replacement') listeners.forEach(callback => callback(tab.id, staleId));
                return {...tab, id: staleId};
              }
              return tab;
            },
            async get(id) {
              if (!changed && scenario === 'closed') {
                changed = true;
                await chrome.tabs.remove(id);
              }
              return chrome.tabs.get(id);
            }
          };
          const scripting = {async executeScript(options) {
            const result = await chrome.scripting.executeScript(options);
            if (!changed && scenario === 'send-response-lost' && options.args[0].send) {
              changed = true;
              throw new Error('No tab with id: ' + options.target.tabId);
            }
            return result;
          }};
          queue = new ExistingBrowserQueue({tabs, scripting, runtime: chrome.runtime, storage: chrome.storage}, render);
        }""", scenario)

    def test_ui_has_no_login_and_uses_extension_page(self):
        self.assertEqual(self.ui.locator("#login, #save-session, #browser").count(), 0)
        self.assertTrue(self.ui.locator("#setup-hint").is_hidden())
        self.assertTrue(self.ui.get_by_role("button", name="Send requests").is_disabled())
        screenshot = runner.ROOT / "data" / "ui-preview.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        self.ui.screenshot(path=str(screenshot), full_page=True)
        self.ui.set_viewport_size({"width": 390, "height": 844})
        self.assertTrue(self.ui.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.assertEqual(self.errors, [])

    def test_invalid_links_do_not_open_linkedin(self):
        self.start("https://example.com/")
        self.ui.locator("#error").wait_for(state="visible")
        self.assertIn("Line 1", self.ui.locator("#error").inner_text())
        self.assertEqual(self.requests, [])

    def test_preview_deduplicates_and_never_clicks_connect(self):
        self.start(TARGET + "\n" + TARGET + "?tracking=1", send=False)
        self.phase()
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.history()[TARGET]["status"], "would_send")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_send_reuses_current_browser_session_and_confirms_pending(self):
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [1, 1, 0])
        self.assertIn("fixtureSession=already-signed-in", self.profile().evaluate("document.cookie"))
        self.assertEqual(self.errors, [])

    def test_notes_are_added(self):
        self.ui.locator("#note").fill("Hello, let's connect.")
        self.start()
        self.phase()
        self.assertEqual(self.profile().evaluate("sentNote"), "Hello, let's connect.")

    def test_more_menu_inspection_then_send(self):
        self.html = fixture(more=True).replace("document.querySelector('#menu').hidden=false", "document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden")
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.profile().evaluate("wrongClicks"), 0)

    def test_sdui_h2_preview_scopes_topcard_and_more_menu(self):
        self.html = sdui_fixture(more=True)
        self.start(send=False)
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "would_send")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])
        self.assertTrue(self.profile().locator("#menu").is_hidden())

    def test_sdui_h2_send_does_not_click_activity_or_recommendations(self):
        self.html = sdui_fixture(more=True)
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [1, 1, 0])

    def test_screenshot_selector_actions_can_be_siblings_of_the_name_section(self):
        self.html = sdui_fixture().replace('</h2></a>', '</h2></a></section><section class="profile-actions">')
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [1, 1, 0])

    def test_screenshot_selector_visible_connect_is_not_hidden_by_aria_label(self):
        self.html = sdui_fixture().replace('aria-label="Invite Target Person to connect"', 'aria-label="Connect with Target Person"')
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [1, 1, 0])

    def check_screenshot_send(self):
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.profile().evaluate("[connectCount, withoutNoteCount, addNoteCount, sendCount, wrongClicks]"), [1, 1, 0, 1, 0])

    def test_screenshot_direct_connect_then_send_without_a_note(self):
        self.html = screenshot_flow_fixture()
        self.check_screenshot_send()

    def test_screenshot_more_connect_then_send_without_a_note(self):
        self.html = screenshot_flow_fixture(more=True)
        self.check_screenshot_send()

    def test_screenshot_native_invitation_dialog_without_explicit_role(self):
        self.html = screenshot_flow_fixture(prompt="native")
        self.check_screenshot_send()

    def test_screenshot_invitation_prompt_with_aria_modal(self):
        self.html = screenshot_flow_fixture(prompt="aria")
        self.check_screenshot_send()

    def test_screenshot_invitation_prompt_identified_by_exact_heading(self):
        self.html = screenshot_flow_fixture(more=True, prompt="roleless")
        self.check_screenshot_send()

    def test_screenshot_invitation_dialog_in_linkedin_interop_shadow_root(self):
        self.html = screenshot_flow_fixture(more=True, prompt="shadow")
        self.check_screenshot_send()

    def test_screenshot_send_without_note_wins_over_another_send_button(self):
        self.html = screenshot_flow_fixture().replace('<h2>Add a note to your invitation?</h2>',
            '<h2>Add a note to your invitation?</h2><button onclick="window.wrongClicks++">Send</button>')
        self.check_screenshot_send()

    def test_screenshot_pending_confirmation_survives_a_card_remount(self):
        self.html = screenshot_flow_fixture().replace("document.querySelector('#action').innerHTML = '<button>Pending</button>';",
            "const card=document.querySelector('[id$=Topcard]'); const next=card.cloneNode(true); next.querySelector('#action').innerHTML='<button>Pending</button>'; card.replaceWith(next);")
        self.check_screenshot_send()

    def test_screenshot_nested_menuitem_and_button_are_one_action(self):
        self.html = screenshot_flow_fixture(more=True).replace('<div role="menuitem" aria-label="Connect with Target Person" onclick="connect()"><span>Connect</span></div>',
            '<div role="menuitem" aria-label="Invite Target Person to connect" onclick="connect()"><button aria-label="Invite Target Person to connect">Connect</button></div>')
        self.check_screenshot_send()

    def test_screenshot_visible_connect_label_ignores_the_icon_glyph(self):
        self.html = screenshot_flow_fixture().replace('onclick="connect()">Connect</button>',
            'onclick="connect()"><span aria-hidden="true">person_add</span><span>Connect</span></button>')
        self.check_screenshot_send()

    def test_screenshot_waits_for_send_without_note_instead_of_clicking_other_send(self):
        self.html = screenshot_flow_fixture().replace('aria-label="Confirm this connection request"', 'id="without-note" hidden aria-label="Confirm this connection request"')
        self.html = self.html.replace('<h2>Add a note to your invitation?</h2>',
            '<h2>Add a note to your invitation?</h2><button onclick="window.wrongClicks++">Send</button>')
        self.html = self.html.replace('document.body.appendChild(dialog);',
            "document.body.appendChild(dialog);setTimeout(()=>dialog.querySelector('#without-note').hidden=false,800);")
        self.check_screenshot_send()

    def test_screenshot_preexisting_note_prompt_is_not_submitted(self):
        self.html = screenshot_flow_fixture(prompt="roleless").replace('<script>',
            '<div><h2>Add a note to your invitation?</h2><button>Add a note</button><button onclick="window.wrongClicks++">Send without a note</button></div><script>')
        self.start()
        self.phase("Needs attention")
        self.assertEqual(self.history()[TARGET]["status"], "halted")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_screenshot_disabled_menuitem_wrapper_is_not_clicked(self):
        self.html = screenshot_flow_fixture(more=True).replace('<div role="menuitem" aria-label="Connect with Target Person" onclick="connect()"><span>Connect</span></div>',
            '<div role="menuitem" aria-disabled="true" aria-label="Invite Target Person to connect" onclick="connect()"><button>Connect</button></div>')
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "unavailable")
        self.assertIn("disabled", self.history()[TARGET]["detail"])
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_sdui_waits_for_interactive_actions_in_a_visible_tab(self):
        self.html = sdui_fixture(more=True).replace('data-rehydrated="true"', 'data-rehydrated="false"')
        self.html = self.html.replace("document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden",
            "if(document.visibilityState==='visible' && document.body.dataset.rehydrated==='true') document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden")
        self.html = self.html.replace('</script>', "setTimeout(()=>document.body.dataset.rehydrated='true',800);</script>")
        self.start(send=False)
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "would_send")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_sdui_waits_for_connect_after_the_more_menu_appears(self):
        self.html = sdui_fixture(more=True)
        self.html = self.html.replace('<div role="menuitem" aria-label="Invite Target Person to connect"',
            '<div id="late-connect" hidden role="menuitem" aria-label="Invite Target Person to connect"')
        self.html = self.html.replace("document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden",
            "document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden;setTimeout(()=>document.querySelector('#late-connect').hidden=false,800)")
        self.start(send=False)
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "would_send")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_sdui_menu_can_replace_its_first_render_and_use_wrapped_label(self):
        self.html = sdui_fixture(more=True)
        self.html = self.html.replace('<div role="menuitem" aria-label="Invite Target Person to connect" onclick="connect()"><span>Connect</span></div>',
            '<div id="late-connect" hidden role="menuitem" aria-label="Invite Target Person\nto connect" onclick="connect()"><span>Connect</span></div>')
        self.html = self.html.replace("document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden",
            "document.querySelector('#menu').hidden=!document.querySelector('#menu').hidden;setTimeout(()=>{const previous=document.querySelector('#menu');const replacement=previous.cloneNode(true);replacement.querySelector('#late-connect').hidden=false;previous.replaceWith(replacement);},800)")
        self.start(send=False)
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "would_send")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_sdui_connected_degree_in_paragraph_is_skipped(self):
        self.html = sdui_fixture(action="connected")
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "connected")
        self.assertEqual(self.profile().evaluate("sendCount"), 0)

    def test_sdui_multiple_topcard_headings_stop_without_sending(self):
        self.html = sdui_fixture().replace("<h2>Target Person</h2>", "<h2>Target Person</h2><h2>Other Person</h2>")
        self.start()
        self.phase("Needs attention")
        self.assertEqual(self.history()[TARGET]["status"], "halted")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_tab_replacement_updates_id_before_send(self):
        self.simulate_tab_failure("replacement")
        self.start(TARGET + "\n" + SECOND)
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.history()[SECOND]["status"], "sent")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [1, 1, 0])

    def test_stale_tab_id_recovers_exact_profile_in_same_window(self):
        self.simulate_tab_failure("fallback")
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertEqual(self.profile().evaluate("sendCount"), 1)

    def test_closed_tab_stops_before_connect_and_marks_current_row(self):
        self.simulate_tab_failure("closed")
        self.start(TARGET + "\n" + SECOND)
        self.phase("Needs attention")
        self.assertEqual(self.history()[TARGET]["status"], "halted")
        self.assertNotIn(SECOND, self.history())
        self.assertIn("Keep it open", self.ui.locator("#status").inner_text())
        self.assertEqual(self.ui.locator("#results .result-status").all_text_contents(), ["Stopped", "Queued"])
        self.assertEqual(self.errors, [])

    def test_lost_send_response_is_reserved_and_never_retried(self):
        self.simulate_tab_failure("send-response-lost")
        self.start(TARGET + "\n" + SECOND)
        self.phase("Needs attention")
        self.assertEqual(self.history()[TARGET]["status"], "review_required")
        self.assertNotIn(SECOND, self.history())
        self.assertEqual(self.profile().evaluate("sendCount"), 1)
        self.assertIn("will not be retried automatically", self.ui.locator("#status").inner_text())
        self.start(TARGET)
        self.phase()
        self.assertEqual(self.profile().evaluate("sendCount"), 1)

    def test_no_recovery_from_a_different_profile_tab(self):
        other = self.context.new_page()
        other.goto(SECOND)
        self.simulate_tab_failure("closed")
        self.start()
        self.phase("Needs attention")
        self.assertEqual(other.evaluate("[connectCount, sendCount]"), [0, 0])
        self.assertEqual(self.history()[TARGET]["status"], "halted")

    def test_ambiguous_replacement_does_not_send_in_duplicate_tabs(self):
        other = self.context.new_page()
        other.goto(TARGET)
        self.simulate_tab_failure("fallback")
        self.start()
        self.phase("Needs attention")
        self.assertIn("Several tabs show this profile", self.ui.locator("#status").inner_text())
        self.assertEqual(self.history()[TARGET]["status"], "halted")
        for page in self.context.pages:
            if page.url == TARGET:
                self.assertEqual(page.evaluate("sendCount"), 0)

    def test_existing_pending_does_not_send(self):
        self.html = fixture(action="pending")
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "pending")
        self.assertEqual(self.profile().evaluate("sendCount"), 0)

    def test_pending_screenshot_is_skipped_and_next_profile_is_sent(self):
        pending_html = sdui_fixture(action="pending").replace(
            '<button aria-label="Pending, click to withdraw invitation sent to Target Person">Pending</button>',
            '<button>Message</button><button aria-label="Withdraw this invitation" onclick="sessionStorage.setItem(\'wrongPendingClick\',\'yes\')"><svg aria-hidden="true"><title>clock</title></svg><span>Pending</span></button><button onclick="sessionStorage.setItem(\'wrongPendingClick\',\'yes\')">More</button>')
        self.profile_fixtures[TARGET] = pending_html
        self.profile_fixtures[SECOND] = screenshot_flow_fixture()
        self.start(TARGET + "\n" + SECOND)
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "pending")
        self.assertEqual(self.history()[SECOND]["status"], "sent")
        self.assertIsNone(self.profile().evaluate("sessionStorage.getItem('wrongPendingClick')"))
        self.assertEqual(self.ui.locator("#results .result-status").all_text_contents(), ["Skipped (pending)", "Sent"])
        self.assertEqual(self.profile().evaluate("sendCount"), 1)
        requests_before = list(self.requests)
        self.start(TARGET)
        self.phase()
        self.assertEqual(self.requests, requests_before)

    def test_pending_is_skipped_before_hydration_and_ambiguous_connect_actions(self):
        self.html = sdui_fixture(action="pending").replace('data-rehydrated="true"', 'data-rehydrated="false"')
        self.html = self.html.replace('<div id="action">', '<div id="action"><button onclick="window.wrongClicks++">Connect</button><button onclick="window.wrongClicks++">Connect</button>')
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "pending")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_duplicate_pending_indicators_still_skip_without_clicking(self):
        self.html = sdui_fixture(action="pending").replace('<div id="action">', '<div id="action"><button onclick="window.wrongClicks++">Pending</button>')
        self.start()
        self.phase()
        self.assertEqual(self.history()[TARGET]["status"], "pending")
        self.assertEqual(self.profile().evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_hidden_pending_does_not_skip_a_profile_that_can_connect(self):
        self.html = screenshot_flow_fixture().replace('<div id="action">', '<div id="action"><button hidden>Pending</button>')
        self.check_screenshot_send()

    def test_pending_in_recommendations_does_not_skip_the_requested_profile(self):
        self.html = screenshot_flow_fixture().replace('<h2>Recommended Person</h2>', '<h2>Recommended Person</h2><button>Pending</button>')
        self.check_screenshot_send()

    def test_limit_stops_queue_before_second_profile(self):
        self.html = fixture(behavior="limit")
        self.start(TARGET + "\n" + SECOND)
        self.phase("Needs attention")
        self.assertEqual(self.history()[TARGET]["status"], "review_required")
        self.assertNotIn(SECOND, self.history())
        self.assertEqual(self.profile().evaluate("sendCount"), 0)

    def test_email_prompt_stops_without_submitting(self):
        self.html = fixture(behavior="email")
        self.start()
        self.phase("Needs attention")
        self.assertEqual(self.profile().evaluate("sendCount"), 0)

    def test_uncertain_send_is_not_reported_successful(self):
        self.html = fixture(behavior="unconfirmed")
        self.start()
        self.phase("Needs attention")
        self.assertEqual(self.history()[TARGET]["status"], "review_required")
        self.assertEqual(self.profile().evaluate("sendCount"), 1)

    def test_sent_and_uncertain_history_skip_navigation(self):
        self.ui.evaluate("async ([first, second]) => chrome.storage.local.set({connectionHistory: {[first]: {status:'sent'}, [second]: {status:'sending'}}})", [TARGET, SECOND])
        self.start(TARGET + "\n" + SECOND)
        self.phase()
        self.assertEqual(self.requests, [])

    def test_stop_button_finishes_current_and_skips_next(self):
        self.ui.locator("#delay").fill("10")
        self.start(TARGET + "\n" + SECOND)
        self.ui.get_by_text("Sent", exact=True).wait_for()
        self.ui.get_by_role("button", name="Stop after current").click()
        self.phase("Stopped")
        self.assertEqual(self.history()[TARGET]["status"], "sent")
        self.assertNotIn(SECOND, self.history())

    def test_second_queue_page_cannot_claim_a_running_queue(self):
        self.ui.locator("#delay").fill("10")
        self.start(TARGET + "\n" + SECOND)
        self.ui.get_by_text("Sent", exact=True).wait_for()
        second_ui = self.context.new_page()
        second_ui.goto(self.app_url)
        second_ui.locator("#links").fill(SECOND)
        second_ui.get_by_role("button", name="Preview", exact=True).click()
        second_ui.locator("#error").wait_for(state="visible")
        self.assertIn("already running", second_ui.locator("#error").inner_text())
        self.ui.get_by_role("button", name="Stop after current").click()
        self.phase("Stopped")


if __name__ == "__main__":
    unittest.main()
