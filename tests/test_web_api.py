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
        cls.orig_profiles_dir = copier_hub.PROFILES_DIR
        cls.orig_presets_file = copier_hub.PRESETS_FILE
        cls.orig_accounts_file = copier_hub.ACCOUNTS_FILE
        cls.orig_master_ini = copier_hub.MASTER_INI_FILE
        cls.orig_slave_ini = copier_hub.SLAVE_INI_FILE

        copier_hub.SYMBOL_MAP_FILE = os.path.join(cls.temp_dir, "symbol_map.json")
        copier_hub.LOT_RULES_FILE = os.path.join(cls.temp_dir, "lot_rules.json")
        copier_hub.AUTH_FILE = os.path.join(cls.temp_dir, "web_auth.json")
        copier_hub.BACKUP_DIR = os.path.join(cls.temp_dir, "backups")
        copier_hub.PROFILES_DIR = os.path.join(cls.temp_dir, "profiles")
        copier_hub.PRESETS_FILE = os.path.join(copier_hub.PROFILES_DIR, "presets.json")
        copier_hub.ACCOUNTS_FILE = os.path.join(copier_hub.PROFILES_DIR, "accounts.json")
        copier_hub.MASTER_INI_FILE = os.path.join(cls.temp_dir, "vantage_data", "mt5-server.ini")
        copier_hub.SLAVE_INI_FILE = os.path.join(cls.temp_dir, "ftmo_data", "mt5-server.ini")
        copier_hub.WEB_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
        os.makedirs(copier_hub.BACKUP_DIR, exist_ok=True)
        os.makedirs(copier_hub.PROFILES_DIR, exist_ok=True)

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
        copier_hub.PROFILES_DIR = cls.orig_profiles_dir
        copier_hub.PRESETS_FILE = cls.orig_presets_file
        copier_hub.ACCOUNTS_FILE = cls.orig_accounts_file
        copier_hub.MASTER_INI_FILE = cls.orig_master_ini
        copier_hub.SLAVE_INI_FILE = cls.orig_slave_ini

    def setUp(self):
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

    def test_position_snapshot_stealth_sl_tp_and_partial_close(self):
        last_positions = {}

        # Step 1: Open position on Vantage with pre-set SL/TP -> should send 0;0 to FTMO
        snapshot_1 = [{
            "ticket": 99001,
            "symbol": "NAS100.r",
            "type": 0,
            "volume": 0.40,
            "price": 20100.0,
            "sl": 20050.0,
            "tp": 20250.0
        }]
        last_positions, cmds_1 = copier_hub.process_position_snapshot(last_positions, snapshot_1)
        self.assertEqual(len(cmds_1), 1)
        self.assertTrue(cmds_1[0].startswith("OPEN;US100.cash;0;"))
        self.assertTrue(cmds_1[0].endswith(";0;0;99001"))

        # Step 2: Partial close on Vantage from 0.40 -> 0.20 (50% reduction)
        snapshot_2 = [{
            "ticket": 99001,
            "symbol": "NAS100.r",
            "type": 0,
            "volume": 0.20,
            "price": 20100.0,
            "sl": 20050.0,
            "tp": 20250.0
        }]
        last_positions, cmds_2 = copier_hub.process_position_snapshot(last_positions, snapshot_2)
        self.assertEqual(cmds_2, ["PARTIAL;US100.cash;0.5;99001"])

        # Step 3: Second partial close on Vantage from 0.20 -> 0.05 (75% of remaining)
        snapshot_3 = [{
            "ticket": 99001,
            "symbol": "NAS100.r",
            "type": 0,
            "volume": 0.05,
            "price": 20100.0,
            "sl": 20050.0,
            "tp": 20250.0
        }]
        last_positions, cmds_3 = copier_hub.process_position_snapshot(last_positions, snapshot_3)
        self.assertEqual(cmds_3, ["PARTIAL;US100.cash;0.75;99001"])

        # Step 4: Full close on Vantage
        snapshot_4 = []
        last_positions, cmds_4 = copier_hub.process_position_snapshot(last_positions, snapshot_4)
        self.assertEqual(cmds_4, ["CLOSE;US100.cash;99001"])

    def test_source_symbol_specific_lot_rules(self):
        # Configure symbol mappings where XAUUSD247 and XAUUSD.tv both map to XAUUSD
        symbols = {
            "XAUUSD247": "XAUUSD",
            "XAUUSD.tv": "XAUUSD"
        }
        req_sym = urllib.request.Request(
            f"{self.server_url}/api/symbols",
            data=json.dumps(symbols).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req_sym).close()

        # Configure distinct multipliers for XAUUSD247 (0.25x), XAUUSD.tv (25x), and fallback XAUUSD (fixed 2.0)
        rules = {
            "default_multiplier": 1.0,
            "min_lot": 0.01,
            "rules": {
                "XAUUSD247": {"multiplier": 0.25},
                "XAUUSD.tv": {"multiplier": 25.0},
                "XAUUSD": {"fixed_lot": 2.0}
            }
        }
        req_rules = urllib.request.Request(
            f"{self.server_url}/api/lot-rules",
            data=json.dumps(rules).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req_rules).close()

        last_positions = {}
        snapshot = [
            {"ticket": 1001, "symbol": "XAUUSD247", "type": 0, "volume": 1.0, "price": 2500.0, "sl": 0.0, "tp": 0.0},
            {"ticket": 1002, "symbol": "XAUUSD.tv", "type": 1, "volume": 0.1, "price": 2500.0, "sl": 0.0, "tp": 0.0},
            {"ticket": 1003, "symbol": "XAUUSD", "type": 0, "volume": 0.5, "price": 2500.0, "sl": 0.0, "tp": 0.0},
        ]
        _, cmds = copier_hub.process_position_snapshot(last_positions, snapshot)
        self.assertEqual(cmds, [
            "OPEN;XAUUSD;0;0.25;0;0;1001",
            "OPEN;XAUUSD;1;2.5;0;0;1002",
            "OPEN;XAUUSD;0;2.0;0;0;1003",
        ])

    def test_profiles_save_and_activate(self):
        # 1. GET initial profiles (auto-seeded with 4 default pairings)
        req_get = urllib.request.Request(
            f"{self.server_url}/api/profiles",
            headers=self._auth_header()
        )
        with urllib.request.urlopen(req_get) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("Vantage -> FTMO", data["presets"])
            self.assertIn("Exness -> FundedNext", data["presets"])

        # 2. Activate "Exness -> FundedNext" profile preset
        req_act = urllib.request.Request(
            f"{self.server_url}/api/profiles",
            data=json.dumps({"action": "activate", "name": "Exness -> FundedNext"}).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_act) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            self.assertEqual(res["active_profile"], "Exness -> FundedNext")

        # Verify in-memory hot-reload mapped USTECm -> NDX100
        self.assertEqual(copier_hub.map_symbol("USTECm"), "NDX100")

    def test_accounts_dual_side_switching(self):
        # 1. Save a Master account (Exness)
        master_payload = {
            "action": "save",
            "role": "master",
            "account": {
                "id": "m_exness",
                "name": "Exness Pro",
                "broker": "Exness",
                "server": "Exness-MT5Real",
                "login": "11223344",
                "password": "secret_master_pwd",
                "startup_symbol": "XAUUSDm",
                "linked_profile": ""
            }
        }
        req_m = urllib.request.Request(
            f"{self.server_url}/api/accounts",
            data=json.dumps(master_payload).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_m) as resp:
            res_m = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res_m["success"])
            # Password should be masked in response
            self.assertEqual(res_m["accounts"]["master_accounts"][0]["password"], "••••••••")

        # 2. Save a Slave account (FundedNext) linked to "Exness -> FundedNext"
        slave_payload = {
            "action": "save",
            "role": "slave",
            "account": {
                "id": "s_fn",
                "name": "FundedNext 100k",
                "broker": "FundedNext",
                "server": "FundedNext-Server",
                "login": "99887766",
                "password": "secret_slave_pwd",
                "startup_symbol": "XAUUSD",
                "linked_profile": "Exness -> FundedNext"
            }
        }
        req_s = urllib.request.Request(
            f"{self.server_url}/api/accounts",
            data=json.dumps(slave_payload).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req_s).close()

        # 3. Activate Master account (restart_container=False for unit test)
        req_act_m = urllib.request.Request(
            f"{self.server_url}/api/accounts",
            data=json.dumps({"action": "activate", "role": "master", "id": "m_exness", "restart_container": False}).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_act_m) as resp:
            res_act_m = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res_act_m["success"])
            self.assertTrue(res_act_m["ini_updated"])

        with open(copier_hub.MASTER_INI_FILE, "r", encoding="utf-8") as f:
            master_ini = f.read()
        self.assertIn("Server=Exness-MT5Real", master_ini)
        self.assertIn("Login=11223344", master_ini)
        self.assertIn("Password=secret_master_pwd", master_ini)
        self.assertIn("Expert=VantageSender", master_ini)
        self.assertIn("Symbol=XAUUSDm", master_ini)

        # 4. Activate Slave account -> should write ftmo_data/mt5-server.ini AND activate linked profile
        req_act_s = urllib.request.Request(
            f"{self.server_url}/api/accounts",
            data=json.dumps({"action": "activate", "role": "slave", "id": "s_fn", "restart_container": False}).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_act_s) as resp:
            res_act_s = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res_act_s["success"])
            self.assertEqual(res_act_s["profile_activated"], "Exness -> FundedNext")

        with open(copier_hub.SLAVE_INI_FILE, "r", encoding="utf-8") as f:
            slave_ini = f.read()
        self.assertIn("Server=FundedNext-Server", slave_ini)
        self.assertIn("Login=99887766", slave_ini)
        self.assertIn("Password=secret_slave_pwd", slave_ini)
        self.assertIn("Expert=FTMOReceiver", slave_ini)

if __name__ == "__main__":
    unittest.main()



