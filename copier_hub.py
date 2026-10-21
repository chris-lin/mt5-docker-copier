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

from vps_monitor import get_vps_metrics
from futures_copier import (
    lucid_lock,
    lucid_open_tickets,
    target_open_tickets,
    lucid_dispatched_orders,
    futures_dispatched_orders,
    external_master_lock,
    external_master_positions,
    reset_futures_runtime_state,
    get_default_futures_config,
    normalize_futures_config,
    mask_futures_data,
    get_enabled_targets,
    calculate_futures_order,
    resolve_reverse_futures_to_mt5,
    dispatch_futures_order,
    apply_master_webhook_signal,
    start_tradovate_master_poller,
)

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
FUTURES_FILE = os.path.join(CONFIG_DIR, "futures_copier.json")
FUTURES_EXAMPLE_FILE = os.path.join(TEMPLATE_DIR, "futures_copier.example.json")
LUCID_FILE = FUTURES_FILE  # Backward-compatible alias
LUCID_EXAMPLE_FILE = FUTURES_EXAMPLE_FILE

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
_migrate_or_seed_config(
    FUTURES_FILE,
    [os.path.join(CONFIG_DIR, "lucid.json"), os.path.join(BASE_DIR, "profiles", "lucid.json")],
    FUTURES_EXAMPLE_FILE
)

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


load_configs()


def backup_file(file_path: str, prefix: str):
    """Create a timestamped backup of the configuration file and keep the 10 most recent."""
    if not os.path.exists(file_path):
        return
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_filename = f"{prefix}_{timestamp}.json"
    bak_path = os.path.join(BACKUP_DIR, bak_filename)
    try:
        shutil.copy2(file_path, bak_path)
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

        rule = (rules.get(src_symbol) if src_symbol else None) or rules.get(target_symbol)

        if rule:
            if "fixed_lot" in rule:
                return round(float(rule["fixed_lot"]), 2)
            if "lot_map" in rule:
                str_lot = str(round(src_lot, 2))
                if str_lot in rule["lot_map"]:
                    return round(float(rule["lot_map"][str_lot]), 2)
                if src_lot in rule["lot_map"]:
                    return round(float(rule["lot_map"][src_lot]), 2)
            if "multiplier" in rule:
                return max(min_lot, round(src_lot * float(rule["multiplier"]), 2))
            if "default_multiplier" in rule:
                return max(min_lot, round(src_lot * float(rule["default_multiplier"]), 2))

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


def load_lucid_data() -> dict:
    """Load Futures Copier configuration from LUCID_FILE (.config/futures_copier.json) or seed from template."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    if os.path.exists(LUCID_FILE):
        try:
            with open(LUCID_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "whitelist_rules" in data:
                    return normalize_futures_config(data)
        except Exception as e:
            log_event("ERROR", f"Failed to load {LUCID_FILE}: {e}")
    if os.path.exists(LUCID_EXAMPLE_FILE):
        try:
            with open(LUCID_EXAMPLE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "whitelist_rules" in data:
                    norm = normalize_futures_config(data)
                    save_atomic_json(LUCID_FILE, norm, "futures_copier")
                    return norm
        except Exception as e:
            log_event("ERROR", f"Failed to load {LUCID_EXAMPLE_FILE}: {e}")
    default_cfg = get_default_futures_config()
    save_atomic_json(LUCID_FILE, default_cfg, "futures_copier")
    return default_cfg


load_futures_data = load_lucid_data
mask_lucid_data = mask_futures_data


def calculate_lucid_order(src_symbol: str, src_lot: float, lucid_cfg: dict = None):
    if lucid_cfg is None:
        lucid_cfg = load_lucid_data()
    return calculate_futures_order(src_symbol, src_lot, lucid_cfg, scale=1.0)


def process_position_snapshot(last_positions: dict, positions: list):
    """Compare previous and current Master position snapshots, dispatch commands to FTMO and all enabled Futures Prop Firms."""
    current_positions = {p["ticket"]: p for p in positions}
    commands_sent = []
    futures_cfg = load_lucid_data()
    enabled_targets = get_enabled_targets(futures_cfg)
    futures_enabled = len(enabled_targets) > 0

    with config_lock:
        sym_map_snapshot = dict(SYMBOL_MAP)
        rules_snapshot = dict(LOT_RULES.get("rules", {}))
        min_lot = float(LOT_RULES.get("min_lot", 0.01))
        def_mult = float(LOT_RULES.get("default_multiplier", 1.0))

    # 1. Detect open positions & partial closes
    for ticket, pos in current_positions.items():
        src_sym = pos["symbol"]
        rev_sym, rev_lot, is_rev = resolve_reverse_futures_to_mt5(src_sym, pos["volume"], futures_cfg, sym_map_snapshot)
        target_symbol = rev_sym if is_rev else map_symbol(src_sym)

        if ticket not in last_positions:
            # If Master order is a Futures contract (e.g. MNQZ6) without an explicit LOT_RULES entry, use reverse_map lot_per_contract
            if is_rev and src_sym not in rules_snapshot and target_symbol not in rules_snapshot:
                vol = max(min_lot, round(rev_lot * def_mult, 2))
            else:
                vol = calculate_target_lot(target_symbol, pos["volume"], src_symbol=src_sym)

            # Stealth SL/TP (Mode A): always pass 0;0 to avoid cross-broker quote mismatch
            cmd = f"OPEN;{target_symbol};{pos['type']};{vol};0;0;{ticket}"
            log_event("TRADE", f"Open detected: {src_sym} -> {target_symbol} vol={vol} (ticket #{ticket})")
            send_to_ftmo(cmd)
            commands_sent.append(cmd)

            # Parallel Multi-Target Futures Routing (Lucid Trading, Topstep, Apex, etc.)
            if futures_enabled:
                entry_action = "Buy" if int(pos["type"]) == 0 else "Sell"
                for t_cfg in enabled_targets:
                    t_id = t_cfg.get("id", "lucid_main")
                    scale = float(t_cfg.get("scale", 1.0) or 1.0)
                    fut_res = calculate_futures_order(src_sym, pos["volume"], futures_cfg, scale=scale)
                    if fut_res:
                        fut_sym, contracts = fut_res
                        state_obj = {
                            "src_symbol": src_sym,
                            "target_symbol": fut_sym,
                            "action": entry_action,
                            "contracts": contracts
                        }
                        with lucid_lock:
                            target_open_tickets.setdefault(t_id, {})[ticket] = dict(state_obj)
                            lucid_open_tickets[ticket] = dict(state_obj)
                        dispatch_futures_order({
                            "event": "OPEN",
                            "ticket": ticket,
                            "src_symbol": src_sym,
                            "symbol": fut_sym,
                            "action": entry_action,
                            "quantity": contracts
                        }, t_cfg, log_event)
        else:
            old_vol = float(last_positions[ticket]["volume"])
            new_vol = float(pos["volume"])
            if old_vol > 0 and new_vol < old_vol - 1e-6:
                ratio = round((old_vol - new_vol) / old_vol, 6)
                cmd = f"PARTIAL;{target_symbol};{ratio};{ticket}"
                log_event(
                    "TRADE",
                    f"Partial close detected: {src_sym} -> {target_symbol} "
                    f"({old_vol:.2f} -> {new_vol:.2f}, -{ratio*100:.1f}%, ticket #{ticket})"
                )
                send_to_ftmo(cmd)
                commands_sent.append(cmd)

                # Parallel Multi-Target Futures Partial Close (Integer Contracts)
                if futures_enabled:
                    for t_cfg in enabled_targets:
                        t_id = t_cfg.get("id", "lucid_main")
                        order_to_send = None
                        with lucid_lock:
                            t_map = target_open_tickets.get(t_id, {})
                            state = t_map.get(ticket) or (lucid_open_tickets.get(ticket) if t_id == enabled_targets[0].get("id", "lucid_main") else None)
                            if state:
                                cur_c = int(state["contracts"])
                                close_qty = int(round(cur_c * ratio))
                                if cur_c > 1 and 1 <= close_qty < cur_c:
                                    close_action = "Sell" if state["action"] == "Buy" else "Buy"
                                    state["contracts"] = cur_c - close_qty
                                    if ticket in lucid_open_tickets:
                                        lucid_open_tickets[ticket]["contracts"] = state["contracts"]
                                    order_to_send = {
                                        "event": "PARTIAL",
                                        "ticket": ticket,
                                        "src_symbol": state["src_symbol"],
                                        "symbol": state["target_symbol"],
                                        "action": close_action,
                                        "quantity": close_qty
                                    }
                        if order_to_send:
                            dispatch_futures_order(order_to_send, t_cfg, log_event)

    # 2. Detect closed positions
    for ticket, pos in last_positions.items():
        if ticket not in current_positions:
            src_sym = pos["symbol"]
            rev_sym, _, is_rev = resolve_reverse_futures_to_mt5(src_sym, pos["volume"], futures_cfg, sym_map_snapshot)
            target_symbol = rev_sym if is_rev else map_symbol(src_sym)
            cmd = f"CLOSE;{target_symbol};{ticket}"
            log_event("TRADE", f"Close detected: {src_sym} -> {target_symbol} (ticket #{ticket})")
            send_to_ftmo(cmd)
            commands_sent.append(cmd)

            # Parallel Multi-Target Futures Full Exit (Remaining Integer Contracts)
            for t_cfg in enabled_targets:
                t_id = t_cfg.get("id", "lucid_main")
                order_to_send = None
                with lucid_lock:
                    t_map = target_open_tickets.get(t_id, {})
                    state = t_map.pop(ticket, None)
                    if state is None and t_id == enabled_targets[0].get("id", "lucid_main"):
                        state = lucid_open_tickets.pop(ticket, None)
                    else:
                        lucid_open_tickets.pop(ticket, None)
                    if state:
                        rem_c = int(state["contracts"])
                        if rem_c >= 1 and futures_enabled:
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
                    dispatch_futures_order(order_to_send, t_cfg, log_event)

            # Clean up any disabled target state for closed ticket
            with lucid_lock:
                lucid_open_tickets.pop(ticket, None)
                for t_map in target_open_tickets.values():
                    t_map.pop(ticket, None)

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
    except Exception:
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
            "Pepperstone -> FTMO": {
                "description": "Pepperstone (TradingView / MT5) Master to FTMO Slave",
                "symbol_map": {
                    "NAS100.a": "US100.cash",
                    "NAS100": "US100.cash",
                    "XAUUSD.a": "XAUUSD",
                    "XAUUSD": "XAUUSD",
                    "US30.a": "US30.cash",
                    "GER40.a": "GER40.cash",
                    "BTCUSD.a": "BTCUSD"
                },
                "lot_rules": {
                    "default_multiplier": 1.0,
                    "min_lot": 0.01,
                    "rules": {
                        "XAUUSD": {"multiplier": 1.0},
                        "US100.cash": {"multiplier": 1.0}
                    }
                }
            },
            "Tradovate (Futures) -> FTMO": {
                "description": "TradingView (Tradovate Futures Master) to FTMO Slave",
                "symbol_map": {
                    "MGCZ6": "XAUUSD",
                    "GCZ6": "XAUUSD",
                    "MNQZ6": "US100.cash",
                    "NQZ6": "US100.cash",
                    "MBTZ6": "BTCUSD"
                },
                "lot_rules": {
                    "default_multiplier": 0.1,
                    "min_lot": 0.01,
                    "rules": {
                        "MGCZ6": {"multiplier": 0.1},
                        "GCZ6": {"multiplier": 1.0},
                        "MNQZ6": {"multiplier": 0.1},
                        "NQZ6": {"multiplier": 1.0},
                        "MBTZ6": {"multiplier": 0.1}
                    }
                }
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
    """Load presets from .config/presets.json, seeding from presets.example.json or defaults if missing."""
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
    """Load Master and Slave accounts from .config/accounts.json."""
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
    return {
        "active_master_id": "",
        "active_slave_id": "",
        "master_accounts": [],
        "slave_accounts": []
    }


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
    target_acc = next((a for a in acc_data.get(list_key, []) if a.get("id") == account_id), None)

    if not target_acc:
        return False, {"error": f"Account '{account_id}' not found in {list_key}"}

    linked_profile = (target_acc.get("linked_profile") or "").strip()
    profile_activated = None
    if linked_profile:
        ok, err = activate_profile_preset(linked_profile)
        if ok:
            profile_activated = linked_profile
        else:
            log_event("WARN", f"Could not activate linked profile '{linked_profile}': {err}")

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


def _save_futures_or_lucid_payload(req_data: dict) -> tuple:
    """Validate and persist POST /api/futures or POST /api/lucid payload."""
    cur_cfg = load_lucid_data()
    cur_tv = cur_cfg.get("tradovate", {})

    enabled = bool(req_data.get("enabled", cur_cfg.get("enabled", False)))
    mode = req_data.get("mode", cur_cfg.get("mode", "webhook"))
    if mode not in ("webhook", "tradovate"):
        return False, "Mode must be 'webhook' or 'tradovate'", None

    webhook_url = (req_data.get("webhook_url") if "webhook_url" in req_data else cur_cfg.get("webhook_url", "")).strip()
    raw_wh_secret = req_data.get("webhook_secret", "••••••••")
    webhook_secret = cur_cfg.get("webhook_secret", "") if raw_wh_secret == "••••••••" else raw_wh_secret

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

    # Handle multi-target prop firm accounts (Lucid Trading, Topstep, Apex, etc.)
    cur_targets_by_id = {t.get("id"): t for t in cur_cfg.get("targets", []) if isinstance(t, dict) and t.get("id")}
    cleaned_targets = []
    if isinstance(req_data.get("targets"), list) and len(req_data["targets"]) > 0:
        for idx, t in enumerate(req_data["targets"]):
            if not isinstance(t, dict):
                continue
            t_id = str(t.get("id") or f"futures_target_{idx + 1}").strip()
            old_t = cur_targets_by_id.get(t_id, {})
            old_t_tv = old_t.get("tradovate", {})
            t_mode = t.get("mode", old_t.get("mode", "webhook"))
            if t_mode not in ("webhook", "tradovate"):
                t_mode = "webhook"
            t_wh_sec = t.get("webhook_secret", "••••••••")
            t_tv_in = t.get("tradovate", {})
            t_tv_pwd = t_tv_in.get("password", "••••••••")
            t_tv_tok = t_tv_in.get("access_token", "••••••••")
            cleaned_targets.append({
                "id": t_id,
                "name": str(t.get("name") or old_t.get("name") or f"Futures Account #{idx + 1}").strip(),
                "prop_firm": str(t.get("prop_firm") or old_t.get("prop_firm") or "Lucid Trading").strip(),
                "enabled": bool(t.get("enabled", old_t.get("enabled", True))),
                "scale": max(0.1, float(t.get("scale", old_t.get("scale", 1.0)) or 1.0)),
                "mode": t_mode,
                "webhook_url": str(t.get("webhook_url", old_t.get("webhook_url", ""))).strip(),
                "webhook_secret": old_t.get("webhook_secret", "") if t_wh_sec == "••••••••" else t_wh_sec,
                "tradovate": {
                    "env": t_tv_in.get("env", old_t_tv.get("env", "demo")),
                    "username": str(t_tv_in.get("username", old_t_tv.get("username", ""))).strip(),
                    "password": old_t_tv.get("password", "") if t_tv_pwd == "••••••••" else t_tv_pwd,
                    "account_spec": str(t_tv_in.get("account_spec", old_t_tv.get("account_spec", ""))).strip(),
                    "account_id": int(t_tv_in.get("account_id", old_t_tv.get("account_id", 0)) or 0),
                    "access_token": old_t_tv.get("access_token", "") if t_tv_tok == "••••••••" else t_tv_tok
                }
            })
    else:
        # Legacy single-target POST /api/lucid call: sync primary target with top-level fields
        existing_targets = cur_cfg.get("targets", [])
        first_old = existing_targets[0] if existing_targets else {}
        cleaned_targets = [
            {
                "id": first_old.get("id", "lucid_main"),
                "name": first_old.get("name", "Lucid Trading"),
                "prop_firm": first_old.get("prop_firm", "Lucid Trading"),
                "enabled": True,
                "scale": float(first_old.get("scale", 1.0) or 1.0),
                "mode": mode,
                "webhook_url": webhook_url,
                "webhook_secret": webhook_secret,
                "tradovate": dict(new_tv)
            }
        ]

    # Sync top-level fields from first target
    if cleaned_targets:
        first_t = cleaned_targets[0]
        mode = first_t["mode"]
        webhook_url = first_t["webhook_url"]
        webhook_secret = first_t["webhook_secret"]
        new_tv.update(first_t["tradovate"])

    # Handle master_source (MT5 / TradingView / Tradovate Master)
    cur_ms = cur_cfg.get("master_source", {})
    req_ms = req_data.get("master_source", {})
    ms_secret_in = req_ms.get("webhook_secret", "••••••••")
    cur_tv_m = cur_ms.get("tradovate_master", {})
    req_tv_m = req_ms.get("tradovate_master", {})
    tv_m_tok = req_tv_m.get("access_token", "••••••••")

    raw_rev = req_ms.get("reverse_map", cur_ms.get("reverse_map", {}))
    cleaned_rev = {}
    if isinstance(raw_rev, dict):
        for f_sym, r_info in raw_rev.items():
            f_key = str(f_sym).strip()
            if f_key and isinstance(r_info, dict) and r_info.get("mt5_symbol"):
                cleaned_rev[f_key] = {
                    "mt5_symbol": str(r_info["mt5_symbol"]).strip(),
                    "lot_per_contract": max(0.01, float(r_info.get("lot_per_contract", 0.10) or 0.10))
                }

    new_master_source = {
        "mode": req_ms.get("mode", cur_ms.get("mode", "hybrid")),
        "webhook_secret": cur_ms.get("webhook_secret", "") if ms_secret_in == "••••••••" else ms_secret_in,
        "tradovate_master": {
            "enabled": bool(req_tv_m.get("enabled", cur_tv_m.get("enabled", False))),
            "poll_interval_sec": max(0.5, float(req_tv_m.get("poll_interval_sec", cur_tv_m.get("poll_interval_sec", 1.0)) or 1.0)),
            "env": req_tv_m.get("env", cur_tv_m.get("env", "demo")),
            "account_spec": str(req_tv_m.get("account_spec", cur_tv_m.get("account_spec", ""))).strip(),
            "account_id": int(req_tv_m.get("account_id", cur_tv_m.get("account_id", 0)) or 0),
            "access_token": cur_tv_m.get("access_token", "") if tv_m_tok == "••••••••" else tv_m_tok
        },
        "reverse_map": cleaned_rev
    }

    raw_rules = req_data.get("whitelist_rules", cur_cfg.get("whitelist_rules", {}))
    if not isinstance(raw_rules, dict):
        return False, "whitelist_rules must be a JSON object", None

    cleaned_rules = {}
    for src_sym, r in raw_rules.items():
        src_key = str(src_sym).strip()
        if not src_key or not isinstance(r, dict):
            continue
        tgt_sym = str(r.get("target_symbol", "")).strip()
        if not tgt_sym:
            return False, f"Target futures contract is required for '{src_key}'", None
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

    updated_cfg = {
        "enabled": enabled,
        "mode": mode,
        "webhook_url": webhook_url,
        "webhook_secret": webhook_secret,
        "tradovate": new_tv,
        "targets": cleaned_targets,
        "master_source": new_master_source,
        "whitelist_rules": cleaned_rules
    }
    save_atomic_json(LUCID_FILE, updated_cfg, "futures_copier")
    active_t_count = sum(1 for t in cleaned_targets if t.get("enabled", True))
    log_event(
        "FUTURES",
        f"Updated Futures Copier config (enabled={enabled}, active_targets={active_t_count}/{len(cleaned_targets)}, whitelist={len(cleaned_rules)} symbols)"
    )
    return True, "", updated_cfg


# Web Server & REST API Handlers
class WebRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
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

            futures_info = load_lucid_data()
            active_targets = get_enabled_targets(futures_info)
            with lucid_lock:
                lucid_open_count = len(lucid_open_tickets)

            futures_summary = {
                "enabled": bool(futures_info.get("enabled", False)),
                "mode": futures_info.get("mode", "webhook"),
                "active_targets_count": len(active_targets),
                "total_targets_count": len(futures_info.get("targets", [])),
                "active_target_names": [t.get("name", "") for t in active_targets],
                "whitelist_count": len(futures_info.get("whitelist_rules", {})),
                "open_positions": lucid_open_count,
                "tradovate_master_enabled": bool(futures_info.get("master_source", {}).get("tradovate_master", {}).get("enabled", False))
            }

            data = {
                "version": VERSION,
                "hub_running": True,
                "active_profile": presets_info.get("active_profile", ""),
                "active_master_name": active_master_name,
                "active_slave_name": active_slave_name,
                "vantage": v_copy,
                "ftmo": f_copy,
                "lucid": futures_summary,
                "futures": futures_summary,
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

        if path in ("/api/lucid", "/api/futures"):
            self.send_json(200, mask_futures_data(load_lucid_data()))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        post_bytes = self.rfile.read(content_length)

        try:
            req_data = json.loads(post_bytes.decode("utf-8")) if post_bytes else {}
        except Exception as e:
            self.send_json(400, {"success": False, "error": f"Malformed JSON: {e}"})
            return

        # Master Webhook endpoint supports either Basic Auth OR master_source.webhook_secret token
        if path == "/api/master-webhook":
            futures_cfg = load_lucid_data()
            expected_secret = (futures_cfg.get("master_source", {}).get("webhook_secret") or "").strip()
            provided_secret = str(req_data.get("secret") or self.headers.get("X-Webhook-Secret") or "").strip()
            if expected_secret:
                if provided_secret != expected_secret and not self.check_auth():
                    return
            else:
                if not self.check_auth():
                    return
            ok, res = apply_master_webhook_signal(req_data, process_position_snapshot, log_event)
            self.send_json(200 if ok else 400, res)
            return

        if not self.check_auth():
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

        if path in ("/api/lucid", "/api/futures"):
            ok, err, updated_cfg = _save_futures_or_lucid_payload(req_data)
            if not ok:
                self.send_json(400, {"success": False, "error": err})
                return
            masked = mask_futures_data(updated_cfg)
            self.send_json(200, {"success": True, "lucid": masked, "futures": masked})
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
    start_tradovate_master_poller(load_lucid_data, process_position_snapshot, log_event)

    log_event("SYSTEM", f"All services online. Web Dashboard on {WEB_HOST}:{WEB_PORT}. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
