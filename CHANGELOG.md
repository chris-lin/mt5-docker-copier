# Changelog

All notable changes to this project will be documented in this file.

## [v3.1] - 2026-11-11

### Added
- **$0-Monthly-Fee Direct Tradovate Integration & Auto-Discovery (`futures_copier.py`, `copier_hub.py`)**:
  - Added `discover_tradovate_account()` and `POST /api/tradovate-sync` to automatically query Tradovate `/v1/account/list` using a Bearer token and populate `Account Spec` and numeric `Account ID` without manual lookup.
  - Added `start_tradovate_token_renewer()` and `renew_all_tradovate_tokens_once()` to automatically extend active Tradovate Bearer tokens every 35 minutes via `/v1/auth/renewaccesstoken` and persist updated tokens to `.config/futures_copier.json`.
  - Added `sanitize_tradovate_token()` and automatic **Demo <-> Live environment fallback** (`demo.tradovateapi.com` <-> `live.tradovateapi.com`) on `HTTP 401/403` responses, gracefully handling pasted `Bearer ` prefixes, quotes, or full `api_authenticator_state` JSON objects from browser storage.
  - Added a built-in **Tradeify 50K (Tradovate)** target preset and `format_webhook_payload()` adapters supporting `standard`, `traderspost`, and `pickmytrade` JSON schemas.
- **1-Click Browser Token Sync Bookmarklet (`web/index.html`, `web/app.js`)**:
  - Added the draggable **`🔖 ⚡ Sync Tradovate to Copier`** bookmarklet (`initTradovateBookmarklet()`), scanning `localStorage` and `sessionStorage` (`api_authenticator_state`) on `trader.tradovate.com` to sync active Bearer tokens to both `Tradovate Master` and `Targets` in one click.
- **Tradovate Net-Position (`netPos`) Layering & Bidirectional 4-Way Copying (`futures_copier.py`, `copier_hub.py`)**:
  - Added `reconcile_net_position_layers()` to convert Tradovate and ticketless TradingView net positions (`netPos`) into MT5-compatible ticket layers, enabling **Scale-In (additions)**, **LIFO Partial Closes**, and **1-Click Reversals (close-before-open)** without recompiling `FTMOReceiver.mq5`.
  - Added **Anti-Self-Loop Guard (`_is_same_tradovate_account()`)** in `process_position_snapshot()` to prevent routing orders back to the same Tradovate account that originated a Master trade when both `Master Parallel Futures Switch` and `Tradovate Master Auto-Poller` are enabled.
- **Wildcard Futures Contract Month Matching (`futures_copier.py`)**:
  - Added `extract_futures_root()` and `DEFAULT_FUTURES_ROOT_MAP` (`MGC`, `GC`, `MNQ`, `NQ`, `MES`, `ES`, `MYM`, `YM`, `MBT`, `BTC`), automatically matching any futures contract expiration month or continuous ticker (`MGCM6`, `MGCZ6`, `MGC1!`, `MNQM6`, `MNQ1!`, etc.) to its MT5 CFD symbol (`XAUUSD`, `US100.cash`, `US500.cash`, `US30.cash`, `BTCUSD`).

### Changed & Fixed
- **Futures-to-MT5 Lot Sizing Isolation (`copier_hub.py`)**:
  - Fixed lot rule evaluation priority in `process_position_snapshot()` when copying from a Futures Master (`is_rev == True`) to an MT5 Slave so that integer futures contract quantities always use `reverse_map` `lot_per_contract` sizing unless an explicit rule exists for the source futures ticker itself, preventing MT5 CFD target-symbol rules (e.g. `XAUUSD` or `US100.cash`) from treating contract counts as CFD lots.
- **Close-Before-Open Ordering on Reversals (`copier_hub.py`)**:
  - Reordered `process_position_snapshot()` to dispatch `CLOSE` commands (including direction flips) before `OPEN` commands within the same snapshot cycle, freeing margin before opening opposite positions.
- **SSH Disconnection Immunity (`copier_hub.py`)**:
  - Fixed `[Errno 5] Input/output error` (`EIO`) in `log_event()` when the launching SSH session disconnects by recording in-memory `event_logs` first and catching `OSError` on detached `stdout` writes.
- **Clean `.config/` Runtime & `config_templates/` Directory Layout**:
  - Centralized tracked example templates under `config_templates/` and runtime configuration files under `.config/` (`symbol_map.json`, `lot_rules.json`, `presets.json`, `accounts.json`, `futures_copier.json`, `web_auth.json`).

---

## [v3.0] - 2026-10-21

### Added
- **Multi-Prop Futures Parallel Copier (`futures_copier.py`)**:
  - Added a lightweight, non-blocking parallel futures execution engine (`GET /api/futures`, `POST /api/futures`, with backward-compatible `/api/lucid`) supporting multiple simultaneous Futures Prop Firm target accounts (e.g., **Lucid Trading**, **Topstep / TopstepX**, **Apex**, **TakeProfitTrader**) via **Webhook Bridge** or **Tradovate Direct REST API** without requiring additional Docker containers.
  - Each futures target account supports independent enable/pause toggles and per-account contract scaling (`scale`), alongside strict whitelist symbol filtering (Gold `MGCZ6`, Nasdaq `MNQZ6`, Micro Bitcoin `MBTZ6`), whole integer contract rounding, and Netting partial/full close tracking.
  - Added the **`🚀 Futures Copier` tab** and live active-targets header badge to the Web Dashboard.
- **Universal Multi-Source Order Entry (MT5 / TradingView CFD / TradingView Tradovate Futures)**:
  - **MT5 Direct & TradingView CFD (Vantage / Pepperstone / Exness)**: Supports placing orders directly in MT5 or via TradingView bound to MT5-synced CFD accounts, with built-in profile presets including `Pepperstone -> FTMO` and `Tradovate (Futures) -> FTMO`.
  - **TradingView Futures Master (`Tradovate Master Poller` & `POST /api/master-webhook`)**: Supports placing orders on TradingView bound to a Futures broker (Tradovate) or via TradingView Alert Webhooks, automatically reverse-mapping futures contracts (`MNQZ6 -> US100.cash`, `MGCZ6 -> XAUUSD`, `MBTZ6 -> BTCUSD`) to copy into **FTMO (MT5 Slave)** and all enabled **Futures Prop Firms** simultaneously.
- **Zero-Overhead VPS System Load Strip (`vps_monitor.py`)**:
  - Added `get_vps_metrics()` with a 5-second in-memory cache reading Linux `/proc/stat`, `/proc/meminfo`, `/proc/uptime`, and `shutil.disk_usage("/")` directly without spawning external subprocesses.
  - Added a mobile-friendly, 1-line non-sticky collapsible VPS status strip (`CPU`, `RAM`, `Swap`, `Disk`, `Uptime`) to `web/index.html` with Page Visibility API (`document.hidden`) auto-pause when the browser tab is in the background.

### Changed
- **Modular Backend & Web Dashboard Architecture**:
  - Split backend logic into `copier_hub.py` (core TCP & HTTP routing), `futures_copier.py` (multi-prop futures & TradingView/Tradovate master engine), and `vps_monitor.py` (cached host resource monitor).
  - Split the single-file `web/index.html` into dedicated `web/index.html` (HTML structure), `web/style.css` (responsive desktop & mobile stylesheet), and `web/app.js` (frontend application logic), served natively by `copier_hub.py`.

---

## [v2.0] - 2026-10-19

### Added
- **Dedicated `profiles/` Management Directory**:
  - Introduced the `profiles/` directory (`profiles/presets.example.json`, `profiles/accounts.example.json`, `profiles/presets.json`, and `profiles/accounts.json`) to centralize multi-broker profile presets and account switching configurations without cluttering the project root.
  - Added both `profiles/accounts.json` and `profiles/presets.json` to `.gitignore` to ensure live MT5 login credentials and personalized lot sizing presets remain strictly local and untracked.
- **One-Click Multi-Broker Profile Presets (`profiles/presets.json`)**:
  - Bundled `symbol_map` and `lot_rules` into named profile presets with four built-in pairings: `Vantage -> FTMO`, `Vantage -> FundedNext`, `Exness -> FTMO`, and `Exness -> FundedNext`.
  - Added `GET /api/profiles` and `POST /api/profiles` (`save`, `activate`, `delete`) endpoints in `copier_hub.py` to support zero-downtime hot-swapping of symbol mappings and lot rules with automatic backups.
  - Added an **Active Profile Switcher Bar** to `web/index.html` for instant preset switching and saving.
- **Dual-Side Master & Slave MT5 Account Switcher (`profiles/accounts.json`)**:
  - Added dual-side account management in `copier_hub.py` (`GET /api/accounts` and `POST /api/accounts` with masked password responses) supporting both **Master Sender** (`mt5_vantage` container / `vantage_data/mt5-server.ini`) and **Slave Receiver** (`mt5_ftmo` container / `ftmo_data/mt5-server.ini`).
  - Switching a Master or Slave account automatically generates the corresponding UTF-16LE `mt5-server.ini`, optionally activates the account's linked profile preset (`linked_profile`), and restarts the target Docker container in a background thread (`docker restart mt5_vantage` or `docker restart mt5_ftmo`).
  - Added the **`👤 Accounts` tab** and modal editor in `web/index.html`, along with live active Master/Slave account name badges in the header.

---

## [v1.1] - 2026-10-17

### Added
- **Proportional Partial Close Synchronization**:
  - `copier_hub.py` now detects partial volume reductions on open positions, calculates the proportional reduction ratio `(old_vol - new_vol) / old_vol`, and dispatches `PARTIAL;{symbol};{ratio};{ticket}` commands.
  - `mql5/FTMOReceiver.mq5` adds `PARTIAL` command handling, `NormalizeLot()` lot step rounding, and dual-anchor tracking (`POSITION_MAGIC` + `POSITION_COMMENT` via `IsTargetPosition()`), ensuring remaining positions stay linked even after MT5 clears `POSITION_COMMENT` during `trade.PositionClosePartial()`.
- **Restart Idempotency Guard (Duplicate Open Protection)**:
  - `mql5/FTMOReceiver.mq5` adds `HasPositionBySource()` to verify whether an active position matching `POSITION_MAGIC` or `Src:{ticket}` already exists before executing an `OPEN` command, preventing duplicate entries when `copier_hub.py` restarts or reconnects.
- **Web Configuration Dashboard & Mobile UI Optimization**:
  - `copier_hub.py` integrates a zero-dependency HTTP server (`127.0.0.1:8080`) protected by HTTP Basic Auth (`web_auth.json`), enabling live editing, in-memory hot-reloading, and automatic timestamped backups (`backups/`) for `symbol_map.json` and `lot_rules.json`.
  - `web/index.html` provides a responsive dark-mode interface optimized for mobile browsers (iOS Safari / Android Chrome) with card-based layouts, decimal keypad triggers, and touch-friendly controls.
- **Broker Server Discovery Script**:
  - Added `search_servers.sh` to scan MT5 terminal data inside Docker containers and locate exact broker server names.
- **Automated Unit Test Suite**:
  - Added `tests/test_web_api.py` covering Web API endpoints, Basic Auth, hot-reloading, Stealth SL/TP, and proportional partial close logic.

### Changed
- **Source-Symbol Lot Sizing Priority**:
  - `calculate_target_lot()` in `copier_hub.py` now checks `lot_rules.json` for the original Vantage source symbol first (e.g., `XAUUSD247` at `0.25x` vs. `XAUUSD.tv` at `25x`) before falling back to the mapped FTMO target symbol (`XAUUSD`), allowing symbols with different contract sizes on the master account to share the same target symbol on FTMO with distinct multipliers.
- **Stealth SL/TP Safeguard (Cross-Broker Quote Offset Immunity)**:
  - `copier_hub.py` now enforces `0;0` for `sl` and `tp` on all `OPEN` commands sent to FTMO, preventing `Invalid Stops` rejections or premature stop-outs caused by price feed offsets between brokers when using pre-set SL/TP or pending orders on Vantage. All exits remain synchronized via `CLOSE` commands when Vantage closes the position.

---

## [v1.0] - Initial Release

### Added
- Dual MT5 Docker container architecture (`mt5-vantage` Master & `mt5-ftmo` Slave).
- Low-latency raw Winsock (`ws2_32.dll`) TCP communication between `VantageSender.mq5`, `copier_hub.py`, and `FTMOReceiver.mq5`.
- Configurable symbol mapping (`symbol_map.json`) and multi-tier lot sizing rules (`lot_rules.json` supporting `fixed_lot`, `lot_map`, and `multiplier`).
