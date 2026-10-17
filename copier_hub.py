import socket
import threading
import json
import time
import os
import shutil
import datetime
import base64
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

HOST = "0.0.0.0"
VANTAGE_PORT = 9001
FTMO_PORT = 9002

WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

# Base directories and file paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYMBOL_MAP_FILE = os.path.join(BASE_DIR, "symbol_map.json")
LOT_RULES_FILE = os.path.join(BASE_DIR, "lot_rules.json")
AUTH_FILE = os.path.join(BASE_DIR, "web_auth.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
WEB_STATIC_DIR = os.path.join(BASE_DIR, "web")

os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(WEB_STATIC_DIR, exist_ok=True)

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

def calculate_target_lot(target_symbol: str, src_lot: float) -> float:
    """Calculate target lot size for FTMO based on symbol and lot mapping rules."""
    with config_lock:
        min_lot = LOT_RULES.get("min_lot", 0.01)
        default_multiplier = LOT_RULES.get("default_multiplier", 1.0)
        rules = LOT_RULES.get("rules", {})
        
        rule = rules.get(target_symbol)
        
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

def process_position_snapshot(last_positions: dict, positions: list):
    """Compare previous and current Vantage position snapshots, dispatch commands to FTMO, and return (current_positions, commands_sent)."""
    current_positions = {p["ticket"]: p for p in positions}
    commands_sent = []

    # 1. Detect open positions & partial closes
    for ticket, pos in current_positions.items():
        if ticket not in last_positions:
            target_symbol = map_symbol(pos['symbol'])
            vol = calculate_target_lot(target_symbol, pos["volume"])
            # Stealth SL/TP (Mode A): always pass 0;0 to avoid cross-broker quote mismatch
            cmd = f"OPEN;{target_symbol};{pos['type']};{vol};0;0;{ticket}"
            log_event("TRADE", f"Open detected: {pos['symbol']} -> {target_symbol} vol={vol} (ticket #{ticket})")
            send_to_ftmo(cmd)
            commands_sent.append(cmd)
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

    # 2. Detect closed positions
    for ticket, pos in last_positions.items():
        if ticket not in current_positions:
            target_symbol = map_symbol(pos['symbol'])
            cmd = f"CLOSE;{target_symbol};{ticket}"
            log_event("TRADE", f"Close detected: {pos['symbol']} -> {target_symbol} (ticket #{ticket})")
            send_to_ftmo(cmd)
            commands_sent.append(cmd)

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

        if path in ("/", "/index.html"):
            index_path = os.path.join(WEB_STATIC_DIR, "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"web/index.html not found")
            return

        if path == "/api/status":
            with status_lock:
                v_copy = dict(vantage_status)
                f_copy = dict(ftmo_status)
            with log_lock:
                logs_copy = list(event_logs)

            data = {
                "hub_running": True,
                "vantage": v_copy,
                "ftmo": f_copy,
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
