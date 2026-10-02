import socket
import threading
import json
import time
import os

HOST = "0.0.0.0"
VANTAGE_PORT = 9001
FTMO_PORT = 9002

# Vantage -> FTMO
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYMBOL_MAP_FILE = os.path.join(BASE_DIR, "symbol_map.json")
LOT_RULES_FILE = os.path.join(BASE_DIR, "lot_rules.json")

SYMBOL_MAP = {}
LOT_RULES = {}

def load_configs():
    """Load or reload JSON configuration files."""
    global SYMBOL_MAP, LOT_RULES
    
    # Load Symbol Map
    if os.path.exists(SYMBOL_MAP_FILE):
        try:
            with open(SYMBOL_MAP_FILE, "r", encoding="utf-8") as f:
                SYMBOL_MAP = json.load(f)
            print(f"[CONFIG] Loaded {len(SYMBOL_MAP)} symbol mappings from {SYMBOL_MAP_FILE}")
        except Exception as e:
            print(f"[ERROR] Failed to load {SYMBOL_MAP_FILE}: {e}")
    else:
        print(f"[WARN] {SYMBOL_MAP_FILE} not found. Symbol mapping disabled.")
        SYMBOL_MAP = {}

    # Load Lot Rules
    if os.path.exists(LOT_RULES_FILE):
        try:
            with open(LOT_RULES_FILE, "r", encoding="utf-8") as f:
                LOT_RULES = json.load(f)
            print(f"[CONFIG] Loaded lot rules from {LOT_RULES_FILE}")
        except Exception as e:
            print(f"[ERROR] Failed to load {LOT_RULES_FILE}: {e}")
            LOT_RULES = {"default_multiplier": 1.0, "min_lot": 0.01, "rules": {}}
    else:
        print(f"[WARN] {LOT_RULES_FILE} not found. Using default lot multiplier (1.0).")
        LOT_RULES = {"default_multiplier": 1.0, "min_lot": 0.01, "rules": {}}

# Initial configuration load
load_configs()

ftmo_client_socket = None
ftmo_lock = threading.Lock()

def map_symbol(vantage_symbol: str) -> str:
    return SYMBOL_MAP.get(vantage_symbol, vantage_symbol)

def calculate_target_lot(target_symbol: str, src_lot: float) -> float:
    """Calculate target lot size for FTMO based on symbol and lot mapping rules."""
    min_lot = LOT_RULES.get("min_lot", 0.01)
    default_multiplier = LOT_RULES.get("default_multiplier", 1.0)
    rules = LOT_RULES.get("rules", {})
    
    rule = rules.get(target_symbol)
    
    if rule:
        # 1. Check fixed lot
        if "fixed_lot" in rule:
            return round(rule["fixed_lot"], 2)
            
        # 2. Check exact lot mapping (Lot Map)
        if "lot_map" in rule:
            # Support both float and str keys
            str_lot = str(round(src_lot, 2))
            if str_lot in rule["lot_map"]:
                return round(float(rule["lot_map"][str_lot]), 2)
            if src_lot in rule["lot_map"]:
                return round(float(rule["lot_map"][src_lot]), 2)
                
        # 3. Check symbol-specific multiplier
        if "multiplier" in rule:
            return max(min_lot, round(src_lot * rule["multiplier"], 2))
            
        # 4. Symbol fallback multiplier
        if "default_multiplier" in rule:
            return max(min_lot, round(src_lot * rule["default_multiplier"], 2))

    # 5. Global default multiplier
    return max(min_lot, round(src_lot * default_multiplier, 2))

def send_to_ftmo(cmd: str):
    global ftmo_client_socket
    with ftmo_lock:
        if ftmo_client_socket:
            try:
                ftmo_client_socket.sendall((cmd + "\n").encode('utf-8'))
                print(f"[TO FTMO] {cmd}")
            except Exception as e:
                print(f"[ERROR] Failed to send to FTMO: {e}")
                ftmo_client_socket = None
        else:
            print("[WARN] FTMO receiver not connected, command dropped!")

def handle_vantage_client(conn, addr):
    print(f"[CONNECTED] Vantage Watcher from {addr}")
    last_positions = {}
    buffer = ""
    
    with conn:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buffer += data.decode('utf-8', errors='ignore')
            
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    positions = json.loads(line)
                    current_positions = {p["ticket"]: p for p in positions}
                    
                    # 1. Detect open positions
                    for ticket, pos in current_positions.items():
                        if ticket not in last_positions:
                            target_symbol = map_symbol(pos['symbol'])
                            vol = calculate_target_lot(target_symbol, pos["volume"])
                            cmd = f"OPEN;{target_symbol};{pos['type']};{vol};{pos['sl']};{pos['tp']};{ticket}"
                            send_to_ftmo(cmd)
                    
                    # 2. Detect closed positions
                    for ticket, pos in last_positions.items():
                        if ticket not in current_positions:
                            target_symbol = map_symbol(pos['symbol'])
                            cmd = f"CLOSE;{target_symbol};{ticket}"
                            send_to_ftmo(cmd)
                            
                    last_positions = current_positions
                except Exception as e:
                    print(f"[ERROR parsing Vantage data] {e} | Raw: {line}")
                    
    print("[DISCONNECTED] Vantage Watcher disconnected")

def handle_ftmo_client(conn, addr):
    global ftmo_client_socket
    print(f"[CONNECTED] FTMO Executor from {addr}")
    with ftmo_lock:
        ftmo_client_socket = conn
        
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            print(f"[FROM FTMO] {data.decode('utf-8', errors='ignore').strip()}")
    except:
        pass
    finally:
        with ftmo_lock:
            if ftmo_client_socket == conn:
                ftmo_client_socket = None
        print("[DISCONNECTED] FTMO Executor disconnected")

def start_server(port, handler):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, port))
    server.listen(5)
    print(f"[LISTENING] Server running on port {port}")
    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=handler, args=(conn, addr), daemon=True)
        t.start()

if __name__ == "__main__":
    t_vantage = threading.Thread(target=start_server, args=(VANTAGE_PORT, handle_vantage_client), daemon=True)
    t_ftmo = threading.Thread(target=start_server, args=(FTMO_PORT, handle_ftmo_client), daemon=True)
    
    t_vantage.start()
    t_ftmo.start()
    
    print("TCP Hub started. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
