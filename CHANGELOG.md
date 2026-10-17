# Changelog

All notable changes to this project will be documented in this file.

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
- **Stealth SL/TP Safeguard (Cross-Broker Quote Offset Immunity)**:
  - `copier_hub.py` now enforces `0;0` for `sl` and `tp` on all `OPEN` commands sent to FTMO, preventing `Invalid Stops` rejections or premature stop-outs caused by price feed offsets between brokers when using pre-set SL/TP or pending orders on Vantage. All exits remain synchronized via `CLOSE` commands when Vantage closes the position.

---

## [v1.0] - Initial Release

### Added
- Dual MT5 Docker container architecture (`mt5-vantage` Master & `mt5-ftmo` Slave).
- Low-latency raw Winsock (`ws2_32.dll`) TCP communication between `VantageSender.mq5`, `copier_hub.py`, and `FTMOReceiver.mq5`.
- Configurable symbol mapping (`symbol_map.json`) and multi-tier lot sizing rules (`lot_rules.json` supporting `fixed_lot`, `lot_map`, and `multiplier`).
