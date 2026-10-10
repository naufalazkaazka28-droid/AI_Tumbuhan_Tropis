import json
import threading
import unittest
import urllib.error
import urllib.request

import server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, timeout=2)
        cls.addClassCleanup(cls.httpd.server_close)
        cls.addClassCleanup(cls.httpd.shutdown)
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    def test_web_app_and_local_knowledge_are_served(self):
        with urllib.request.urlopen(self.base_url + "/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("Tumbuhan Tropis Saja", response.read().decode("utf-8"))

        with urllib.request.urlopen(self.base_url + "/app.js") as response:
            self.assertEqual(response.status, 200)
            script = response.read().decode("utf-8")
            self.assertIn("function classify", script)
            self.assertNotIn('fetch("/api/', script)

        with urllib.request.urlopen(self.base_url + "/tropical_plants_kb.json") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
            self.assertGreater(len(json.loads(response.read())["plants"]), 50)

    def test_local_assets_are_served(self):
        assets = (
            ("/background.jpg", "image/jpeg", b"\xff\xd8\xff"),
            ("/chat-decoration.svg", "image/svg+xml", b"<svg"),
            ("/logo.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        )
        for path, content_type, signature in assets:
            with self.subTest(path=path), urllib.request.urlopen(self.base_url + path) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Type"], content_type)
                self.assertTrue(response.read().startswith(signature))

    def test_chat_and_whatsapp_api_routes_are_not_exposed(self):
        for method, path in (
            ("POST", "/api/chat"),
            ("GET", "/api/poll"),
            ("GET", "/api/handoff"),
            ("POST", "/webhook/whatsapp"),
        ):
            with self.subTest(method=method, path=path):
                request = urllib.request.Request(self.base_url + path, method=method)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 404)

    def test_unlisted_files_are_not_served(self):
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                request = urllib.request.Request(self.base_url + "/server.py", method=method)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
