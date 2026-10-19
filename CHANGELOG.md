# Changelog

All notable changes to this project will be documented in this file.

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
- **Zero-Overhead VPS System Load Strip**:
  - Added `get_vps_metrics()` in `copier_hub.py` with a 5-second in-memory cache reading Linux `/proc/stat`, `/proc/meminfo`, `/proc/uptime`, and `shutil.disk_usage("/")` directly without spawning external subprocesses.
  - Added a mobile-friendly, 1-line non-sticky collapsible VPS status strip (`CPU`, `RAM`, `Swap`, `Disk`, `Uptime`) to `web/index.html` with Page Visibility API (`document.hidden`) auto-pause when the browser tab is in the background.

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
