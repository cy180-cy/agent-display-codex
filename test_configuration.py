import hashlib
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from configure import make_config
from live_observer import LiveObserver
from server import Server, load_config


class PortableConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".config-test-", dir=Path(__file__).resolve().parent)
        self.path = Path(self.temp.name) / "rollout-selected.jsonl"
        self.path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "fixture-selected"}}) + "\n", "utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_detects_selected_record_only_without_modifying_it(self):
        before = self.path.read_bytes()
        config = make_config(self.path, "示例任务")
        self.assertEqual(config["thread_id"], "fixture-selected")
        self.assertEqual(config["rollout_path"], str(self.path.resolve()))
        self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_id_cannot_disagree_with_metadata(self):
        with self.assertRaises(ValueError):
            make_config(self.path, "示例任务", "another-thread")

    def test_filename_fallback_and_missing_metadata(self):
        path = self.path.with_name("rollout-2030-01-01-00000000-0000-0000-0000-000000000001.jsonl")
        path.write_text("", "utf-8")
        self.assertEqual(make_config(path, "任务")["thread_id"], "00000000-0000-0000-0000-000000000001")
        self.path.write_text("", "utf-8")
        with self.assertRaises(ValueError):
            make_config(self.path, "任务")

    def test_invalid_title_and_non_log_file_are_rejected(self):
        for title in ("", "x" * 81):
            with self.assertRaises(ValueError):
                make_config(self.path, title)
        with self.assertRaises(ValueError):
            make_config(self.path.with_suffix(".txt"), "任务")

    def test_generated_config_is_loadable(self):
        config = make_config(self.path, "任务")
        path = self.path.with_name("config.local.json")
        path.write_text(json.dumps(config), "utf-8")
        self.assertEqual(load_config(path), config)


class LocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".http-test-", dir=Path(__file__).resolve().parent)
        self.path = Path(self.temp.name) / "rollout-fixture-http.jsonl"
        self.path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "fixture-http"}}) + "\n", "utf-8")
        self.original = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.observer = LiveObserver({"thread_id": "fixture-http", "title": '<任务 "测试">', "rollout_path": str(self.path)})
        self.observer.poll()
        self.server = Server(("127.0.0.1", 0), self.observer)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.worker.join(timeout=2)
        self.server.server_close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), self.original)
        self.temp.cleanup()

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_root_injects_only_local_config_and_escapes_title(self):
        status, body, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'content="fixture-http"', body)
        self.assertIn('&lt;任务 &quot;测试&quot;&gt;'.encode(), body)
        self.assertNotIn(b"DISPLAY_SELECTED_THREAD_ID", body)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_snapshot_requires_current_local_page_key(self):
        self.assertEqual(self.request("GET", "/api/snapshot")[0], 403)
        status, body, _ = self.request("GET", "/api/snapshot", {"X-Display-Key": self.server.key})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["thread"]["id"], "fixture-http")

    def test_cross_origin_request_is_rejected_even_with_key(self):
        self.assertEqual(self.request("GET", "/api/snapshot", {
            "X-Display-Key": self.server.key, "Origin": "https://example.invalid"})[0], 403)

    def test_cache_clear_does_not_delete_source(self):
        status, body, _ = self.request("POST", "/api/cache/clear", {"X-Display-Key": self.server.key})
        self.assertEqual(status, 200)
        self.assertIn("cleanup", json.loads(body))
        self.assertTrue(self.path.is_file())

    def test_only_loopback_binding_and_read_only_routes(self):
        with self.assertRaises(ValueError):
            Server(("0.0.0.0", 0), self.observer)
        self.assertEqual(self.request("PUT", "/api/snapshot")[0], 405)
        self.assertEqual(self.request("GET", "/../config.local.json")[0], 404)


if __name__ == "__main__":
    unittest.main()
