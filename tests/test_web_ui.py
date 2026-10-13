from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import linkconnect as runner
import web_ui


class PreviewServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = web_ui.LocalServer(("127.0.0.1", 0), web_ui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, post=False):
        try:
            response = urlopen(Request(self.url + path, data=b"" if post else None), timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read().decode("utf-8")

    def test_no_login_controls_in_html(self):
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertNotIn('id="login"', html)
        self.assertNotIn('id="save-session"', html)
        self.assertNotIn('id="browser"', html)

    def test_preview_cannot_launch_login_or_send(self):
        for path in ["/api/login", "/api/run", "/api/finish-login"]:
            self.assertEqual(self.request(path, post=True)[0], 405)

    def test_browser_scripts_served_and_data_private(self):
        for name in ["profile.js", "runner.js", "app.js", "style.css"]:
            self.assertEqual(self.request("/" + name)[0], 200)
        self.assertEqual(self.request("/data/results.jsonl")[0], 404)
        self.assertEqual(self.request("/../linkconnect.py")[0], 404)

    def test_extension_archive_contains_only_public_assets(self):
        with ZipFile(runner.ROOT / "connection-queue.zip") as archive:
            self.assertEqual(set(archive.namelist()), {
                "manifest.json", "background.js", "web/index.html", "web/style.css", "web/app.js", "web/profile.js", "web/runner.js",
            })
        for name in ["index.html", "style.css", "app.js", "profile.js", "runner.js"]:
            self.assertEqual((runner.ROOT / "web" / name).read_bytes(), (runner.ROOT / "extension" / "web" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
