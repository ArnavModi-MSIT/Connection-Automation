from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import linkconnect as app


TARGET = "https://www.linkedin.com/in/target-person/"
SECOND = "https://www.linkedin.com/in/second-person/"


class LocalStateTests(unittest.TestCase):
    def test_normalizes_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "links.txt"
            path.write_text(
                "\ufeff# profiles\nlinkedin.com/in/target-person\n"
                "https://in.linkedin.com/in/target-person/?tracking=1#about\n\n" + SECOND,
                encoding="utf-8",
            )
            self.assertEqual(app.load_links(path), [TARGET, SECOND])

    def test_rejects_nonprofile_and_deceptive_links(self):
        invalid = [
            "http://www.linkedin.com/in/person/",
            "https://linkedin.com.evil.example/in/person/",
            "https://www.linkedin.com@evil.example/in/person/",
            "https://evil.example@linkedin.com/in/person/",
            "https://www.linkedin.com:443/in/person/",
            "https://www.linkedin.com/company/example/",
            "https://www.linkedin.com/in/person/details/",
            "https://www.linkedin.com/in/person%2Fother/",
            "https://www.linkedin.com/in/person%5Cother/",
            "https://www.linkedin.com/in/person%0Aother/",
            "https://www.linkedin.com/in/person%ZZ/",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.canonical_url(value)

    def test_invalid_line_blocks_entire_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "links.txt"
            path.write_text(TARGET + "\nhttps://example.com/\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2"):
                app.load_links(path)

    def test_empty_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "links.txt"
            path.write_text("# only a comment\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no profiles"):
                app.load_links(path)

    def test_journal_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.jsonl"
            journal = app.Journal(path)
            journal.write(TARGET, "sending")
            journal.write(TARGET, "sent")
            journal.write(SECOND, "review_required")
            restored = app.Journal(path)
            self.assertEqual(restored.latest[TARGET]["status"], "sent")
            self.assertEqual(restored.latest[SECOND]["status"], "review_required")

    def test_corrupt_journal_stops_instead_of_losing_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.jsonl"
            app.Journal(path).write(TARGET, "sent")
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"url":')
            with self.assertRaisesRegex(ValueError, "line 2"):
                app.Journal(path)

    def test_lock_blocks_a_second_process_and_is_released(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with app.session_lock(directory):
                with self.assertRaises(app.StopRun):
                    with app.session_lock(directory):
                        self.fail("Second lock must not be acquired")
                self.assertTrue((directory / "session.lock").exists())
            self.assertFalse((directory / "session.lock").exists())

    def test_queue_stops_after_uncertain_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            journal = app.Journal(directory / "results.jsonl")
            args = SimpleNamespace(send=True, limit=None, delay=0, data_dir=directory)

            def interrupted(*unused, **kwargs):
                kwargs["before_click"]()
                raise app.StopRun("Unclear result")

            with patch.object(app, "process_profile", side_effect=interrupted) as process:
                with patch.object(app, "diagnostic", return_value="fixture.png"), redirect_stdout(StringIO()):
                    code = app.run_queue(object(), [TARGET, SECOND], journal, args, None)
            self.assertEqual(code, 2)
            self.assertEqual(process.call_count, 1)
            self.assertEqual(journal.latest[TARGET]["status"], "review_required")
            self.assertNotIn(SECOND, journal.latest)

    def test_ctrl_c_preserves_attempt_record(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / "results.jsonl"
            journal = app.Journal(path)
            args = SimpleNamespace(send=True, limit=None, delay=0, data_dir=directory)

            def interrupted(*unused, **kwargs):
                kwargs["before_click"]()
                raise KeyboardInterrupt

            with patch.object(app, "process_profile", side_effect=interrupted), redirect_stdout(StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    app.run_queue(object(), [TARGET], journal, args, None)
            self.assertEqual(app.Journal(path).latest[TARGET]["status"], "sending")

    def test_resume_skips_sent_and_uncertain_without_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            journal = app.Journal(directory / "results.jsonl")
            journal.write(TARGET, "sent")
            journal.write(SECOND, "review_required")
            args = SimpleNamespace(send=True, limit=None, delay=0, data_dir=directory)
            with patch.object(app, "process_profile") as process, redirect_stdout(StringIO()):
                self.assertEqual(app.run_queue(object(), [TARGET, SECOND], journal, args, None), 0)
            process.assert_not_called()


def fixture(action="connect", behavior="dialog", more=False, extra=""):
    action_markup = {
        "connect": '<button aria-label="Invite Target Person to connect" onclick="connect()">Connect</button>',
        "pending": '<button aria-label="Pending, click to withdraw invitation sent to Target Person">Pending</button>',
        "connected": '<span>1st</span><button>Message</button>',
        "unavailable": '<button>Follow</button>',
        "disabled": '<button disabled>Connect</button>',
    }[action]
    menu = ""
    if more:
        action_markup = '<button onclick="document.querySelector(\'#menu\').hidden=false">More</button>'
        menu = '<div id="menu" role="menu" hidden><button aria-label="Invite Target Person to connect" onclick="connect()">Connect</button></div>'
    dialog_contents = {
        "dialog": '<h2>Invite Target Person to connect</h2><button onclick="addNote()">Add a note</button><button onclick="submitInvite()">Send without a note</button>',
        "email": '<h2>Invite Target Person to connect</h2><p>Enter their email address</p><input type="email"><button onclick="submitInvite()">Send</button>',
        "limit": '<h2>You have reached the weekly invitation limit</h2>',
        "unconfirmed": '<h2>Invite Target Person to connect</h2><button onclick="window.sendCount++">Send without a note</button>',
        "wrong_dialog": '<h2>Compose message</h2><button onclick="submitInvite()">Send</button>',
    }.get(behavior, "")
    return f"""<!doctype html><html lang="en"><body>
    <main><section><h1>Target Person</h1><div id="action">{action_markup}</div>{menu}</section></main>
    <aside><h2>Recommended Person</h2><button aria-label="Invite Recommended Person to connect" onclick="window.wrongClicks++">Connect</button></aside>
    {extra}
    <script>
      window.connectCount = 0; window.sendCount = 0; window.wrongClicks = 0; window.sentNote = null;
      const dialogHtml = {repr(dialog_contents)};
      function connect() {{
        window.connectCount++;
        const menu = document.querySelector('#menu'); if (menu) menu.hidden = true;
        if ('{behavior}' === 'direct') {{ submitInvite(); return; }}
        const dialog = document.createElement('div'); dialog.setAttribute('role', 'dialog');
        dialog.innerHTML = dialogHtml; document.body.appendChild(dialog);
      }}
      function addNote() {{
        document.querySelector('[role=dialog]').innerHTML = '<h2>Add a note to your invitation</h2><textarea maxlength="200"></textarea><button onclick="submitInvite()">Send</button>';
      }}
      function submitInvite() {{
        window.sendCount++;
        const note = document.querySelector('textarea'); if (note) window.sentNote = note.value;
        document.querySelector('#action').innerHTML = '<button>Pending</button>';
        const dialog = document.querySelector('[role=dialog]'); if (dialog) dialog.remove();
      }}
    </script></body></html>"""


class LocalBrowserTests(unittest.TestCase):
    """All HTTPS requests are fulfilled locally; these tests never reach LinkedIn."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.html = fixture()
        self.http_status = 200
        self.context = self.browser.new_context()
        self.context.route("**/*", lambda route: route.fulfill(
            status=self.http_status, content_type="text/html", body=self.html
        ))
        self.page = self.context.new_page()
        self.page.set_default_timeout(1000)
        self.attempts = 0

    def tearDown(self):
        self.context.close()

    def before_click(self):
        self.attempts += 1

    def process(self, send=True, note=None):
        return app.process_profile(self.page, TARGET, send, note, self.before_click, timeout=0.75)

    def test_preview_never_clicks_connect_or_send(self):
        status, _ = self.process(send=False)
        self.assertEqual(status, "would_send")
        self.assertEqual(self.attempts, 0)
        self.assertEqual(self.page.evaluate("[connectCount, sendCount, wrongClicks]"), [0, 0, 0])

    def test_send_confirms_target_pending_and_ignores_recommendations(self):
        status, _ = self.process()
        self.assertEqual(status, "sent")
        self.assertEqual(self.attempts, 1)
        self.assertEqual(self.page.evaluate("[connectCount, sendCount, wrongClicks]"), [1, 1, 0])

    def test_more_menu_support(self):
        self.html = fixture(more=True)
        self.assertEqual(self.process()[0], "sent")
        self.assertEqual(self.page.evaluate("wrongClicks"), 0)

    def test_more_menu_preview_does_not_send(self):
        self.html = fixture(more=True)
        self.assertEqual(self.process(send=False)[0], "would_send")
        self.assertEqual(self.page.evaluate("[connectCount, sendCount]"), [0, 0])

    def test_optional_note_is_filled_before_send(self):
        self.assertEqual(self.process(note="Hello, let's connect.")[0], "sent")
        self.assertEqual(self.page.evaluate("sentNote"), "Hello, let's connect.")

    def test_immediate_submission_is_not_sent_twice(self):
        self.html = fixture(behavior="direct")
        self.assertEqual(self.process()[0], "sent")
        self.assertEqual(self.page.evaluate("sendCount"), 1)

    def test_pending_is_skipped(self):
        self.html = fixture(action="pending")
        self.assertEqual(self.process()[0], "pending")
        self.assertEqual(self.attempts, 0)

    def test_connected_is_skipped(self):
        self.html = fixture(action="connected")
        self.assertEqual(self.process()[0], "connected")
        self.assertEqual(self.attempts, 0)

    def test_unavailable_does_not_click_sidebar_connect(self):
        self.html = fixture(action="unavailable")
        self.assertEqual(self.process()[0], "unavailable")
        self.assertEqual(self.page.evaluate("wrongClicks"), 0)

    def test_disabled_connect_is_not_clicked(self):
        self.html = fixture(action="disabled")
        self.assertEqual(self.process()[0], "unavailable")
        self.assertEqual(self.attempts, 0)

    def test_email_prompt_stops_without_submitting(self):
        self.html = fixture(behavior="email")
        with self.assertRaisesRegex(app.StopRun, "email"):
            self.process()
        self.assertEqual(self.page.evaluate("sendCount"), 0)

    def test_limit_stops_without_submitting(self):
        self.html = fixture(behavior="limit")
        with self.assertRaisesRegex(app.StopRun, "limit"):
            self.process()
        self.assertEqual(self.page.evaluate("sendCount"), 0)

    def test_unrecognized_dialog_is_not_submitted(self):
        self.html = fixture(behavior="wrong_dialog")
        with self.assertRaisesRegex(app.StopRun, "recognized"):
            self.process()
        self.assertEqual(self.page.evaluate("sendCount"), 0)

    def test_unconfirmed_send_is_not_reported_successful(self):
        self.html = fixture(behavior="unconfirmed")
        with self.assertRaisesRegex(app.StopRun, "not confirmed"):
            self.process()
        self.assertEqual(self.page.evaluate("sendCount"), 1)

    def test_ambiguous_target_controls_stop_before_attempt(self):
        self.html = fixture().replace('</div></section>', '<button>Connect</button></div></section>')
        with self.assertRaisesRegex(app.StopRun, "Multiple visible"):
            self.process()
        self.assertEqual(self.attempts, 0)

    def test_preexisting_dialog_stops_before_attempt(self):
        self.html = fixture(extra='<div role="dialog"><h2>Something unexpected</h2><button>Send</button></div>')
        with self.assertRaisesRegex(app.StopRun, "unexpected dialog"):
            self.process()
        self.assertEqual(self.attempts, 0)

    def test_security_notice_stops_before_attempt(self):
        self.html = fixture(extra='<div role="alert">Your account is temporarily restricted</div>')
        with self.assertRaisesRegex(app.StopRun, "restriction"):
            self.process()
        self.assertEqual(self.attempts, 0)

    def test_http_rate_limit_stops_before_attempt(self):
        self.http_status = 429
        with self.assertRaisesRegex(app.StopRun, "429"):
            self.process()
        self.assertEqual(self.attempts, 0)

    def test_challenge_route_is_not_bypassed(self):
        self.page.goto("https://www.linkedin.com/checkpoint/challenge/")
        with self.assertRaisesRegex(app.StopRun, "verification"):
            app.guard(self.page)

    def test_wrong_profile_url_is_rejected(self):
        self.page.goto(SECOND)
        with self.assertRaisesRegex(app.StopRun, "URL changed"):
            app.guard(self.page, TARGET)


if __name__ == "__main__":
    unittest.main()
