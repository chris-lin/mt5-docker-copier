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
        cls.orig_lucid_file = copier_hub.LUCID_FILE
        cls.orig_master_ini = copier_hub.MASTER_INI_FILE
        cls.orig_slave_ini = copier_hub.SLAVE_INI_FILE

        copier_hub.SYMBOL_MAP_FILE = os.path.join(cls.temp_dir, "symbol_map.json")
        copier_hub.LOT_RULES_FILE = os.path.join(cls.temp_dir, "lot_rules.json")
        copier_hub.AUTH_FILE = os.path.join(cls.temp_dir, "web_auth.json")
        copier_hub.BACKUP_DIR = os.path.join(cls.temp_dir, "backups")
        copier_hub.PROFILES_DIR = os.path.join(cls.temp_dir, "profiles")
        copier_hub.PRESETS_FILE = os.path.join(copier_hub.PROFILES_DIR, "presets.json")
        copier_hub.ACCOUNTS_FILE = os.path.join(copier_hub.PROFILES_DIR, "accounts.json")
        copier_hub.LUCID_FILE = os.path.join(copier_hub.PROFILES_DIR, "lucid.json")
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
        copier_hub.LUCID_FILE = cls.orig_lucid_file
        copier_hub.MASTER_INI_FILE = cls.orig_master_ini
        copier_hub.SLAVE_INI_FILE = cls.orig_slave_ini

    def setUp(self):
        copier_hub.reset_futures_runtime_state()
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
            self.assertIn("vps", data)
            vps = data["vps"]
            self.assertIn("cpu_percent", vps)
            self.assertIn("ram_percent", vps)
            self.assertIn("disk_percent", vps)
            self.assertIn("uptime_str", vps)
            self.assertGreater(vps["disk_total_gb"], 0)
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
            self.assertIn("/style.css", html)
            self.assertIn("/app.js", html)

        # Verify split CSS and JS assets are served properly
        req_css = urllib.request.Request(f"{self.server_url}/style.css", headers=self._auth_header())
        with urllib.request.urlopen(req_css) as resp_css:
            self.assertEqual(resp_css.status, 200)
            self.assertIn("vps-strip-wrapper", resp_css.read().decode("utf-8"))

        req_js = urllib.request.Request(f"{self.server_url}/app.js", headers=self._auth_header())
        with urllib.request.urlopen(req_js) as resp_js:
            self.assertEqual(resp_js.status, 200)
            self.assertIn("renderVpsMetrics", resp_js.read().decode("utf-8"))

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

    def test_lucid_parallel_whitelist_and_integer_partial_close(self):
        # 1. Enable Lucid Trading via POST /api/lucid with strict whitelist (NAS100.r -> MNQZ6, XAUUSD247 -> MGCZ6, BTCUSD -> MBTZ6)
        lucid_payload = {
            "enabled": True,
            "mode": "webhook",
            "webhook_url": "",
            "webhook_secret": "my_webhook_secret",
            "whitelist_rules": {
                "NAS100.r": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
                "XAUUSD247": {"target_symbol": "MGCZ6", "sizing_mode": "multiplier", "multiplier": 2.5, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
                "BTCUSD": {"target_symbol": "MBTZ6", "sizing_mode": "fixed", "multiplier": 1.0, "fixed_contracts": 2, "min_contracts": 1, "max_contracts": 10}
            }
        }
        req_post = urllib.request.Request(
            f"{self.server_url}/api/lucid",
            data=json.dumps(lucid_payload).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_post) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            self.assertEqual(res["lucid"]["webhook_secret"], "••••••••")

        # 2. Snapshot 1: Open NAS100.r (0.40 lot -> 4 MNQZ6), EURUSD (0.50 lot -> NOT whitelisted), BTCUSD (0.10 lot -> 2 MBTZ6 fixed)
        last_pos = {}
        snap_1 = [
            {"ticket": 5001, "symbol": "NAS100.r", "type": 0, "volume": 0.40, "price": 20000.0, "sl": 0, "tp": 0},
            {"ticket": 5002, "symbol": "EURUSD", "type": 1, "volume": 0.50, "price": 1.0800, "sl": 0, "tp": 0},
            {"ticket": 5003, "symbol": "BTCUSD", "type": 0, "volume": 0.10, "price": 68000.0, "sl": 0, "tp": 0}
        ]
        last_pos, ftmo_cmds_1 = copier_hub.process_position_snapshot(last_pos, snap_1)
        # FTMO receives all 3 trades
        self.assertEqual(len(ftmo_cmds_1), 3)
        # Lucid ONLY receives whitelisted NAS100.r (4 MNQZ6) and BTCUSD (2 MBTZ6), ignoring EURUSD
        with copier_hub.lucid_lock:
            lucid_orders = list(copier_hub.lucid_dispatched_orders)
        self.assertEqual(len(lucid_orders), 2)
        self.assertEqual(lucid_orders[0], {"event": "OPEN", "ticket": 5001, "src_symbol": "NAS100.r", "symbol": "MNQZ6", "action": "Buy", "quantity": 4})
        self.assertEqual(lucid_orders[1], {"event": "OPEN", "ticket": 5003, "src_symbol": "BTCUSD", "symbol": "MBTZ6", "action": "Buy", "quantity": 2})

        # 3. Snapshot 2: Partial close 50% on NAS100.r (0.40 -> 0.20) -> Lucid sells 2 MNQZ6 (leaving 2 contracts)
        snap_2 = [
            {"ticket": 5001, "symbol": "NAS100.r", "type": 0, "volume": 0.20, "price": 20000.0, "sl": 0, "tp": 0},
            {"ticket": 5002, "symbol": "EURUSD", "type": 1, "volume": 0.50, "price": 1.0800, "sl": 0, "tp": 0},
            {"ticket": 5003, "symbol": "BTCUSD", "type": 0, "volume": 0.10, "price": 68000.0, "sl": 0, "tp": 0}
        ]
        last_pos, _ = copier_hub.process_position_snapshot(last_pos, snap_2)
        with copier_hub.lucid_lock:
            lucid_orders = list(copier_hub.lucid_dispatched_orders)
        self.assertEqual(len(lucid_orders), 3)
        self.assertEqual(lucid_orders[2], {"event": "PARTIAL", "ticket": 5001, "src_symbol": "NAS100.r", "symbol": "MNQZ6", "action": "Sell", "quantity": 2})

        # 4. Snapshot 3: Close all positions -> Lucid sells remaining 2 MNQZ6 and 2 MBTZ6
        last_pos, _ = copier_hub.process_position_snapshot(last_pos, [])
        with copier_hub.lucid_lock:
            lucid_orders = list(copier_hub.lucid_dispatched_orders)
        self.assertEqual(len(lucid_orders), 5)
        self.assertEqual(lucid_orders[3], {"event": "CLOSE", "ticket": 5001, "src_symbol": "NAS100.r", "symbol": "MNQZ6", "action": "Sell", "quantity": 2})
        self.assertEqual(lucid_orders[4], {"event": "CLOSE", "ticket": 5003, "src_symbol": "BTCUSD", "symbol": "MBTZ6", "action": "Sell", "quantity": 2})

    def test_multi_prop_futures_targets_and_universal_master_webhook(self):
        # 1. Configure 2 simultaneous Futures Prop Firm targets: Lucid Trading (1.0x) and Topstep (2.0x)
        futures_payload = {
            "enabled": True,
            "targets": [
                {
                    "id": "lucid_1",
                    "name": "Lucid Trading 50K",
                    "prop_firm": "Lucid Trading",
                    "enabled": True,
                    "scale": 1.0,
                    "mode": "webhook",
                    "webhook_url": ""
                },
                {
                    "id": "topstep_1",
                    "name": "Topstep 100K",
                    "prop_firm": "Topstep",
                    "enabled": True,
                    "scale": 2.0,
                    "mode": "webhook",
                    "webhook_url": ""
                }
            ],
            "master_source": {
                "mode": "hybrid",
                "webhook_secret": "tv_master_secret",
                "reverse_map": {
                    "MNQZ6": {"mt5_symbol": "US100.cash", "lot_per_contract": 0.10},
                    "MGCZ6": {"mt5_symbol": "XAUUSD", "lot_per_contract": 0.10}
                }
            },
            "whitelist_rules": {
                "NAS100.r": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
                "XAUUSD247": {"target_symbol": "MGCZ6", "sizing_mode": "multiplier", "multiplier": 2.5, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20}
            }
        }
        req_cfg = urllib.request.Request(
            f"{self.server_url}/api/futures",
            data=json.dumps(futures_payload).encode("utf-8"),
            headers={**self._auth_header(), "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_cfg) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res["success"])
            self.assertEqual(len(res["futures"]["targets"]), 2)

        # 2. Place a TradingView / Tradovate Futures Master order via POST /api/master-webhook (Buy 2 MNQZ6)
        wh_open = {
            "secret": "tv_master_secret",
            "symbol": "MNQZ6",
            "action": "buy",
            "quantity": 2,
            "ticket": 88001
        }
        req_wh1 = urllib.request.Request(
            f"{self.server_url}/api/master-webhook",
            data=json.dumps(wh_open).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_wh1) as resp:
            res_wh1 = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(res_wh1["success"])
            # FTMO receives reverse-mapped US100.cash (2 contracts * 0.10 = 0.20 base, or 2.0 * 2.0 = 4.0 from US100.cash rule in setUp)
            self.assertEqual(len(res_wh1["commands_sent"]), 1)
            self.assertTrue(res_wh1["commands_sent"][0].startswith("OPEN;US100.cash;0;"))

        # Verify BOTH Lucid Trading (2 MNQZ6) and Topstep (4 MNQZ6 at 2.0x scale) received the order!
        with copier_hub.lucid_lock:
            fut_orders = list(copier_hub.futures_dispatched_orders)
        self.assertEqual(len(fut_orders), 2)
        self.assertEqual(fut_orders[0]["target_id"], "lucid_1")
        self.assertEqual(fut_orders[0]["quantity"], 2)
        self.assertEqual(fut_orders[1]["target_id"], "topstep_1")
        self.assertEqual(fut_orders[1]["quantity"], 4)

        # 3. Partial close 1 contract on TradingView Master (2 -> 1, 50% reduction)
        wh_partial = {
            "secret": "tv_master_secret",
            "symbol": "MNQZ6",
            "action": "partial",
            "quantity": 1,
            "ticket": 88001
        }
        req_wh2 = urllib.request.Request(
            f"{self.server_url}/api/master-webhook",
            data=json.dumps(wh_partial).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_wh2) as resp:
            res_wh2 = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(res_wh2["commands_sent"], ["PARTIAL;US100.cash;0.5;88001"])

        with copier_hub.lucid_lock:
            fut_orders = list(copier_hub.futures_dispatched_orders)
        self.assertEqual(len(fut_orders), 4)
        self.assertEqual(fut_orders[2]["target_id"], "lucid_1")
        self.assertEqual(fut_orders[2]["quantity"], 1)  # 50% of 2 = 1
        self.assertEqual(fut_orders[3]["target_id"], "topstep_1")
        self.assertEqual(fut_orders[3]["quantity"], 2)  # 50% of 4 = 2

        # 4. Full close on TradingView Master
        wh_close = {
            "secret": "tv_master_secret",
            "symbol": "MNQZ6",
            "action": "close",
            "ticket": 88001
        }
        req_wh3 = urllib.request.Request(
            f"{self.server_url}/api/master-webhook",
            data=json.dumps(wh_close).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req_wh3) as resp:
            res_wh3 = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(res_wh3["commands_sent"], ["CLOSE;US100.cash;88001"])

        with copier_hub.lucid_lock:
            fut_orders = list(copier_hub.futures_dispatched_orders)
        self.assertEqual(len(fut_orders), 6)
        self.assertEqual(fut_orders[4]["event"], "CLOSE")
        self.assertEqual(fut_orders[4]["quantity"], 1)
        self.assertEqual(fut_orders[5]["event"], "CLOSE")
        self.assertEqual(fut_orders[5]["quantity"], 2)

    def test_tradovate_direct_free_mode_and_wildcard_futures_months(self):
        import futures_copier
        from unittest.mock import patch, MagicMock

        # 1. Verify wildcard contract month extraction & reverse mapping to MT5 FTMO
        cfg = futures_copier.get_default_futures_config()
        sym_map = {"NAS100.r": "US100.cash"}

        self.assertEqual(futures_copier.extract_futures_root("MGCM6"), "MGC")
        self.assertEqual(futures_copier.extract_futures_root("MGC1!"), "MGC")
        self.assertEqual(futures_copier.extract_futures_root("MNQU25"), "MNQ")
        self.assertEqual(futures_copier.extract_futures_root("MESZ6"), "MES")

        cfd_sym, cfd_lot, is_rev = futures_copier.resolve_reverse_futures_to_mt5("MGCM6", 3, cfg, sym_map)
        self.assertTrue(is_rev)
        self.assertEqual(cfd_sym, "XAUUSD")
        self.assertEqual(cfd_lot, 0.30)

        cfd_sym2, cfd_lot2, is_rev2 = futures_copier.resolve_reverse_futures_to_mt5("MGC1!", 2, cfg, sym_map)
        self.assertTrue(is_rev2)
        self.assertEqual(cfd_sym2, "XAUUSD")
        self.assertEqual(cfd_lot2, 0.20)

        cfd_sym3, cfd_lot3, is_rev3 = futures_copier.resolve_reverse_futures_to_mt5("MNQM6", 4, cfg, sym_map)
        self.assertTrue(is_rev3)
        self.assertEqual(cfd_sym3, "US100.cash")
        self.assertEqual(cfd_lot3, 0.40)

        # 2. Verify webhook_format payload adapters (Standard, TradersPost, PickMyTrade)
        sample_open = {"event": "OPEN", "ticket": 901, "src_symbol": "XAUUSD", "symbol": "MGCZ6", "action": "Buy", "quantity": 2}
        sample_close = {"event": "CLOSE", "ticket": 901, "src_symbol": "XAUUSD", "symbol": "MGCZ6", "action": "Sell", "quantity": 2}

        tp_open = futures_copier.format_webhook_payload(sample_open, {"webhook_format": "traderspost", "webhook_secret": "s1"})
        self.assertEqual(tp_open, {"ticker": "MGCZ6", "action": "buy", "quantity": 2, "secret": "s1"})
        tp_close = futures_copier.format_webhook_payload(sample_close, {"webhook_format": "traderspost"})
        self.assertEqual(tp_close, {"ticker": "MGCZ6", "action": "exit"})

        pmt_open = futures_copier.format_webhook_payload(
            sample_open,
            {"webhook_format": "pickmytrade", "webhook_secret": "pmt_tok", "tradovate": {"account_spec": "TDFY50K"}}
        )
        self.assertEqual(pmt_open, {"symbol": "MGCZ6", "data": "buy", "quantity": 2, "account_name": "TDFY50K", "token": "pmt_tok"})

        # 3. Verify $0 Free Direct Tradovate API auto-discovery, order placement & token auto-renewal
        captured_requests = []

        def fake_urlopen(req, timeout=8):
            url = req.full_url
            captured_requests.append((req.get_method(), url, req.data.decode("utf-8") if req.data else None))
            resp_mock = MagicMock()
            resp_mock.status = 200
            resp_mock.__enter__.return_value = resp_mock
            if "/account/list" in url:
                resp_mock.read.return_value = json.dumps([{"id": 778899, "name": "TDFY50K_SIM", "active": True}]).encode("utf-8")
            elif "/auth/renewaccesstoken" in url:
                resp_mock.read.return_value = json.dumps({"accessToken": "renewed_bearer_tok_999"}).encode("utf-8")
            else:
                resp_mock.read.return_value = json.dumps({"orderId": 123456}).encode("utf-8")
            return resp_mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            target_cfg = {
                "id": "tradeify_main",
                "name": "Tradeify 50K",
                "prop_firm": "Tradeify",
                "enabled": True,
                "mode": "tradovate",
                "tradovate": {
                    "env": "demo",
                    "account_spec": "",
                    "account_id": 0,
                    "access_token": "initial_bearer_tok_111"
                }
            }
            # Placing an order with blank account_spec/account_id should auto-discover TDFY50K_SIM (778899) and POST /order/placeorder
            futures_copier.execute_target_http(sample_open, target_cfg, copier_hub.log_event)
            self.assertEqual(target_cfg["tradovate"]["account_spec"], "TDFY50K_SIM")
            self.assertEqual(target_cfg["tradovate"]["account_id"], 778899)
            self.assertEqual(len(captured_requests), 2)
            self.assertIn("/account/list", captured_requests[0][1])
            self.assertIn("/order/placeorder", captured_requests[1][1])
            placed_body = json.loads(captured_requests[1][2])
            self.assertEqual(placed_body["accountSpec"], "TDFY50K_SIM")
            self.assertEqual(placed_body["accountId"], 778899)
            self.assertEqual(placed_body["symbol"], "MGCZ6")
            self.assertEqual(placed_body["orderQty"], 2)

            # Renewing tokens should update access_token to renewed_bearer_tok_999
            saved_configs = []
            res_renew = futures_copier.renew_all_tradovate_tokens_once(
                {"enabled": True, "targets": [target_cfg]},
                lambda c: saved_configs.append(c),
                copier_hub.log_event
            )
            self.assertTrue(res_renew["changed"])
            self.assertEqual(len(saved_configs), 1)
            self.assertEqual(saved_configs[0]["targets"][0]["tradovate"]["access_token"], "renewed_bearer_tok_999")

    def test_tradovate_master_scale_in_reverse_and_self_loop_guard(self):
        import futures_copier
        from unittest.mock import patch, MagicMock

        # Configure Tradeify 50K as Tradovate Master (account_id=778899, account_spec="TDFY50K_SIM")
        # AND enable 2 Futures targets:
        #   - Target 1: Same Tradeify 50K account (778899 / TDFY50K_SIM) -> MUST be skipped by Anti-Self-Loop Guard!
        #   - Target 2: Separate Lucid 50K account (889900 / LUCID50K_SIM) -> MUST receive parallel Futures orders!
        futures_cfg = futures_copier.get_default_futures_config()
        futures_cfg["enabled"] = True
        futures_cfg["master_source"]["tradovate_master"] = {
            "enabled": True,
            "env": "demo",
            "poll_interval_sec": 1.0,
            "account_id": 778899,
            "account_spec": "TDFY50K_SIM",
            "access_token": "tok_master_778899"
        }
        futures_cfg["targets"] = [
            {
                "id": "tradeify_self",
                "name": "Tradeify 50K (Self)",
                "prop_firm": "Tradeify",
                "enabled": True,
                "scale": 1.0,
                "mode": "tradovate",
                "tradovate": {
                    "env": "demo",
                    "account_spec": "TDFY50K_SIM",
                    "account_id": 778899,
                    "access_token": "tok_master_778899"
                }
            },
            {
                "id": "lucid_second",
                "name": "Lucid 50K (Second Prop)",
                "prop_firm": "Lucid Trading",
                "enabled": True,
                "scale": 1.0,
                "mode": "webhook",
                "webhook_url": "",
                "tradovate": {
                    "env": "demo",
                    "account_spec": "LUCID50K_SIM",
                    "account_id": 889900,
                    "access_token": ""
                }
            }
        ]
        futures_cfg["whitelist_rules"]["MGCM6"] = {
            "target_symbol": "MGCM6",
            "sizing_mode": "multiplier",
            "multiplier": 1.0,
            "fixed_contracts": 1,
            "min_contracts": 1,
            "max_contracts": 20
        }
        copier_hub.save_atomic_json(copier_hub.LUCID_FILE, futures_cfg, "futures_copier")

        current_net_pos = [0]

        def fake_tradovate_urlopen(req, timeout=6):
            url = req.full_url
            resp_mock = MagicMock()
            resp_mock.status = 200
            resp_mock.__enter__.return_value = resp_mock
            if "/position/list" in url:
                if current_net_pos[0] == 0:
                    pos_list = [{"id": 50001, "accountId": 778899, "contractId": 301, "netPos": 0, "netPrice": 0.0}]
                else:
                    pos_list = [{"id": 50001, "accountId": 778899, "contractId": 301, "netPos": current_net_pos[0], "netPrice": 2950.5}]
                resp_mock.read.return_value = json.dumps(pos_list).encode("utf-8")
            elif "/contract/item?id=301" in url:
                resp_mock.read.return_value = json.dumps({"id": 301, "name": "MGCM6"}).encode("utf-8")
            else:
                resp_mock.read.return_value = json.dumps({}).encode("utf-8")
            return resp_mock

        with patch("urllib.request.urlopen", side_effect=fake_tradovate_urlopen):
            # Step 1: Open Long 1 MGCM6 on Tradovate (netPos = +1)
            current_net_pos[0] = 1
            cmds_1 = futures_copier.poll_tradovate_master_once(
                futures_cfg,
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertEqual(cmds_1, ["OPEN;XAUUSD;0;0.1;0;0;5000101"])

            # Check Anti-Self-Loop Guard: ONLY lucid_second received the order; tradeify_self was skipped!
            with copier_hub.lucid_lock:
                dispatched_1 = list(copier_hub.futures_dispatched_orders)
            self.assertEqual(len(dispatched_1), 1)
            self.assertEqual(dispatched_1[0]["target_id"], "lucid_second")
            self.assertEqual(dispatched_1[0]["event"], "OPEN")
            self.assertEqual(dispatched_1[0]["quantity"], 1)

            # Step 2: Scale-In Long +2 MGCM6 on Tradovate (netPos: 1 -> 3)
            # Generates new layer ticket #5000102 for the +2 contract addition so FTMOReceiver.mq5 opens +0.2 lot!
            current_net_pos[0] = 3
            cmds_2 = futures_copier.poll_tradovate_master_once(
                futures_cfg,
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertEqual(cmds_2, ["OPEN;XAUUSD;0;0.2;0;0;5000102"])

            # Step 3: Partial Close 1 MGCM6 on Tradovate (netPos: 3 -> 2)
            # Reduces LIFO layer ticket #5000102 from 2 -> 1 (50% partial close on #5000102)
            current_net_pos[0] = 2
            cmds_3 = futures_copier.poll_tradovate_master_once(
                futures_cfg,
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertEqual(cmds_3, ["PARTIAL;XAUUSD;0.5;5000102"])

            # Step 4: Reverse / Flip directly from Long 2 to Short 1 MGCM6 (netPos: +2 -> -1)
            # Closes BOTH Long layer tickets (#5000101 and #5000102) FIRST, then opens Short 0.1 lot (#5000151)!
            current_net_pos[0] = -1
            cmds_4 = futures_copier.poll_tradovate_master_once(
                futures_cfg,
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertEqual(cmds_4, [
                "CLOSE;XAUUSD;5000101",
                "CLOSE;XAUUSD;5000102",
                "OPEN;XAUUSD;1;0.1;0;0;5000151"
            ])

            # Step 5: Flatten Short 1 MGCM6 (netPos: -1 -> 0)
            current_net_pos[0] = 0
            cmds_5 = futures_copier.poll_tradovate_master_once(
                futures_cfg,
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertEqual(cmds_5, ["CLOSE;XAUUSD;5000151"])

    def test_all_four_copying_routes_end_to_end(self):
        import futures_copier
        from unittest.mock import patch, MagicMock

        # Configure realistic MT5 symbol map & lot rules (including target-symbol rules for XAUUSD and US100.cash)
        with open(copier_hub.SYMBOL_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump({"NAS100.r": "US100.cash", "XAUUSD247": "XAUUSD"}, f)
        with open(copier_hub.LOT_RULES_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "default_multiplier": 1.0,
                "min_lot": 0.01,
                "rules": {
                    "XAUUSD247": {"multiplier": 0.25},
                    "XAUUSD": {"fixed_lot": 2.0},
                    "US100.cash": {"multiplier": 2.0}
                }
            }, f)
        copier_hub.load_configs()

        f_cfg = futures_copier.get_default_futures_config()
        f_cfg["enabled"] = True
        f_cfg["targets"] = [{
            "id": "tradeify_main",
            "name": "Tradeify 50K",
            "prop_firm": "Tradeify",
            "enabled": True,
            "scale": 1.0,
            "mode": "tradovate",
            "tradovate": {
                "env": "demo",
                "account_spec": "TDFY50K_SIM",
                "account_id": 778899,
                "access_token": "tok_tradeify_123"
            }
        }]
        f_cfg["master_source"]["tradovate_master"] = {
            "enabled": True,
            "env": "demo",
            "account_id": 778899,
            "account_spec": "TDFY50K_SIM",
            "access_token": "tok_tradeify_123"
        }
        copier_hub.save_atomic_json(copier_hub.LUCID_FILE, f_cfg, "futures_copier")

        def fake_urlopen(req, timeout=6):
            url = req.full_url
            resp = MagicMock()
            resp.status = 200
            resp.__enter__.return_value = resp
            if "/position/list" in url:
                resp.read.return_value = json.dumps([
                    {"id": 6001, "accountId": 778899, "contractId": 301, "netPos": 1, "netPrice": 2950.0},
                    {"id": 6002, "accountId": 778899, "contractId": 302, "netPos": -3, "netPrice": 20100.0}
                ]).encode("utf-8")
            elif "/contract/item?id=301" in url:
                resp.read.return_value = json.dumps({"id": 301, "name": "MGCM6"}).encode("utf-8")
            elif "/contract/item?id=302" in url:
                resp.read.return_value = json.dumps({"id": 302, "name": "MNQM6"}).encode("utf-8")
            else:
                resp.read.return_value = json.dumps({"orderId": 999}).encode("utf-8")
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            # ROUTE 1 (MT5 -> MT5) & ROUTE 2 (MT5 -> Futures Tradeify)
            futures_copier.reset_futures_runtime_state()
            _, mt5_cmds = copier_hub.process_position_snapshot({}, [
                {"ticket": 1001, "symbol": "XAUUSD247", "type": 0, "volume": 0.80, "price": 2900.0, "sl": 0, "tp": 0},
                {"ticket": 1002, "symbol": "NAS100.r", "type": 1, "volume": 0.30, "price": 20000.0, "sl": 0, "tp": 0}
            ])
            self.assertEqual(mt5_cmds, [
                "OPEN;XAUUSD;0;0.2;0;0;1001",
                "OPEN;US100.cash;1;0.6;0;0;1002"
            ])
            with copier_hub.lucid_lock:
                fut_dispatched = list(copier_hub.futures_dispatched_orders)
            self.assertEqual(len(fut_dispatched), 2)
            self.assertEqual(fut_dispatched[0]["symbol"], "MGCZ6")
            self.assertEqual(fut_dispatched[0]["quantity"], 2)  # 0.80 * 2.5 = 2 contracts
            self.assertEqual(fut_dispatched[1]["symbol"], "MNQZ6")
            self.assertEqual(fut_dispatched[1]["quantity"], 3)  # 0.30 * 10.0 = 3 contracts

            # ROUTE 3 (Tradovate Master -> MT5 FTMO)
            # Even though XAUUSD has fixed_lot=2.0 and US100.cash has multiplier=2.0 for CFD copying,
            # Futures Master 1 MGCM6 MUST map to 0.10 lot XAUUSD, and 3 MNQM6 MUST map to 0.30 lot US100.cash!
            futures_copier.reset_futures_runtime_state()
            tv_cmds = futures_copier.poll_tradovate_master_once(
                f_cfg,
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertEqual(tv_cmds, [
                "OPEN;XAUUSD;0;0.1;0;0;600101",
                "OPEN;US100.cash;1;0.3;0;0;600251"
            ])

            # ROUTE 4 (TradingView Webhook -> MT5 FTMO, ticketless scale-in, partial close, and full close)
            futures_copier.reset_futures_runtime_state()
            ok1, r1 = futures_copier.apply_master_webhook_signal(
                {"symbol": "MGCM6", "action": "buy", "quantity": 1},
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertTrue(ok1)
            self.assertEqual(len(r1["commands_sent"]), 1)
            self.assertTrue(r1["commands_sent"][0].startswith("OPEN;XAUUSD;0;0.1;0;0;"))

            # Ticketless Scale-In (+2 MGCM6 -> opens a second layer of 0.2 lot XAUUSD)
            ok2, r2 = futures_copier.apply_master_webhook_signal(
                {"symbol": "MGCM6", "action": "buy", "quantity": 2},
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertTrue(ok2)
            self.assertEqual(len(r2["commands_sent"]), 1)
            self.assertTrue(r2["commands_sent"][0].startswith("OPEN;XAUUSD;0;0.2;0;0;"))

            # Ticketless Opposite Sell 1 MGCM6 (reduces net position from 3 -> 2 -> PARTIAL 0.5 on layer #2)
            ok3, r3 = futures_copier.apply_master_webhook_signal(
                {"symbol": "MGCM6", "action": "sell", "quantity": 1},
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertTrue(ok3)
            self.assertEqual(len(r3["commands_sent"]), 1)
            self.assertTrue(r3["commands_sent"][0].startswith("PARTIAL;XAUUSD;0.5;"))

            # Ticketless Close all MGCM6 -> closes both layers
            ok4, r4 = futures_copier.apply_master_webhook_signal(
                {"symbol": "MGCM6", "action": "close"},
                copier_hub.process_position_snapshot,
                copier_hub.log_event
            )
            self.assertTrue(ok4)
            self.assertEqual(len(r4["commands_sent"]), 2)
            self.assertTrue(all(c.startswith("CLOSE;XAUUSD;") for c in r4["commands_sent"]))

    def test_tradovate_token_sanitizer_and_env_fallback(self):
        import futures_copier
        from unittest.mock import patch, MagicMock

        # 1. Verify sanitize_tradovate_token handles Bearer prefix, quotes, and full JSON objects
        self.assertEqual(futures_copier.sanitize_tradovate_token('  "Bearer eyJhbGciOi123" '), "eyJhbGciOi123")
        self.assertEqual(futures_copier.sanitize_tradovate_token("Authorization: Bearer tok_abc_999"), "tok_abc_999")
        self.assertEqual(
            futures_copier.sanitize_tradovate_token('{"token": "tok_from_storage_777", "expirationTime": "2026-11-11"}'),
            "tok_from_storage_777"
        )

        # 2. Verify discover_tradovate_account auto-switches from 'demo' to 'live' when 'demo' returns HTTP 401
        def fake_401_on_demo(req, timeout=8):
            url = req.full_url
            if "demo.tradovateapi.com" in url:
                raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
            resp = MagicMock()
            resp.status = 200
            resp.__enter__.return_value = resp
            resp.read.return_value = json.dumps([{"id": 991122, "name": "LIVE_PROP_50K", "active": True}]).encode("utf-8")
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_401_on_demo):
            disc = futures_copier.discover_tradovate_account("demo", '{"token": "Bearer live_tok_555"}')
            self.assertEqual(disc["resolved_env"], "live")
            self.assertEqual(disc["clean_token"], "live_tok_555")
            self.assertEqual(disc["account_id"], 991122)
            self.assertEqual(disc["account_spec"], "LIVE_PROP_50K")

if __name__ == "__main__":
    unittest.main()



