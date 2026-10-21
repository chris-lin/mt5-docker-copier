import socket
import threading
import json
import time
import os
import shutil
import datetime
import base64
import subprocess
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import urllib.request
import urllib.error

VERSION = "3.0"

HOST = "0.0.0.0"
VANTAGE_PORT = 9001
FTMO_PORT = 9002

WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# Base directories and file paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, ".config")
PROFILES_DIR = CONFIG_DIR  # Backward-compatible alias
TEMPLATE_DIR = os.path.join(BASE_DIR, "config_templates")

SYMBOL_MAP_FILE = os.path.join(CONFIG_DIR, "symbol_map.json")
SYMBOL_MAP_EXAMPLE_FILE = os.path.join(TEMPLATE_DIR, "symbol_map.example.json")
LOT_RULES_FILE = os.path.join(CONFIG_DIR, "lot_rules.json")
LOT_RULES_EXAMPLE_FILE = os.path.join(TEMPLATE_DIR, "lot_rules.example.json")
AUTH_FILE = os.path.join(CONFIG_DIR, "web_auth.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
WEB_STATIC_DIR = os.path.join(BASE_DIR, "web")
PRESETS_FILE = os.path.join(CONFIG_DIR, "presets.json")
PRESETS_EXAMPLE_FILE = os.path.join(TEMPLATE_DIR, "presets.example.json")
ACCOUNTS_FILE = os.path.join(CONFIG_DIR, "accounts.json")
ACCOUNTS_EXAMPLE_FILE = os.path.join(TEMPLATE_DIR, "accounts.example.json")
LUCID_FILE = os.path.join(CONFIG_DIR, "lucid.json")
LUCID_EXAMPLE_FILE = os.path.join(TEMPLATE_DIR, "lucid.example.json")

MASTER_INI_FILE = os.path.join(BASE_DIR, os.getenv("MASTER_INI_PATH", "vantage_data/mt5-server.ini"))
SLAVE_INI_FILE = os.path.join(BASE_DIR, os.getenv("SLAVE_INI_PATH", "ftmo_data/mt5-server.ini"))
MASTER_CONTAINER = os.getenv("MASTER_CONTAINER", "mt5_vantage")
SLAVE_CONTAINER = os.getenv("SLAVE_CONTAINER", "mt5_ftmo")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(WEB_STATIC_DIR, exist_ok=True)

def _migrate_or_seed_config(target_path: str, legacy_paths: list, example_path: str = None):
    """Migrate existing legacy config file into .config/ or seed from config_templates/ if missing."""
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    if os.path.exists(target_path):
        return
    for leg in legacy_paths:
        if leg and os.path.exists(leg):
            try:
                shutil.copy2(leg, target_path)
                return
            except Exception:
                pass
    if example_path and os.path.exists(example_path):
        try:
            shutil.copy2(example_path, target_path)
        except Exception:
            pass

_migrate_or_seed_config(SYMBOL_MAP_FILE, [os.path.join(BASE_DIR, "symbol_map.json")], SYMBOL_MAP_EXAMPLE_FILE)
_migrate_or_seed_config(LOT_RULES_FILE, [os.path.join(BASE_DIR, "lot_rules.json")], LOT_RULES_EXAMPLE_FILE)
_migrate_or_seed_config(AUTH_FILE, [os.path.join(BASE_DIR, "web_auth.json")])
_migrate_or_seed_config(PRESETS_FILE, [os.path.join(BASE_DIR, "profiles", "presets.json")], PRESETS_EXAMPLE_FILE)
_migrate_or_seed_config(ACCOUNTS_FILE, [os.path.join(BASE_DIR, "profiles", "accounts.json")])
_migrate_or_seed_config(LUCID_FILE, [os.path.join(BASE_DIR, "profiles", "lucid.json")], LUCID_EXAMPLE_FILE)

# Shared memory configurations
config_lock = threading.Lock()
SYMBOL_MAP = {}
LOT_RULES = {}
last_config_load_time = None

# Real-time connection and event tracking
status_lock = threading.Lock()
vantage_status = {"connected": False, "addr": None, "connected_at": None, "last_msg_at": None}
ftmo_status = {"connected": False, "addr": None, "connected_at": None, "last_msg_at": None}

event_logs = deque(maxlen=100)
log_lock = threading.Lock()

def log_event(category: str, msg: str):
    """Log an event to stdout and store in recent history for the web UI."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{category}] {msg}")
    with log_lock:
        event_logs.append({
            "time": now_str,
            "category": category,
            "msg": str(msg)
        })

def load_configs():
    """Load or reload JSON configuration files into memory safely."""
    global SYMBOL_MAP, LOT_RULES, last_config_load_time
    with config_lock:
        # Load Symbol Map
        if os.path.exists(SYMBOL_MAP_FILE):
            try:
                with open(SYMBOL_MAP_FILE, "r", encoding="utf-8") as f:
                    SYMBOL_MAP = json.load(f)
                log_event("CONFIG", f"Loaded {len(SYMBOL_MAP)} symbol mappings from {os.path.basename(SYMBOL_MAP_FILE)}")
            except Exception as e:
                log_event("ERROR", f"Failed to load {SYMBOL_MAP_FILE}: {e}")
        else:
            log_event("CONFIG", f"{os.path.basename(SYMBOL_MAP_FILE)} not found. Symbol mapping disabled.")
            SYMBOL_MAP = {}

        # Load Lot Rules
        if os.path.exists(LOT_RULES_FILE):
            try:
                with open(LOT_RULES_FILE, "r", encoding="utf-8") as f:
                    LOT_RULES = json.load(f)
                log_event("CONFIG", f"Loaded lot rules from {os.path.basename(LOT_RULES_FILE)}")
            except Exception as e:
                log_event("ERROR", f"Failed to load {LOT_RULES_FILE}: {e}")
                LOT_RULES = {"default_multiplier": 1.0, "min_lot": 0.01, "rules": {}}
        else:
            log_event("CONFIG", f"{os.path.basename(LOT_RULES_FILE)} not found. Using default lot multiplier (1.0).")
            LOT_RULES = {"default_multiplier": 1.0, "min_lot": 0.01, "rules": {}}

        last_config_load_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Initial configuration load
load_configs()

# Backup management
def backup_file(file_path: str, prefix: str):
    """Create a timestamped backup of the configuration file and keep the 10 most recent."""
    if not os.path.exists(file_path):
        return
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_filename = f"{prefix}_{timestamp}.json"
    bak_path = os.path.join(BACKUP_DIR, bak_filename)
    try:
        shutil.copy2(file_path, bak_path)
        # Rotate backups
        existing = [f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix + "_") and f.endswith(".json")]
        existing.sort()
        while len(existing) > 10:
            to_remove = os.path.join(BACKUP_DIR, existing.pop(0))
            os.remove(to_remove)
    except Exception as e:
        log_event("ERROR", f"Backup failed for {file_path}: {e}")

def save_atomic_json(file_path: str, data: dict, backup_prefix: str):
    """Write data to a temp file and atomically replace the target file."""
    backup_file(file_path, backup_prefix)
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, file_path)

# Authentication management
def get_auth_credentials():
    """Retrieve current username and password from file or environment."""
    if os.path.exists(AUTH_FILE):
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("username", "admin"), data.get("password", "admin123")
        except Exception:
            pass
    return os.getenv("WEB_USER", "admin"), os.getenv("WEB_PASS", "admin123")

def set_auth_credentials(username: str, password: str):
    """Save updated credentials to auth file."""
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump({"username": username, "password": password}, f, indent=2)

# Schema validation helpers
def validate_symbol_map(data):
    if not isinstance(data, dict):
        return False, "Symbol map must be a JSON object"
    for k, v in data.items():
        if not isinstance(k, str) or not k.strip():
            return False, f"Invalid source symbol key: {k}"
        if not isinstance(v, str) or not v.strip():
            return False, f"Invalid target symbol for '{k}': {v}"
    return True, ""

def validate_lot_rules(data):
    if not isinstance(data, dict):
        return False, "Lot rules must be a JSON object"
    
    min_lot = data.get("min_lot")
    if min_lot is None or not isinstance(min_lot, (int, float)) or min_lot <= 0:
        return False, "Field 'min_lot' must be a number > 0"
        
    def_mult = data.get("default_multiplier")
    if def_mult is None or not isinstance(def_mult, (int, float)) or def_mult <= 0:
        return False, "Field 'default_multiplier' must be a number > 0"
        
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        return False, "Field 'rules' must be a JSON object"
        
    for sym, rule in rules.items():
        if not isinstance(rule, dict):
            return False, f"Rule for symbol '{sym}' must be an object"
        has_any = False
        if "fixed_lot" in rule:
            has_any = True
            if not isinstance(rule["fixed_lot"], (int, float)) or rule["fixed_lot"] <= 0:
                return False, f"'fixed_lot' for '{sym}' must be a number > 0"
        if "multiplier" in rule:
            has_any = True
            if not isinstance(rule["multiplier"], (int, float)) or rule["multiplier"] <= 0:
                return False, f"'multiplier' for '{sym}' must be a number > 0"
        if "lot_map" in rule:
            has_any = True
            if not isinstance(rule["lot_map"], dict):
                return False, f"'lot_map' for '{sym}' must be an object"
            for k, v in rule["lot_map"].items():
                try:
                    if float(k) <= 0 or float(v) <= 0:
                        return False, f"All lot_map tiers for '{sym}' must have values > 0"
                except (ValueError, TypeError):
                    return False, f"Invalid numeric tier in lot_map for '{sym}': {k} -> {v}"
        if not has_any:
            return False, f"Rule for '{sym}' must define at least one of: fixed_lot, multiplier, or lot_map"
        if "default_multiplier" in rule:
            if not isinstance(rule["default_multiplier"], (int, float)) or rule["default_multiplier"] <= 0:
                return False, f"'default_multiplier' for '{sym}' must be a number > 0"

    return True, ""

# Trading Logic
ftmo_client_socket = None
ftmo_lock = threading.Lock()

def map_symbol(vantage_symbol: str) -> str:
    with config_lock:
        return SYMBOL_MAP.get(vantage_symbol, vantage_symbol)

def calculate_target_lot(target_symbol: str, src_lot: float, src_symbol: str = None) -> float:
    """Calculate target lot size for FTMO based on source/target symbol and lot mapping rules."""
    with config_lock:
        min_lot = LOT_RULES.get("min_lot", 0.01)
        default_multiplier = LOT_RULES.get("default_multiplier", 1.0)
        rules = LOT_RULES.get("rules", {})
        
        # Priority: 1. Source symbol rule (e.g. XAUUSD247, XAUUSD.tv) -> 2. Target symbol rule (e.g. XAUUSD)
        rule = (rules.get(src_symbol) if src_symbol else None) or rules.get(target_symbol)
        
        if rule:
            # 1. Check fixed lot
            if "fixed_lot" in rule:
                return round(float(rule["fixed_lot"]), 2)
                
            # 2. Check exact lot mapping (Lot Map)
            if "lot_map" in rule:
                str_lot = str(round(src_lot, 2))
                if str_lot in rule["lot_map"]:
                    return round(float(rule["lot_map"][str_lot]), 2)
                if src_lot in rule["lot_map"]:
                    return round(float(rule["lot_map"][src_lot]), 2)
                    
            # 3. Check symbol-specific multiplier
            if "multiplier" in rule:
                return max(min_lot, round(src_lot * float(rule["multiplier"]), 2))
                
            # 4. Symbol fallback multiplier
            if "default_multiplier" in rule:
                return max(min_lot, round(src_lot * float(rule["default_multiplier"]), 2))

        # 5. Global default multiplier
        return max(min_lot, round(src_lot * float(default_multiplier), 2))

def send_to_ftmo(cmd: str):
    global ftmo_client_socket
    with ftmo_lock:
        if ftmo_client_socket:
            try:
                ftmo_client_socket.sendall((cmd + "\n").encode('utf-8'))
                log_event("TO FTMO", cmd)
            except Exception as e:
                log_event("ERROR", f"Failed to send to FTMO: {e}")
                ftmo_client_socket = None
        else:
            log_event("WARN", "FTMO receiver not connected, command dropped!")

# Lucid Trading (Tradovate / Webhook) Parallel Futures Executor
lucid_lock = threading.Lock()
lucid_open_tickets = {}  # {ticket: {"src_symbol": str, "target_symbol": str, "action": "Buy"|"Sell", "contracts": int}}
lucid_dispatched_orders = deque(maxlen=50)

def get_default_lucid_config() -> dict:
    return {
        "enabled": False,
        "mode": "webhook",
        "webhook_url": "",
        "webhook_secret": "",
        "tradovate": {
            "env": "demo",
            "username": "",
            "password": "",
            "account_spec": "",
            "account_id": 0,
            "access_token": "",
            "cid": 0,
            "sec": ""
        },
        "whitelist_rules": {
            "XAUUSD247": {"target_symbol": "MGCZ6", "sizing_mode": "multiplier", "multiplier": 2.5, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "XAUUSD.tv": {"target_symbol": "MGCZ6", "sizing_mode": "multiplier", "multiplier": 250.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "XAUUSD": {"target_symbol": "MGCZ6", "sizing_mode": "fixed", "multiplier": 10.0, "fixed_contracts": 2, "min_contracts": 1, "max_contracts": 20},
            "NAS100.r": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "NAS100": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "BTCUSD": {"target_symbol": "MBTZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 10}
        }
    }

def load_lucid_data() -> dict:
    """Load Lucid Trading futures configuration from profiles/lucid.json or seed from lucid.example.json."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    if os.path.exists(LUCID_FILE):
        try:
            with open(LUCID_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "whitelist_rules" in data:
                    return data
        except Exception as e:
            log_event("ERROR", f"Failed to load {LUCID_FILE}: {e}")
    if os.path.exists(LUCID_EXAMPLE_FILE):
        try:
            with open(LUCID_EXAMPLE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "whitelist_rules" in data:
                    save_atomic_json(LUCID_FILE, data, "lucid")
                    return data
        except Exception as e:
            log_event("ERROR", f"Failed to load {LUCID_EXAMPLE_FILE}: {e}")
    default_cfg = get_default_lucid_config()
    save_atomic_json(LUCID_FILE, default_cfg, "lucid")
    return default_cfg

def mask_lucid_data(data: dict) -> dict:
    """Mask sensitive secrets/passwords in Lucid config before returning to Web UI."""
    masked = json.loads(json.dumps(data))
    if masked.get("webhook_secret"):
        masked["webhook_secret"] = "••••••••"
    tv = masked.get("tradovate", {})
    if isinstance(tv, dict):
        if tv.get("password"):
            tv["password"] = "••••••••"
        if tv.get("access_token"):
            tv["access_token"] = "••••••••"
        if tv.get("sec"):
            tv["sec"] = "••••••••"
    with lucid_lock:
        masked["open_tickets"] = dict(lucid_open_tickets)
    return masked

def calculate_lucid_order(src_symbol: str, src_lot: float, lucid_cfg: dict = None):
    """Evaluate strict whitelist for Lucid Trading and return (target_symbol, integer_contracts) or None."""
    if lucid_cfg is None:
        lucid_cfg = load_lucid_data()
    rules = lucid_cfg.get("whitelist_rules", {})
    rule = rules.get(src_symbol)
    if not rule:
        return None
    target_symbol = (rule.get("target_symbol") or "").strip()
    if not target_symbol:
        return None

    sizing_mode = rule.get("sizing_mode", "multiplier")
    if sizing_mode == "fixed":
        raw_qty = float(rule.get("fixed_contracts", 1))
    else:
        raw_qty = float(src_lot) * float(rule.get("multiplier", 1.0))

    min_c = max(1, int(rule.get("min_contracts", 1)))
    max_c = max(min_c, int(rule.get("max_contracts", 50)))
    contracts = max(min_c, min(max_c, int(round(raw_qty))))
    return target_symbol, contracts

def _execute_lucid_http(order_payload: dict, lucid_cfg: dict):
    """Send order payload to Webhook Bridge or Tradovate Direct REST API."""
    mode = lucid_cfg.get("mode", "webhook")
    try:
        if mode == "webhook":
            url = (lucid_cfg.get("webhook_url") or "").strip()
            if not url:
                log_event("LUCID", f"Simulated {order_payload['event']}: {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (No webhook_url configured)")
                return
            body = dict(order_payload)
            if lucid_cfg.get("webhook_secret"):
                body["secret"] = lucid_cfg["webhook_secret"]
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "MT5-Copier-Hub/2.0"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                log_event("TO LUCID", f"Webhook {order_payload['event']} -> {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (HTTP {resp.status})")
        else:
            tv = lucid_cfg.get("tradovate", {})
            env = tv.get("env", "demo")
            base_url = "https://live.tradovateapi.com/v1" if env == "live" else "https://demo.tradovateapi.com/v1"
            token = (tv.get("access_token") or "").strip()
            if not token:
                log_event("LUCID", f"Simulated Tradovate {order_payload['event']}: {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (No access_token configured)")
                return
            tv_body = {
                "accountSpec": tv.get("account_spec", ""),
                "accountId": int(tv.get("account_id") or 0),
                "action": order_payload["action"],
                "symbol": order_payload["symbol"],
                "orderQty": int(order_payload["quantity"]),
                "orderType": "Market",
                "isAutomated": True
            }
            req = urllib.request.Request(
                f"{base_url}/order/placeorder",
                data=json.dumps(tv_body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                log_event("TO LUCID", f"Tradovate {order_payload['event']} -> {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (HTTP {resp.status})")
    except Exception as e:
        log_event("ERROR", f"Lucid order failed ({order_payload['action']} {order_payload['quantity']} {order_payload['symbol']}): {e}")

def dispatch_lucid_order(order_payload: dict, lucid_cfg: dict):
    """Record order in history and fire non-blocking HTTP request in background thread."""
    with lucid_lock:
        lucid_dispatched_orders.append(order_payload)
    t = threading.Thread(target=_execute_lucid_http, args=(order_payload, lucid_cfg), daemon=True)
    t.start()

def process_position_snapshot(last_positions: dict, positions: list):
    """Compare previous and current Vantage position snapshots, dispatch commands to FTMO and Lucid Trading, and return (current_positions, commands_sent)."""
    current_positions = {p["ticket"]: p for p in positions}
    commands_sent = []
    lucid_cfg = load_lucid_data()
    lucid_enabled = bool(lucid_cfg.get("enabled", False))

    # 1. Detect open positions & partial closes
    for ticket, pos in current_positions.items():
        if ticket not in last_positions:
            target_symbol = map_symbol(pos['symbol'])
            vol = calculate_target_lot(target_symbol, pos["volume"], src_symbol=pos['symbol'])
            # Stealth SL/TP (Mode A): always pass 0;0 to avoid cross-broker quote mismatch
            cmd = f"OPEN;{target_symbol};{pos['type']};{vol};0;0;{ticket}"
            log_event("TRADE", f"Open detected: {pos['symbol']} -> {target_symbol} vol={vol} (ticket #{ticket})")
            send_to_ftmo(cmd)
            commands_sent.append(cmd)

            # Parallel Lucid Trading Whitelist Routing
            if lucid_enabled:
                lucid_res = calculate_lucid_order(pos["symbol"], pos["volume"], lucid_cfg)
                if lucid_res:
                    lucid_sym, contracts = lucid_res
                    entry_action = "Buy" if int(pos["type"]) == 0 else "Sell"
                    with lucid_lock:
                        lucid_open_tickets[ticket] = {
                            "src_symbol": pos["symbol"],
                            "target_symbol": lucid_sym,
                            "action": entry_action,
                            "contracts": contracts
                        }
                    dispatch_lucid_order({
                        "event": "OPEN",
                        "ticket": ticket,
                        "src_symbol": pos["symbol"],
                        "symbol": lucid_sym,
                        "action": entry_action,
                        "quantity": contracts
                    }, lucid_cfg)
        else:
            old_vol = float(last_positions[ticket]["volume"])
            new_vol = float(pos["volume"])
            if old_vol > 0 and new_vol < old_vol - 1e-6:
                target_symbol = map_symbol(pos['symbol'])
                ratio = round((old_vol - new_vol) / old_vol, 6)
                cmd = f"PARTIAL;{target_symbol};{ratio};{ticket}"
                log_event(
                    "TRADE",
                    f"Partial close detected: {pos['symbol']} -> {target_symbol} "
                    f"({old_vol:.2f} -> {new_vol:.2f}, -{ratio*100:.1f}%, ticket #{ticket})"
                )
                send_to_ftmo(cmd)
                commands_sent.append(cmd)

                # Parallel Lucid Trading Partial Close (Integer Contracts)
                if lucid_enabled:
                    order_to_send = None
                    with lucid_lock:
                        if ticket in lucid_open_tickets:
                            state = lucid_open_tickets[ticket]
                            cur_c = int(state["contracts"])
                            close_qty = int(round(cur_c * ratio))
                            if cur_c > 1 and 1 <= close_qty < cur_c:
                                close_action = "Sell" if state["action"] == "Buy" else "Buy"
                                state["contracts"] = cur_c - close_qty
                                order_to_send = {
                                    "event": "PARTIAL",
                                    "ticket": ticket,
                                    "src_symbol": state["src_symbol"],
                                    "symbol": state["target_symbol"],
                                    "action": close_action,
                                    "quantity": close_qty
                                }
                    if order_to_send:
                        dispatch_lucid_order(order_to_send, lucid_cfg)

    # 2. Detect closed positions
    for ticket, pos in last_positions.items():
        if ticket not in current_positions:
            target_symbol = map_symbol(pos['symbol'])
            cmd = f"CLOSE;{target_symbol};{ticket}"
            log_event("TRADE", f"Close detected: {pos['symbol']} -> {target_symbol} (ticket #{ticket})")
            send_to_ftmo(cmd)
            commands_sent.append(cmd)

            # Parallel Lucid Trading Full Exit (Remaining Integer Contracts)
            order_to_send = None
            with lucid_lock:
                if ticket in lucid_open_tickets:
                    state = lucid_open_tickets.pop(ticket)
                    rem_c = int(state["contracts"])
                    if rem_c >= 1 and lucid_enabled:
                        close_action = "Sell" if state["action"] == "Buy" else "Buy"
                        order_to_send = {
                            "event": "CLOSE",
                            "ticket": ticket,
                            "src_symbol": state["src_symbol"],
                            "symbol": state["target_symbol"],
                            "action": close_action,
                            "quantity": rem_c
                        }
            if order_to_send:
                dispatch_lucid_order(order_to_send, lucid_cfg)

    return current_positions, commands_sent

def handle_vantage_client(conn, addr):
    with status_lock:
        vantage_status["connected"] = True
        vantage_status["addr"] = f"{addr[0]}:{addr[1]}"
        vantage_status["connected_at"] = time.time()
        vantage_status["last_msg_at"] = time.time()
    log_event("CONNECTED", f"Vantage Watcher from {addr}")

    last_positions = {}
    buffer = ""
    
    with conn:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            with status_lock:
                vantage_status["last_msg_at"] = time.time()

            buffer += data.decode('utf-8', errors='ignore')
            
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    positions = json.loads(line)
                    last_positions, _ = process_position_snapshot(last_positions, positions)
                except Exception as e:
                    log_event("ERROR", f"Parsing Vantage data: {e} | Raw: {line}")
                    
    with status_lock:
        vantage_status["connected"] = False
        vantage_status["addr"] = None
    log_event("DISCONNECTED", "Vantage Watcher disconnected")

def handle_ftmo_client(conn, addr):
    global ftmo_client_socket
    with ftmo_lock:
        ftmo_client_socket = conn
    with status_lock:
        ftmo_status["connected"] = True
        ftmo_status["addr"] = f"{addr[0]}:{addr[1]}"
        ftmo_status["connected_at"] = time.time()
        ftmo_status["last_msg_at"] = time.time()
    log_event("CONNECTED", f"FTMO Executor from {addr}")
        
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            with status_lock:
                ftmo_status["last_msg_at"] = time.time()
            msg = data.decode('utf-8', errors='ignore').strip()
            log_event("FROM FTMO", msg)
    except:
        pass
    finally:
        with ftmo_lock:
            if ftmo_client_socket == conn:
                ftmo_client_socket = None
        with status_lock:
            ftmo_status["connected"] = False
            ftmo_status["addr"] = None
        log_event("DISCONNECTED", "FTMO Executor disconnected")

def start_server(port, handler):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, port))
    server.listen(5)
    log_event("LISTENING", f"TCP Server running on port {port}")
    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handler, args=(conn, addr), daemon=True)
        t.start()

def get_default_presets():
    """Return initial default broker pairing presets seeded from current configs."""
    with config_lock:
        cur_sym = dict(SYMBOL_MAP) if SYMBOL_MAP else {
            "NAS100.r": "US100.cash",
            "XAUUSD247": "XAUUSD",
            "XAUUSD.tv": "XAUUSD",
            "US30.r": "US30.cash",
            "GER40.r": "GER40.cash"
        }
        cur_lot = json.loads(json.dumps(LOT_RULES)) if LOT_RULES else {
            "default_multiplier": 1.0,
            "min_lot": 0.01,
            "rules": {}
        }

    return {
        "active_profile": "Vantage -> FTMO",
        "presets": {
            "Vantage -> FTMO": {
                "description": "Vantage Master to FTMO Slave",
                "symbol_map": json.loads(json.dumps(cur_sym)),
                "lot_rules": json.loads(json.dumps(cur_lot))
            },
            "Vantage -> FundedNext": {
                "description": "Vantage Master to FundedNext Slave",
                "symbol_map": {
                    "NAS100.r": "NDX100",
                    "NAS100": "NDX100",
                    "XAUUSD247": "XAUUSD",
                    "XAUUSD.tv": "XAUUSD",
                    "US30.r": "US30",
                    "GER40.r": "GER40"
                },
                "lot_rules": json.loads(json.dumps(cur_lot))
            },
            "Exness -> FTMO": {
                "description": "Exness Master to FTMO Slave",
                "symbol_map": {
                    "USTECm": "US100.cash",
                    "USTEC": "US100.cash",
                    "XAUUSDm": "XAUUSD",
                    "US30m": "US30.cash",
                    "DE30m": "GER40.cash"
                },
                "lot_rules": {
                    "default_multiplier": 1.0,
                    "min_lot": 0.01,
                    "rules": {
                        "XAUUSD": {"fixed_lot": 2.0},
                        "US100.cash": {"multiplier": 1.0}
                    }
                }
            },
            "Exness -> FundedNext": {
                "description": "Exness Master to FundedNext Slave",
                "symbol_map": {
                    "USTECm": "NDX100",
                    "USTEC": "NDX100",
                    "XAUUSDm": "XAUUSD",
                    "US30m": "US30",
                    "DE30m": "GER40"
                },
                "lot_rules": {
                    "default_multiplier": 1.0,
                    "min_lot": 0.01,
                    "rules": {
                        "XAUUSD": {"fixed_lot": 2.0},
                        "NDX100": {"multiplier": 1.0}
                    }
                }
            }
        }
    }

def load_presets_data() -> dict:
    """Load presets from profiles/presets.json, seeding from presets.example.json or defaults if missing."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "presets" in data:
                    return data
        except Exception as e:
            log_event("ERROR", f"Failed to load {PRESETS_FILE}: {e}")
    if os.path.exists(PRESETS_EXAMPLE_FILE):
        try:
            with open(PRESETS_EXAMPLE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "presets" in data:
                    save_atomic_json(PRESETS_FILE, data, "presets")
                    return data
        except Exception as e:
            log_event("ERROR", f"Failed to load {PRESETS_EXAMPLE_FILE}: {e}")
    default_data = get_default_presets()
    save_atomic_json(PRESETS_FILE, default_data, "presets")
    return default_data

def activate_profile_preset(profile_name: str):
    """Apply a named preset to symbol_map.json and lot_rules.json and hot-reload into memory."""
    data = load_presets_data()
    presets = data.get("presets", {})
    if profile_name not in presets:
        return False, f"Profile '{profile_name}' not found"

    preset = presets[profile_name]
    sym_map = preset.get("symbol_map", {})
    lot_rules = preset.get("lot_rules", {})

    valid_s, err_s = validate_symbol_map(sym_map)
    if not valid_s:
        return False, f"Invalid symbol_map in profile: {err_s}"
    valid_l, err_l = validate_lot_rules(lot_rules)
    if not valid_l:
        return False, f"Invalid lot_rules in profile: {err_l}"

    save_atomic_json(SYMBOL_MAP_FILE, sym_map, "symbol_map")
    save_atomic_json(LOT_RULES_FILE, lot_rules, "lot_rules")
    load_configs()

    data["active_profile"] = profile_name
    save_atomic_json(PRESETS_FILE, data, "presets")
    log_event("PROFILE", f"Activated profile preset '{profile_name}' ({len(sym_map)} symbols)")
    return True, ""

def load_accounts_data() -> dict:
    """Load Master and Slave accounts from profiles/accounts.json."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("active_master_id", "")
                    data.setdefault("active_slave_id", "")
                    data.setdefault("master_accounts", [])
                    data.setdefault("slave_accounts", [])
                    return data
        except Exception as e:
            log_event("ERROR", f"Failed to load {ACCOUNTS_FILE}: {e}")
    default_acc = {
        "active_master_id": "",
        "active_slave_id": "",
        "master_accounts": [],
        "slave_accounts": []
    }
    return default_acc

def mask_accounts_data(data: dict) -> dict:
    """Return a copy of accounts data with passwords masked for safe UI display."""
    masked = {
        "active_master_id": data.get("active_master_id", ""),
        "active_slave_id": data.get("active_slave_id", ""),
        "master_accounts": [],
        "slave_accounts": []
    }
    for key in ("master_accounts", "slave_accounts"):
        for acc in data.get(key, []):
            item = dict(acc)
            has_pwd = bool(item.get("password"))
            item["password"] = "••••••••" if has_pwd else ""
            item["has_password"] = has_pwd
            masked[key].append(item)
    return masked

def generate_mt5_ini(server: str, login: str, password: str, expert: str, startup_symbol: str = "XAUUSD") -> str:
    """Generate MT5 startup configuration (mt5-server.ini) content."""
    symbol = (startup_symbol or "XAUUSD").strip()
    return (
        "[Common]\n"
        f"Server={server.strip()}\n"
        f"Login={str(login).strip()}\n"
        f"Password={password}\n"
        "News=0\n"
        "Experts=1\n"
        "\n"
        "[Experts]\n"
        "Enabled=1\n"
        "AllowLiveTrading=1\n"
        "AllowDllImport=1\n"
        "\n"
        "[StartUp]\n"
        f"Expert={expert}\n"
        f"Symbol={symbol}\n"
        "Period=M1\n"
        "EnableTrading=1\n"
    )

def activate_mt5_account(role: str, account_id: str, restart_container: bool = True):
    """Switch active Master or Slave MT5 account, write mt5-server.ini, apply linked profile, and optionally restart container."""
    if role not in ("master", "slave"):
        return False, {"error": "Role must be 'master' or 'slave'"}

    acc_data = load_accounts_data()
    list_key = "master_accounts" if role == "master" else "slave_accounts"
    target_acc = None
    for acc in acc_data.get(list_key, []):
        if acc.get("id") == account_id:
            target_acc = acc
            break

    if not target_acc:
        return False, {"error": f"Account '{account_id}' not found in {list_key}"}

    # 1. If account has a linked profile preset, activate it automatically
    linked_profile = (target_acc.get("linked_profile") or "").strip()
    profile_activated = None
    if linked_profile:
        ok, err = activate_profile_preset(linked_profile)
        if ok:
            profile_activated = linked_profile
        else:
            log_event("WARN", f"Could not activate linked profile '{linked_profile}': {err}")

    # 2. Write mt5-server.ini atomically
    ini_path = MASTER_INI_FILE if role == "master" else SLAVE_INI_FILE
    expert_name = "VantageSender" if role == "master" else "FTMOReceiver"
    container_name = MASTER_CONTAINER if role == "master" else SLAVE_CONTAINER

    os.makedirs(os.path.dirname(os.path.abspath(ini_path)), exist_ok=True)
    ini_content = generate_mt5_ini(
        server=target_acc.get("server", ""),
        login=target_acc.get("login", ""),
        password=target_acc.get("password", ""),
        expert=expert_name,
        startup_symbol=target_acc.get("startup_symbol", "XAUUSD")
    )
    tmp_ini = ini_path + ".tmp"
    with open(tmp_ini, "w", encoding="utf-8") as f:
        f.write(ini_content)
    os.replace(tmp_ini, ini_path)

    # 3. Update active account ID
    if role == "master":
        acc_data["active_master_id"] = account_id
    else:
        acc_data["active_slave_id"] = account_id
    save_atomic_json(ACCOUNTS_FILE, acc_data, "accounts")

    log_event(
        "ACCOUNT",
        f"Switched {role.upper()} account to '{target_acc.get('name')}' "
        f"({target_acc.get('server')} / {target_acc.get('login')}) -> updated {os.path.relpath(ini_path, BASE_DIR)}"
    )

    # 4. Optionally restart Docker container
    container_restarted = False
    docker_msg = "Container restart skipped"
    if restart_container:
        try:
            proc = subprocess.run(
                ["docker", "restart", container_name],
                capture_output=True,
                text=True,
                timeout=20
            )
            if proc.returncode == 0:
                container_restarted = True
                docker_msg = f"Restarted container '{container_name}'"
                log_event("ACCOUNT", f"Docker container '{container_name}' restarted successfully")
            else:
                docker_msg = (proc.stderr or proc.stdout or f"Exit code {proc.returncode}").strip()
                log_event("WARN", f"Docker restart '{container_name}' returned non-zero: {docker_msg}")
        except Exception as e:
            docker_msg = str(e)
            log_event("WARN", f"Docker restart '{container_name}' skipped/failed: {e}")

    return True, {
        "success": True,
        "role": role,
        "active_id": account_id,
        "account_name": target_acc.get("name"),
        "profile_activated": profile_activated,
        "ini_updated": True,
        "container": container_name,
        "container_restarted": container_restarted,
        "docker_msg": docker_msg
    }

# Lightweight VPS System Load Monitor (Zero-Dependency, 5s Cached)
_metrics_lock = threading.Lock()
_metrics_cache_time = 0.0
_metrics_cache_data = None
_prev_cpu_times = None
_PROCESS_START_TIME = time.time()
SYSTEM_METRICS_TTL = 5.0

def _format_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

def _read_linux_cpu_times():
    """Read aggregate CPU jiffies from /proc/stat -> (idle_all, total)."""
    with open("/proc/stat", "r", encoding="utf-8") as f:
        first = f.readline()
    parts = first.split()
    if not parts or parts[0] != "cpu":
        return None
    values = [int(x) for x in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return idle, total

def get_vps_metrics(force: bool = False) -> dict:
    """Return cached host CPU, RAM, Swap, Disk, and Uptime metrics without spawning subprocesses."""
    global _metrics_cache_time, _metrics_cache_data, _prev_cpu_times
    now = time.time()
    with _metrics_lock:
        if not force and _metrics_cache_data is not None and (now - _metrics_cache_time) < SYSTEM_METRICS_TTL:
            return _metrics_cache_data

        cpu_cores = os.cpu_count() or 1
        try:
            load_1m, load_5m, _ = os.getloadavg()
        except Exception:
            load_1m, load_5m = 0.0, 0.0

        # 1. CPU %
        cpu_percent = min(100.0, round((load_1m / max(1, cpu_cores)) * 100.0, 1))
        if os.path.exists("/proc/stat"):
            try:
                cur_cpu = _read_linux_cpu_times()
                if cur_cpu and _prev_cpu_times:
                    idle_delta = cur_cpu[0] - _prev_cpu_times[0]
                    total_delta = cur_cpu[1] - _prev_cpu_times[1]
                    if total_delta > 0:
                        cpu_percent = round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)
                _prev_cpu_times = cur_cpu
            except Exception:
                pass

        # 2. RAM & Swap from /proc/meminfo
        ram_total_gb = 0.0
        ram_used_gb = 0.0
        ram_percent = 0.0
        swap_total_gb = 0.0
        swap_used_gb = 0.0
        swap_percent = 0.0

        if os.path.exists("/proc/meminfo"):
            try:
                meminfo = {}
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    for line in f:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            val_parts = v.strip().split()
                            if val_parts and val_parts[0].isdigit():
                                meminfo[k.strip()] = int(val_parts[0]) # kB
                mem_total_kb = meminfo.get("MemTotal", 0)
                mem_avail_kb = meminfo.get(
                    "MemAvailable",
                    meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)
                )
                if mem_total_kb > 0:
                    mem_used_kb = max(0, mem_total_kb - mem_avail_kb)
                    ram_total_gb = round(mem_total_kb / (1024 * 1024), 2)
                    ram_used_gb = round(mem_used_kb / (1024 * 1024), 2)
                    ram_percent = round((mem_used_kb / mem_total_kb) * 100.0, 1)

                swap_total_kb = meminfo.get("SwapTotal", 0)
                swap_free_kb = meminfo.get("SwapFree", 0)
                if swap_total_kb > 0:
                    swap_used_kb = max(0, swap_total_kb - swap_free_kb)
                    swap_total_gb = round(swap_total_kb / (1024 * 1024), 2)
                    swap_used_gb = round(swap_used_kb / (1024 * 1024), 2)
                    swap_percent = round((swap_used_kb / swap_total_kb) * 100.0, 1)
            except Exception:
                pass

        # 3. Disk Usage (/)
        disk_total_gb = 0.0
        disk_used_gb = 0.0
        disk_percent = 0.0
        try:
            du = shutil.disk_usage("/")
            if du.total > 0:
                disk_total_gb = round(du.total / (1024 ** 3), 1)
                disk_used_gb = round(du.used / (1024 ** 3), 1)
                disk_percent = round((du.used / du.total) * 100.0, 1)
        except Exception:
            pass

        # 4. System Uptime
        uptime_secs = now - _PROCESS_START_TIME
        if os.path.exists("/proc/uptime"):
            try:
                with open("/proc/uptime", "r", encoding="utf-8") as f:
                    uptime_secs = float(f.readline().split()[0])
            except Exception:
                pass

        _metrics_cache_data = {
            "cpu_percent": cpu_percent,
            "cpu_cores": cpu_cores,
            "load_1m": round(load_1m, 2),
            "load_5m": round(load_5m, 2),
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "ram_percent": ram_percent,
            "swap_used_gb": swap_used_gb,
            "swap_total_gb": swap_total_gb,
            "swap_percent": swap_percent,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "disk_percent": disk_percent,
            "uptime_str": _format_uptime(uptime_secs)
        }
        _metrics_cache_time = now
        return _metrics_cache_data

# Web Server & REST API Handlers
class WebRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default stdout log spam from http.server
        return

    def check_auth(self):
        auth_header = self.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="MT5 Copier Dashboard"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authentication required")
            return False

        try:
            encoded = auth_header.split(" ", 1)[1]
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)
            expected_user, expected_pass = get_auth_credentials()
            if username == expected_user and password == expected_pass:
                return True
        except Exception:
            pass

        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="MT5 Copier Dashboard"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Invalid credentials")
        return False

    def send_json(self, status_code: int, data):
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not self.check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        static_files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "application/javascript; charset=utf-8"),
        }
        if path in static_files:
            filename, content_type = static_files[path]
            file_path = os.path.join(WEB_STATIC_DIR, filename)
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(f"web/{filename} not found".encode("utf-8"))
            return

        if path == "/api/status":
            with status_lock:
                v_copy = dict(vantage_status)
                f_copy = dict(ftmo_status)
            with log_lock:
                logs_copy = list(event_logs)

            presets_info = load_presets_data()
            acc_info = load_accounts_data()
            active_master_name = next(
                (a.get("name") for a in acc_info.get("master_accounts", []) if a.get("id") == acc_info.get("active_master_id")),
                None
            )
            active_slave_name = next(
                (a.get("name") for a in acc_info.get("slave_accounts", []) if a.get("id") == acc_info.get("active_slave_id")),
                None
            )

            lucid_info = load_lucid_data()
            with lucid_lock:
                lucid_open_count = len(lucid_open_tickets)

            data = {
                "version": VERSION,
                "hub_running": True,
                "active_profile": presets_info.get("active_profile", ""),
                "active_master_name": active_master_name,
                "active_slave_name": active_slave_name,
                "vantage": v_copy,
                "ftmo": f_copy,
                "lucid": {
                    "enabled": bool(lucid_info.get("enabled", False)),
                    "mode": lucid_info.get("mode", "webhook"),
                    "whitelist_count": len(lucid_info.get("whitelist_rules", {})),
                    "open_positions": lucid_open_count
                },
                "vps": get_vps_metrics(),
                "loaded_at": last_config_load_time,
                "recent_logs": logs_copy
            }
            self.send_json(200, data)
            return

        if path == "/api/symbols":
            with config_lock:
                symbols_copy = dict(SYMBOL_MAP)
            self.send_json(200, symbols_copy)
            return

        if path == "/api/lot-rules":
            with config_lock:
                rules_copy = dict(LOT_RULES)
            self.send_json(200, rules_copy)
            return

        if path == "/api/profiles":
            self.send_json(200, load_presets_data())
            return

        if path == "/api/accounts":
            self.send_json(200, mask_accounts_data(load_accounts_data()))
            return

        if path == "/api/lucid":
            self.send_json(200, mask_lucid_data(load_lucid_data()))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self.check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        post_bytes = self.rfile.read(content_length)

        try:
            req_data = json.loads(post_bytes.decode("utf-8")) if post_bytes else {}
        except Exception as e:
            self.send_json(400, {"success": False, "error": f"Malformed JSON: {e}"})
            return

        if path == "/api/symbols":
            valid, err = validate_symbol_map(req_data)
            if not valid:
                self.send_json(400, {"success": False, "error": err})
                return
            try:
                save_atomic_json(SYMBOL_MAP_FILE, req_data, "symbol_map")
                load_configs()
                log_event("CONFIG", f"Updated symbol mappings via Web UI ({len(req_data)} mappings)")
                self.send_json(200, {"success": True, "count": len(req_data)})
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e)})
            return

        if path == "/api/lot-rules":
            valid, err = validate_lot_rules(req_data)
            if not valid:
                self.send_json(400, {"success": False, "error": err})
                return
            try:
                save_atomic_json(LOT_RULES_FILE, req_data, "lot_rules")
                load_configs()
                log_event("CONFIG", "Updated lot sizing rules via Web UI")
                self.send_json(200, {"success": True})
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e)})
            return

        if path == "/api/profiles":
            action = req_data.get("action", "")
            name = (req_data.get("name") or "").strip()
            if not name:
                self.send_json(400, {"success": False, "error": "Profile name is required"})
                return

            data = load_presets_data()
            presets = data.setdefault("presets", {})

            if action == "save":
                sym_map = req_data.get("symbol_map")
                lot_rules = req_data.get("lot_rules")
                if sym_map is None:
                    with config_lock:
                        sym_map = dict(SYMBOL_MAP)
                if lot_rules is None:
                    with config_lock:
                        lot_rules = json.loads(json.dumps(LOT_RULES))

                valid_s, err_s = validate_symbol_map(sym_map)
                if not valid_s:
                    self.send_json(400, {"success": False, "error": err_s})
                    return
                valid_l, err_l = validate_lot_rules(lot_rules)
                if not valid_l:
                    self.send_json(400, {"success": False, "error": err_l})
                    return

                presets[name] = {
                    "description": req_data.get("description", f"Saved preset: {name}"),
                    "symbol_map": sym_map,
                    "lot_rules": lot_rules
                }
                data["active_profile"] = name
                save_atomic_json(PRESETS_FILE, data, "presets")
                log_event("PROFILE", f"Saved profile preset '{name}'")
                self.send_json(200, {"success": True, "active_profile": name, "profiles": data})
                return

            if action == "activate":
                ok, err = activate_profile_preset(name)
                if not ok:
                    self.send_json(400, {"success": False, "error": err})
                    return
                self.send_json(200, {"success": True, "active_profile": name, "profiles": load_presets_data()})
                return

            if action == "delete":
                if name not in presets:
                    self.send_json(404, {"success": False, "error": f"Profile '{name}' not found"})
                    return
                del presets[name]
                if data.get("active_profile") == name:
                    data["active_profile"] = next(iter(presets.keys()), "")
                save_atomic_json(PRESETS_FILE, data, "presets")
                log_event("PROFILE", f"Deleted profile preset '{name}'")
                self.send_json(200, {"success": True, "profiles": data})
                return

            self.send_json(400, {"success": False, "error": f"Unknown profile action: {action}"})
            return

        if path == "/api/accounts":
            action = req_data.get("action", "")
            role = req_data.get("role", "")
            if role not in ("master", "slave"):
                self.send_json(400, {"success": False, "error": "Role must be 'master' or 'slave'"})
                return

            acc_data = load_accounts_data()
            list_key = "master_accounts" if role == "master" else "slave_accounts"
            acc_list = acc_data.setdefault(list_key, [])

            if action == "save":
                acc_obj = req_data.get("account", {})
                acc_id = (acc_obj.get("id") or "").strip() or f"{role}_{int(time.time() * 1000)}"
                name = (acc_obj.get("name") or "").strip()
                server = (acc_obj.get("server") or "").strip()
                login = str(acc_obj.get("login") or "").strip()
                raw_pwd = acc_obj.get("password", "")
                startup_symbol = (acc_obj.get("startup_symbol") or "XAUUSD").strip()
                linked_profile = (acc_obj.get("linked_profile") or "").strip()
                broker = (acc_obj.get("broker") or "").strip()

                if not name or not server or not login:
                    self.send_json(400, {"success": False, "error": "Name, Server, and Login are required"})
                    return

                existing_idx = next((i for i, a in enumerate(acc_list) if a.get("id") == acc_id), None)
                if existing_idx is not None:
                    old_pwd = acc_list[existing_idx].get("password", "")
                    final_pwd = old_pwd if (raw_pwd in ("", "••••••••")) else raw_pwd
                else:
                    final_pwd = "" if raw_pwd == "••••••••" else raw_pwd

                record = {
                    "id": acc_id,
                    "name": name,
                    "broker": broker,
                    "server": server,
                    "login": login,
                    "password": final_pwd,
                    "startup_symbol": startup_symbol,
                    "linked_profile": linked_profile
                }
                if existing_idx is not None:
                    acc_list[existing_idx] = record
                else:
                    acc_list.append(record)

                save_atomic_json(ACCOUNTS_FILE, acc_data, "accounts")
                log_event("ACCOUNT", f"Saved {role} account '{name}' ({server} / {login})")
                self.send_json(200, {"success": True, "accounts": mask_accounts_data(acc_data)})
                return

            if action == "delete":
                acc_id = (req_data.get("id") or "").strip()
                acc_data[list_key] = [a for a in acc_list if a.get("id") != acc_id]
                active_key = "active_master_id" if role == "master" else "active_slave_id"
                if acc_data.get(active_key) == acc_id:
                    acc_data[active_key] = ""
                save_atomic_json(ACCOUNTS_FILE, acc_data, "accounts")
                log_event("ACCOUNT", f"Deleted {role} account id '{acc_id}'")
                self.send_json(200, {"success": True, "accounts": mask_accounts_data(acc_data)})
                return

            if action == "activate":
                acc_id = (req_data.get("id") or "").strip()
                restart = bool(req_data.get("restart_container", True))
                ok, res = activate_mt5_account(role, acc_id, restart_container=restart)
                if not ok:
                    self.send_json(400, {"success": False, **res})
                    return
                res["accounts"] = mask_accounts_data(load_accounts_data())
                res["profiles"] = load_presets_data()
                self.send_json(200, res)
                return

            self.send_json(400, {"success": False, "error": f"Unknown account action: {action}"})
            return

        if path == "/api/lucid":
            cur_lucid = load_lucid_data()
            cur_tv = cur_lucid.get("tradovate", {})

            enabled = bool(req_data.get("enabled", cur_lucid.get("enabled", False)))
            mode = req_data.get("mode", cur_lucid.get("mode", "webhook"))
            if mode not in ("webhook", "tradovate"):
                self.send_json(400, {"success": False, "error": "Mode must be 'webhook' or 'tradovate'"})
                return

            webhook_url = (req_data.get("webhook_url") if "webhook_url" in req_data else cur_lucid.get("webhook_url", "")).strip()
            raw_wh_secret = req_data.get("webhook_secret", "••••••••")
            webhook_secret = cur_lucid.get("webhook_secret", "") if raw_wh_secret == "••••••••" else raw_wh_secret

            req_tv = req_data.get("tradovate", {})
            tv_pwd = req_tv.get("password", "••••••••")
            tv_token = req_tv.get("access_token", "••••••••")
            tv_sec = req_tv.get("sec", "••••••••")

            new_tv = {
                "env": req_tv.get("env", cur_tv.get("env", "demo")),
                "username": (req_tv.get("username") if "username" in req_tv else cur_tv.get("username", "")).strip(),
                "password": cur_tv.get("password", "") if tv_pwd == "••••••••" else tv_pwd,
                "account_spec": (req_tv.get("account_spec") if "account_spec" in req_tv else cur_tv.get("account_spec", "")).strip(),
                "account_id": int(req_tv.get("account_id") if "account_id" in req_tv else cur_tv.get("account_id", 0) or 0),
                "access_token": cur_tv.get("access_token", "") if tv_token == "••••••••" else tv_token,
                "cid": int(req_tv.get("cid") if "cid" in req_tv else cur_tv.get("cid", 0) or 0),
                "sec": cur_tv.get("sec", "") if tv_sec == "••••••••" else tv_sec
            }

            raw_rules = req_data.get("whitelist_rules", cur_lucid.get("whitelist_rules", {}))
            if not isinstance(raw_rules, dict):
                self.send_json(400, {"success": False, "error": "whitelist_rules must be a JSON object"})
                return

            cleaned_rules = {}
            for src_sym, r in raw_rules.items():
                src_key = str(src_sym).strip()
                if not src_key or not isinstance(r, dict):
                    continue
                tgt_sym = str(r.get("target_symbol", "")).strip()
                if not tgt_sym:
                    self.send_json(400, {"success": False, "error": f"Target futures contract is required for '{src_key}'"})
                    return
                s_mode = r.get("sizing_mode", "multiplier")
                if s_mode not in ("multiplier", "fixed"):
                    s_mode = "multiplier"
                mult = max(0.01, float(r.get("multiplier", 1.0) or 1.0))
                fixed_c = max(1, int(r.get("fixed_contracts", 1) or 1))
                min_c = max(1, int(r.get("min_contracts", 1) or 1))
                max_c = max(min_c, int(r.get("max_contracts", 20) or 20))
                cleaned_rules[src_key] = {
                    "target_symbol": tgt_sym,
                    "sizing_mode": s_mode,
                    "multiplier": mult,
                    "fixed_contracts": fixed_c,
                    "min_contracts": min_c,
                    "max_contracts": max_c
                }

            updated_lucid = {
                "enabled": enabled,
                "mode": mode,
                "webhook_url": webhook_url,
                "webhook_secret": webhook_secret,
                "tradovate": new_tv,
                "whitelist_rules": cleaned_rules
            }
            save_atomic_json(LUCID_FILE, updated_lucid, "lucid")
            log_event("LUCID", f"Updated Lucid Futures config (enabled={enabled}, mode={mode}, whitelist={len(cleaned_rules)} symbols)")
            self.send_json(200, {"success": True, "lucid": mask_lucid_data(updated_lucid)})
            return

        if path == "/api/reload":
            try:
                load_configs()
                log_event("CONFIG", "Manual configuration reload triggered via Web UI")
                self.send_json(200, {"success": True, "loaded_at": last_config_load_time})
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e)})
            return

        if path == "/api/change-password":
            user = req_data.get("username", "").strip()
            pwd = req_data.get("password", "").strip()
            if not user or not pwd or len(pwd) < 4:
                self.send_json(400, {"success": False, "error": "Username and password (min length 4) are required"})
                return
            try:
                set_auth_credentials(user, pwd)
                log_event("SECURITY", f"Dashboard credentials updated for user '{user}'")
                self.send_json(200, {"success": True})
            except Exception as e:
                self.send_json(500, {"success": False, "error": str(e)})
            return

        self.send_response(404)
        self.end_headers()

def start_web_server(host: str, port: int):
    try:
        httpd = ThreadingHTTPServer((host, port), WebRequestHandler)
        log_event("WEB", f"Dashboard running at http://{host}:{port}/")
        httpd.serve_forever()
    except Exception as e:
        log_event("ERROR", f"Failed to start Web Dashboard on {host}:{port}: {e}")

if __name__ == "__main__":
    t_vantage = threading.Thread(target=start_server, args=(VANTAGE_PORT, handle_vantage_client), daemon=True)
    t_ftmo = threading.Thread(target=start_server, args=(FTMO_PORT, handle_ftmo_client), daemon=True)
    t_web = threading.Thread(target=start_web_server, args=(WEB_HOST, WEB_PORT), daemon=True)
    
    t_vantage.start()
    t_ftmo.start()
    t_web.start()
    
    log_event("SYSTEM", f"All services online. Web Dashboard on {WEB_HOST}:{WEB_PORT}. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
