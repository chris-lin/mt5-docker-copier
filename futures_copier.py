import json
import threading
import time
import urllib.request
import urllib.error
from collections import deque

# Shared state for Futures Copier (Lucid Trading, Topstep, Apex, etc.) & External/Tradovate Master
lucid_lock = threading.Lock()
lucid_open_tickets = {}           # Primary/legacy ticket state: {ticket: {...}}
target_open_tickets = {}          # Per-target ticket state: {target_id: {ticket: {...}}}
lucid_dispatched_orders = deque(maxlen=100)    # 6-key dict for backward-compatible assertions
futures_dispatched_orders = deque(maxlen=100)  # Enriched dict with target_id & target_name

external_master_lock = threading.Lock()
external_master_positions = {}    # {ticket: {"ticket": int, "symbol": str, "type": int, "volume": float, ...}}


def reset_futures_runtime_state():
    """Clear in-memory open tickets and dispatched order history (used by unit tests)."""
    with lucid_lock:
        lucid_open_tickets.clear()
        target_open_tickets.clear()
        lucid_dispatched_orders.clear()
        futures_dispatched_orders.clear()
    with external_master_lock:
        external_master_positions.clear()


def get_default_futures_config() -> dict:
    """Return default Multi-Prop Futures Copier & Universal Master Source configuration."""
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
        "targets": [
            {
                "id": "lucid_main",
                "name": "Lucid Trading 50K",
                "prop_firm": "Lucid Trading",
                "enabled": True,
                "scale": 1.0,
                "mode": "webhook",
                "webhook_url": "",
                "webhook_secret": "",
                "tradovate": {
                    "env": "demo",
                    "username": "",
                    "password": "",
                    "account_spec": "",
                    "account_id": 0,
                    "access_token": ""
                }
            },
            {
                "id": "topstep_main",
                "name": "Topstep 50K (TopstepX / Tradovate)",
                "prop_firm": "Topstep",
                "enabled": False,
                "scale": 1.0,
                "mode": "webhook",
                "webhook_url": "",
                "webhook_secret": "",
                "tradovate": {
                    "env": "demo",
                    "username": "",
                    "password": "",
                    "account_spec": "",
                    "account_id": 0,
                    "access_token": ""
                }
            }
        ],
        "master_source": {
            "mode": "hybrid",
            "webhook_secret": "",
            "tradovate_master": {
                "enabled": False,
                "poll_interval_sec": 1.0,
                "env": "demo",
                "account_spec": "",
                "account_id": 0,
                "access_token": ""
            },
            "reverse_map": {
                "MGCZ6": {"mt5_symbol": "XAUUSD", "lot_per_contract": 0.10},
                "GCZ6": {"mt5_symbol": "XAUUSD", "lot_per_contract": 1.00},
                "MNQZ6": {"mt5_symbol": "US100.cash", "lot_per_contract": 0.10},
                "NQZ6": {"mt5_symbol": "US100.cash", "lot_per_contract": 1.00},
                "MBTZ6": {"mt5_symbol": "BTCUSD", "lot_per_contract": 0.10}
            }
        },
        "whitelist_rules": {
            "XAUUSD247": {"target_symbol": "MGCZ6", "sizing_mode": "multiplier", "multiplier": 2.5, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "XAUUSD.tv": {"target_symbol": "MGCZ6", "sizing_mode": "multiplier", "multiplier": 250.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "XAUUSD.a": {"target_symbol": "MGCZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "XAUUSD": {"target_symbol": "MGCZ6", "sizing_mode": "fixed", "multiplier": 10.0, "fixed_contracts": 2, "min_contracts": 1, "max_contracts": 20},
            "NAS100.r": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "NAS100.a": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "NAS100": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "US100.cash": {"target_symbol": "MNQZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 20},
            "BTCUSD": {"target_symbol": "MBTZ6", "sizing_mode": "multiplier", "multiplier": 10.0, "fixed_contracts": 1, "min_contracts": 1, "max_contracts": 10}
        }
    }


def normalize_futures_config(data: dict) -> dict:
    """Ensure loaded futures/lucid config has all multi-target and master_source fields populated."""
    defaults = get_default_futures_config()
    if not isinstance(data, dict):
        return defaults

    cfg = json.loads(json.dumps(data))
    cfg.setdefault("enabled", False)
    cfg.setdefault("mode", "webhook")
    cfg.setdefault("webhook_url", "")
    cfg.setdefault("webhook_secret", "")
    cfg.setdefault("tradovate", defaults["tradovate"])
    cfg.setdefault("whitelist_rules", defaults["whitelist_rules"])

    # Normalize master_source
    ms = cfg.setdefault("master_source", {})
    def_ms = defaults["master_source"]
    ms.setdefault("mode", def_ms["mode"])
    ms.setdefault("webhook_secret", def_ms["webhook_secret"])
    tv_m = ms.setdefault("tradovate_master", {})
    for k, v in def_ms["tradovate_master"].items():
        tv_m.setdefault(k, v)
    ms.setdefault("reverse_map", def_ms["reverse_map"])

    # Normalize targets list (if legacy single-target config, seed first target from top-level fields)
    targets = cfg.get("targets")
    if not isinstance(targets, list) or len(targets) == 0:
        cfg["targets"] = [
            {
                "id": "lucid_main",
                "name": "Lucid Trading",
                "prop_firm": "Lucid Trading",
                "enabled": True,
                "scale": 1.0,
                "mode": cfg.get("mode", "webhook"),
                "webhook_url": cfg.get("webhook_url", ""),
                "webhook_secret": cfg.get("webhook_secret", ""),
                "tradovate": dict(cfg.get("tradovate", defaults["tradovate"]))
            }
        ]
    else:
        for idx, t in enumerate(targets):
            if not isinstance(t, dict):
                continue
            t.setdefault("id", f"futures_target_{idx + 1}")
            t.setdefault("name", f"Futures Account #{idx + 1}")
            t.setdefault("prop_firm", "Lucid Trading")
            t.setdefault("enabled", True)
            t.setdefault("scale", 1.0)
            t.setdefault("mode", "webhook")
            t.setdefault("webhook_url", "")
            t.setdefault("webhook_secret", "")
            tv = t.setdefault("tradovate", {})
            for k, v in defaults["tradovate"].items():
                tv.setdefault(k, v)

    # Keep top-level legacy fields synced with the first target for backward compatibility
    first = cfg["targets"][0]
    cfg["mode"] = first.get("mode", cfg.get("mode", "webhook"))
    cfg["webhook_url"] = first.get("webhook_url", cfg.get("webhook_url", ""))
    cfg["webhook_secret"] = first.get("webhook_secret", cfg.get("webhook_secret", ""))
    cfg["tradovate"] = dict(first.get("tradovate", cfg.get("tradovate", {})))
    return cfg


def mask_futures_data(data: dict) -> dict:
    """Mask sensitive secrets/passwords/tokens in Futures config before returning to Web UI."""
    masked = normalize_futures_config(data)
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

    for t in masked.get("targets", []):
        if t.get("webhook_secret"):
            t["webhook_secret"] = "••••••••"
        t_tv = t.get("tradovate", {})
        if isinstance(t_tv, dict):
            if t_tv.get("password"):
                t_tv["password"] = "••••••••"
            if t_tv.get("access_token"):
                t_tv["access_token"] = "••••••••"
            if t_tv.get("sec"):
                t_tv["sec"] = "••••••••"

    ms = masked.get("master_source", {})
    if ms.get("webhook_secret"):
        ms["webhook_secret"] = "••••••••"
    tv_m = ms.get("tradovate_master", {})
    if isinstance(tv_m, dict) and tv_m.get("access_token"):
        tv_m["access_token"] = "••••••••"

    with lucid_lock:
        masked["open_tickets"] = dict(lucid_open_tickets)
    return masked


def get_enabled_targets(futures_cfg: dict) -> list:
    """Return list of enabled target account dicts when futures copier is enabled."""
    if not futures_cfg or not futures_cfg.get("enabled", False):
        return []
    cfg = normalize_futures_config(futures_cfg)
    return [t for t in cfg.get("targets", []) if t.get("enabled", True)]


def calculate_futures_order(src_symbol: str, src_lot: float, futures_cfg: dict, scale: float = 1.0):
    """
    Evaluate strict whitelist for Futures copying and return (target_symbol, integer_contracts) or None.
    Also supports direct Futures-to-Futures pass-through when Master is TradingView/Tradovate (e.g. MNQZ6 -> MNQZ6).
    """
    rules = futures_cfg.get("whitelist_rules", {})
    rule = rules.get(src_symbol)
    if rule:
        target_symbol = (rule.get("target_symbol") or "").strip()
        if not target_symbol:
            return None
        sizing_mode = rule.get("sizing_mode", "multiplier")
        if sizing_mode == "fixed":
            raw_qty = float(rule.get("fixed_contracts", 1)) * float(scale)
        else:
            raw_qty = float(src_lot) * float(rule.get("multiplier", 1.0)) * float(scale)
        min_c = max(1, int(rule.get("min_contracts", 1)))
        max_c = max(min_c, int(rule.get("max_contracts", 50)))
        contracts = max(min_c, min(max_c, int(round(raw_qty))))
        return target_symbol, contracts

    # Check if src_symbol is ALREADY a whitelisted target futures contract or in master_source.reverse_map
    rev_map = futures_cfg.get("master_source", {}).get("reverse_map", {})
    whitelisted_contracts = {r.get("target_symbol") for r in rules.values() if isinstance(r, dict) and r.get("target_symbol")}
    if src_symbol in rev_map or src_symbol in whitelisted_contracts:
        contracts = max(1, int(round(float(src_lot) * float(scale))))
        return src_symbol, contracts

    return None


def resolve_reverse_futures_to_mt5(src_symbol: str, src_volume: float, futures_cfg: dict, symbol_map: dict):
    """
    If the Master order originated on a Futures symbol (e.g. MGCZ6, MNQZ6, MBTZ6 on TradingView/Tradovate),
    convert it to the corresponding MT5 CFD symbol and CFD lot size using master_source.reverse_map.
    Returns (mapped_cfd_symbol, base_cfd_lot, is_reverse_mapped).
    """
    if src_symbol in symbol_map:
        return symbol_map[src_symbol], float(src_volume), False

    rev_map = (futures_cfg or {}).get("master_source", {}).get("reverse_map", {})
    if src_symbol in rev_map:
        entry = rev_map[src_symbol]
        mt5_sym = (entry.get("mt5_symbol") or src_symbol).strip()
        # Allow SYMBOL_MAP to further map the canonical CFD symbol if desired
        final_sym = symbol_map.get(mt5_sym, mt5_sym)
        lot_per_contract = float(entry.get("lot_per_contract", 0.10) or 0.10)
        cfd_lot = max(0.01, round(float(src_volume) * lot_per_contract, 2))
        return final_sym, cfd_lot, True

    return src_symbol, float(src_volume), False


def execute_target_http(order_payload: dict, target_cfg: dict, log_fn):
    """Send order payload to a specific Futures Prop Firm target via Webhook Bridge or Tradovate REST API."""
    target_name = target_cfg.get("name") or target_cfg.get("prop_firm") or "Futures"
    mode = target_cfg.get("mode", "webhook")
    try:
        if mode == "webhook":
            url = (target_cfg.get("webhook_url") or "").strip()
            if not url:
                log_fn("FUTURES", f"[{target_name}] Simulated {order_payload['event']}: {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (No webhook_url configured)")
                return
            body = dict(order_payload)
            if target_cfg.get("webhook_secret"):
                body["secret"] = target_cfg["webhook_secret"]
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "MT5-Copier-Hub/3.0"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                log_fn("TO FUTURES", f"[{target_name}] Webhook {order_payload['event']} -> {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (HTTP {resp.status})")
        else:
            tv = target_cfg.get("tradovate", {})
            env = tv.get("env", "demo")
            base_url = "https://live.tradovateapi.com/v1" if env == "live" else "https://demo.tradovateapi.com/v1"
            token = (tv.get("access_token") or "").strip()
            if not token:
                log_fn("FUTURES", f"[{target_name}] Simulated Tradovate {order_payload['event']}: {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (No access_token configured)")
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
                log_fn("TO FUTURES", f"[{target_name}] Tradovate {order_payload['event']} -> {order_payload['action']} {order_payload['quantity']} {order_payload['symbol']} (HTTP {resp.status})")
    except Exception as e:
        log_fn("ERROR", f"[{target_name}] Futures order failed ({order_payload['action']} {order_payload['quantity']} {order_payload['symbol']}): {e}")


def dispatch_futures_order(order_payload: dict, target_cfg: dict, log_fn):
    """Record order in history and fire non-blocking HTTP request in background thread."""
    base_record = {
        "event": order_payload["event"],
        "ticket": order_payload["ticket"],
        "src_symbol": order_payload["src_symbol"],
        "symbol": order_payload["symbol"],
        "action": order_payload["action"],
        "quantity": order_payload["quantity"]
    }
    enriched_record = {
        **base_record,
        "target_id": target_cfg.get("id", "lucid_main"),
        "target_name": target_cfg.get("name", "Lucid Trading"),
        "prop_firm": target_cfg.get("prop_firm", "Lucid Trading")
    }
    with lucid_lock:
        lucid_dispatched_orders.append(base_record)
        futures_dispatched_orders.append(enriched_record)
    t = threading.Thread(target=execute_target_http, args=(base_record, target_cfg, log_fn), daemon=True)
    t.start()


def apply_master_webhook_signal(req_data: dict, process_snapshot_fn, log_fn) -> tuple:
    """
    Process an incoming TradingView / Tradovate / Custom Master Webhook signal.
    Supports two payload formats:
      1. Full snapshot: {"positions": [{"ticket": 8001, "symbol": "MNQZ6", "type": 0, "volume": 2}]}
      2. Action event:  {"symbol": "MNQZ6", "action": "buy"|"sell"|"close"|"partial", "quantity": 2, "ticket": 8001}
    Returns (success: bool, response_dict: dict).
    """
    with external_master_lock:
        if isinstance(req_data.get("positions"), list):
            snapshot = []
            for p in req_data["positions"]:
                if not isinstance(p, dict) or not p.get("symbol"):
                    continue
                t_id = int(p.get("ticket") or abs(hash(p["symbol"])) % 900000 + 100000)
                snapshot.append({
                    "ticket": t_id,
                    "symbol": str(p["symbol"]).strip(),
                    "type": int(p.get("type", 0)),
                    "volume": float(p.get("volume", 1.0)),
                    "price": float(p.get("price", 0.0)),
                    "sl": float(p.get("sl", 0.0)),
                    "tp": float(p.get("tp", 0.0))
                })
            old_snapshot = dict(external_master_positions)
            new_positions, cmds = process_snapshot_fn(old_snapshot, snapshot)
            external_master_positions.clear()
            external_master_positions.update(new_positions)
            log_fn("MASTER WEBHOOK", f"Processed position snapshot ({len(snapshot)} open positions)")
            return True, {"success": True, "open_positions": len(external_master_positions), "commands_sent": cmds}

        symbol = str(req_data.get("symbol") or req_data.get("ticker") or "").strip()
        action = str(req_data.get("action") or req_data.get("side") or "").strip().lower()
        qty = float(req_data.get("quantity") or req_data.get("contracts") or req_data.get("volume") or 1.0)
        if not symbol or action not in ("buy", "long", "sell", "short", "close", "exit", "flat", "partial"):
            return False, {"error": "Webhook payload must provide 'symbol' and valid 'action' (buy/sell/close/partial) or 'positions' array"}

        ticket = int(req_data.get("ticket") or (abs(hash(symbol)) % 800000 + 200000))
        old_snapshot = {int(k): dict(v) for k, v in external_master_positions.items()}
        cur_map = {int(k): dict(v) for k, v in old_snapshot.items()}

        if action in ("buy", "long", "sell", "short"):
            pos_type = 0 if action in ("buy", "long") else 1
            cur_map[ticket] = {
                "ticket": ticket,
                "symbol": symbol,
                "type": pos_type,
                "volume": qty,
                "price": float(req_data.get("price", 0.0)),
                "sl": float(req_data.get("sl", 0.0)),
                "tp": float(req_data.get("tp", 0.0))
            }
        elif action == "partial":
            if ticket in cur_map:
                rem = max(0.0, float(cur_map[ticket]["volume"]) - qty)
                if rem <= 1e-6:
                    del cur_map[ticket]
                else:
                    cur_map[ticket]["volume"] = rem
        elif action in ("close", "exit", "flat"):
            # Remove matching ticket or all tickets for that symbol
            if ticket in cur_map:
                del cur_map[ticket]
            else:
                for k in [k for k, v in cur_map.items() if v.get("symbol") == symbol]:
                    del cur_map[k]

        snapshot_list = list(cur_map.values())
        new_positions, cmds = process_snapshot_fn(old_snapshot, snapshot_list)
        external_master_positions.clear()
        external_master_positions.update(new_positions)
        log_fn("MASTER WEBHOOK", f"Signal '{action.upper()} {qty} {symbol}' (ticket #{ticket}) -> {len(cmds)} MT5 cmd(s)")
        return True, {"success": True, "ticket": ticket, "open_positions": len(external_master_positions), "commands_sent": cmds}


_contract_name_cache = {}


def _resolve_tradovate_contract_name(base_url: str, token: str, contract_id: int) -> str:
    if contract_id in _contract_name_cache:
        return _contract_name_cache[contract_id]
    req = urllib.request.Request(
        f"{base_url}/contract/item?id={int(contract_id)}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        name = (data.get("name") or str(contract_id)).strip()
        _contract_name_cache[contract_id] = name
        return name


def poll_tradovate_master_once(futures_cfg: dict, process_snapshot_fn, log_fn):
    """Poll Tradovate Master account /position/list and feed open positions into process_snapshot_fn."""
    ms = (futures_cfg or {}).get("master_source", {})
    tv_m = ms.get("tradovate_master", {})
    if not tv_m.get("enabled"):
        return
    token = (tv_m.get("access_token") or "").strip()
    if not token:
        return

    env = tv_m.get("env", "demo")
    base_url = "https://live.tradovateapi.com/v1" if env == "live" else "https://demo.tradovateapi.com/v1"
    target_acc_id = int(tv_m.get("account_id") or 0)

    req = urllib.request.Request(
        f"{base_url}/position/list",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET"
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        positions_raw = json.loads(resp.read().decode("utf-8"))

    if not isinstance(positions_raw, list):
        return

    snapshot = []
    for p in positions_raw:
        if not isinstance(p, dict):
            continue
        if target_acc_id and int(p.get("accountId") or 0) != target_acc_id:
            continue
        net_pos = int(p.get("netPos") or 0)
        if net_pos == 0:
            continue
        contract_id = int(p.get("contractId") or 0)
        sym = (p.get("symbol") or "").strip()
        if not sym and contract_id:
            sym = _resolve_tradovate_contract_name(base_url, token, contract_id)
        if not sym:
            continue
        ticket_id = int(p.get("id") or (700000 + contract_id))
        snapshot.append({
            "ticket": ticket_id,
            "symbol": sym,
            "type": 0 if net_pos > 0 else 1,
            "volume": float(abs(net_pos)),
            "price": float(p.get("netPrice") or 0.0),
            "sl": 0.0,
            "tp": 0.0
        })

    with external_master_lock:
        old_snapshot = dict(external_master_positions)
        if old_snapshot != {item["ticket"]: item for item in snapshot}:
            new_positions, _ = process_snapshot_fn(old_snapshot, snapshot)
            external_master_positions.clear()
            external_master_positions.update(new_positions)


def start_tradovate_master_poller(load_cfg_fn, process_snapshot_fn, log_fn):
    """Background daemon loop that polls Tradovate Master when enabled."""
    def _loop():
        while True:
            interval = 1.0
            try:
                cfg = load_cfg_fn()
                tv_m = (cfg or {}).get("master_source", {}).get("tradovate_master", {})
                interval = max(0.5, float(tv_m.get("poll_interval_sec", 1.0) or 1.0))
                if tv_m.get("enabled") and tv_m.get("access_token"):
                    poll_tradovate_master_once(cfg, process_snapshot_fn, log_fn)
            except Exception as e:
                log_fn("WARN", f"Tradovate Master poll warning: {e}")
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t

