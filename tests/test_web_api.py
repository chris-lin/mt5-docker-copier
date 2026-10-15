import unittest
import json
import os
import shutil
import tempfile
import threading
import time
import urllib.request
import urllib.error
import base64

import copier_hub

class TestWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use a temporary directory for tests
        cls.temp_dir = tempfile.mkdtemp()
        cls.orig_symbol_file = copier_hub.SYMBOL_MAP_FILE
        cls.orig_lot_file = copier_hub.LOT_RULES_FILE
        cls.orig_auth_file = copier_hub.AUTH_FILE
        cls.orig_backup_dir = copier_hub.BACKUP_DIR
        cls.orig_web_dir = copier_hub.WEB_STATIC_DIR

        copier_hub.SYMBOL_MAP_FILE = os.path.join(cls.temp_dir, "symbol_map.json")
        copier_hub.LOT_RULES_FILE = os.path.join(cls.temp_dir, "lot_rules.json")
        copier_hub.AUTH_FILE = os.path.join(cls.temp_dir, "web_auth.json")
        copier_hub.BACKUP_DIR = os.path.join(cls.temp_dir, "backups")
        copier_hub.WEB_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
        os.makedirs(copier_hub.BACKUP_DIR, exist_ok=True)

        # Write initial dummy configs
        with open(copier_hub.SYMBOL_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump({"NAS100.r": "US100.cash"}, f)

        with open(copier_hub.LOT_RULES_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "default_multiplier": 1.0,
                "min_lot": 0.01,
                "rules": {
                    "US100.cash": { "multiplier": 2.0 }
                }
            }, f)

        copier_hub.load_configs()

        # Start Web Server on test port 18080
        cls.port = 18080
        cls.server_url = f"http://127.0.0.1:{cls.port}"
        cls.httpd = copier_hub.ThreadingHTTPServer(("127.0.0.1", cls.port), copier_hub.WebRequestHandler)
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        # Restore original paths
        copier_hub.SYMBOL_MAP_FILE = cls.orig_symbol_file
        copier_hub.LOT_RULES_FILE = cls.orig_lot_file
        copier_hub.AUTH_FILE = cls.orig_auth_file
        copier_hub.BACKUP_DIR = cls.orig_backup_dir
        copier_hub.WEB_STATIC_DIR = cls.orig_web_dir

    def _auth_header(self, user="admin", password="admin123"):
        creds = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("utf-8")
        return {"Authorization": f"Basic {creds}"}

    def test_validation_logic(self):
        # Symbol map validation
        self.assertTrue(copier_hub.validate_symbol_map({"A": "B"})[0])
        self.assertFalse(copier_hub.validate_symbol_map("not a dict")[0])
        self.assertFalse(copier_hub.validate_symbol_map({"": "B"})[0])
        self.assertFalse(copier_hub.validate_symbol_map({"A": ""})[0])

        # Lot rules validation
        valid_rules = {
            "default_multiplier": 1.5,
            "min_lot": 0.01,
            "rules": {
                "EURUSD": {"fixed_lot": 0.5},
                "XAUUSD": {"multiplier": 2.0},
                "US100": {"lot_map": {"0.1": 0.2, "0.5": 1.0}, "default_multiplier": 1.0}
            }
        }
        self.assertTrue(copier_hub.validate_lot_rules(valid_rules)[0])

        # Negative min_lot
        invalid_rules = dict(valid_rules)
        invalid_rules["min_lot"] = -0.01
        self.assertFalse(copier_hub.validate_lot_rules(invalid_rules)[0])

        # Empty rule
        invalid_empty = {
            "default_multiplier": 1.0,
            "min_lot": 0.01,
            "rules": {"EURUSD": {}}
        }
        self.assertFalse(copier_hub.validate_lot_rules(invalid_empty)[0])

    def test_unauthorized_access(self):
        req = urllib.request.Request(f"{self.server_url}/api/status")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 401)

    def test_get_status(self):
        req = urllib.request.Request(
            f"{self.server_url}/api/status",
            headers=self._auth_header()
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["hub_running"])
            self.assertIn("vantage", data)
            self.assertIn("ftmo", data)
            self.assertIn("recent_logs", data)

    def test_get_and_post_symbols(self):
        # 1. GET
        req = urllib.request.Request(
            f"{self.server_url}/api/symbols",
            headers=self._auth_header()
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data.get("NAS100.r"), "US100.cash")

        # 2. POST (Hot-reload)
        new_symbols = {
            "NAS100.r": "US100.cash",
            "XAUUSD247": "XAUUSD",
            "BTCUSD.r": "BTCUSD.cash"
        }
        req_post = urllib.request.Request(
            f"{self.server_url}/api/symbols",
            data=json.dumps(new_symbols).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_post) as resp:
            self.assertEqual(resp.status, 200)
            res_data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res_data["success"])
            self.assertEqual(res_data["count"], 3)

        # 3. Verify memory hot-reload in copier_hub
        self.assertEqual(copier_hub.map_symbol("BTCUSD.r"), "BTCUSD.cash")

        # 4. Check that backup file was created
        backups = os.listdir(copier_hub.BACKUP_DIR)
        self.assertTrue(any(b.startswith("symbol_map_") for b in backups))

    def test_get_and_post_lot_rules(self):
        new_rules = {
            "default_multiplier": 1.2,
            "min_lot": 0.02,
            "rules": {
                "BTCUSD.cash": {"fixed_lot": 0.05},
                "XAUUSD": {"multiplier": 1.8}
            }
        }
        req_post = urllib.request.Request(
            f"{self.server_url}/api/lot-rules",
            data=json.dumps(new_rules).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_post) as resp:
            self.assertEqual(resp.status, 200)
            res_data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res_data["success"])

        # Check immediate calculation using new rules
        self.assertEqual(copier_hub.calculate_target_lot("BTCUSD.cash", 0.5), 0.05)
        self.assertEqual(copier_hub.calculate_target_lot("XAUUSD", 1.0), 1.8)
        # Fallback to default_multiplier (1.2)
        self.assertEqual(copier_hub.calculate_target_lot("OTHER", 1.0), 1.2)

    def test_html_dashboard_served(self):
        req = urllib.request.Request(
            f"{self.server_url}/",
            headers=self._auth_header()
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
            self.assertIn("MT5 Copier Dashboard", html)
            self.assertIn("Symbol Mapping", html)
            self.assertIn("Lot Sizing Rules", html)

if __name__ == "__main__":
    unittest.main()
