# Headless MT5 Docker Copier

## Overview

A lightweight, low-latency, cross-broker automated copy trading system running on Linux via Docker and Wine. This project demonstrates bridging two MetaTrader 5 (MT5) instances in headless mode using a central Python TCP hub.

The reference configuration uses **Vantage** as the Master (Signal Source) and **FTMO** as the Slave (Execution Receiver).

---

## Architecture & Key Features

* **Headless Docker Environment**: Runs MT5 instances inside minimal Linux containers via Wine without requiring an X11 server or GUI.
* **Decoupled Mapping Engine**:
  * **Symbol Mapping**: Resolves contract naming discrepancies across brokers (e.g., `NAS100.r` -> `US100.cash`).
  * **Flexible Lot Sizing**: Supports exact ladder maps, symbol-specific multipliers, fixed lots, and fallback global multipliers.

---

## Repository Structure

```text
mt5-docker-copier/
├── .gitignore
├── README.md
├── docker-compose.yml.example     # Docker Compose template
├── setup_firewall.sh              # Automated host firewall configuration
├── copier_hub.py                  # Python TCP routing hub
├── lot_rules.json                 # Lot size conversion rules
├── symbol_map.json                # Cross-broker symbol map
├── mql5/
│   ├── VantageSender.mq5          # Sender EA (Winsock-based)
│   └── FTMOReceiver.mq5           # Receiver EA (Winsock + SymbolSelect)
└── config_templates/
    ├── mt5-server.ini.example     # MT5 startup config template
    ├── servers.dat                # Broker servers list
    └── common.ini                 # Global MT5 flags (AllowDllImport, ExpertsEnable)
```

---

## Configuration Reference

### `symbol_map.json`
Maps source broker symbol names to target broker symbol names:

```json
{
  // Format: "SourceSymbol": "TargetSymbol"
  "NAS100.r": "US100.cash",
  "NAS100": "US100.cash",
  "XAUUSD247": "XAUUSD",
  "US30.r": "US30.cash",
  "GER40.r": "GER40.cash"
}
```

### `lot_rules.json`
Defines lot conversion logic keyed by the target symbol. Evaluation priority: `fixed_lot` -> `lot_map` -> `multiplier` -> `default_multiplier`.

```json
{
  // Global settings
  "default_multiplier": 1.0,
  "min_lot": 0.01,

  "rules": {
    "US100.cash": {
      // Exact ladder matching: "SourceLot": TargetLot
      "lot_map": {
        "0.1": 0.2,
        "0.2": 0.4,
        "0.5": 1.0,
        "1.0": 2.0
      },
      "default_multiplier": 1.0
    },
    "XAUUSD": {
      // Proportional multiplier
      "multiplier": 1.5
    },
    "EURUSD": {
      // Fixed lot size regardless of master volume
      "fixed_lot": 0.5
    }
  }
}
```

---

## Step-by-Step Setup Guide

### 1. Prepare MT5 Installation Folders
Copy an existing, initialized Windows MT5 terminal installation into two local directories:

```bash
mkdir -p vantage_data ftmo_data
cp -r /path/to/windows/mt5/* vantage_data/
cp -r /path/to/windows/mt5/* ftmo_data/
```

### 2. Configure MT5 Terminal Settings

#### `mt5-server.ini`
Place a tailored `mt5-server.ini` inside each instance's root folder.

Example for FTMO (`ftmo_data/mt5-server.ini`):
```ini
[Common]
Server=FTMO-Demo
Login=YOUR_LOGIN
Password=YOUR_PASSWORD
News=0
Experts=1

[Experts]
Enabled=1
AllowLiveTrading=1
AllowDllImport=1

[StartUp]
Expert=FTMOReceiver
Symbol=XAUUSD
Period=M1
EnableTrading=1
```
*(Repeat configuration for `vantage_data/mt5-server.ini` with `Expert=VantageSender` and Vantage broker credentials).*

#### `common.ini`
Ensure `common.ini` in each data directory enables DLLs and Automated Trading:

```ini
[Common]
News=0
Experts=1
AllowDllImport=1
ExpertsEnable=1
```

### 3. Compile and Deploy EAs
1. Open MetaEditor on Windows.
2. Compile `mql5/VantageSender.mq5` to produce `VantageSender.ex5`.
3. Compile `mql5/FTMOReceiver.mq5` to produce `FTMOReceiver.ex5`.
4. Deploy the compiled binaries:
   ```bash
   cp VantageSender.ex5 vantage_data/MQL5/Experts/
   cp FTMOReceiver.ex5 ftmo_data/MQL5/Experts/
   ```

### 4. Configure Host Firewall
Allow incoming traffic from internal Docker bridge interfaces (`docker0` and `br-+`):

```bash
chmod +x setup_firewall.sh
sudo ./setup_firewall.sh
```

### 5. Start Docker Containers
Prepare your `docker-compose.yml` from `docker-compose.yml.example`, then build and run:

```bash
docker build -t mt5-headless .
docker compose up -d
```

### 6. Start the TCP Bridge
Launch the central router on the host machine:

```bash
python3 copier_hub.py
```

Once the containers start up, you will see `[CONNECTED]` events from both the Vantage sender (port 9001) and the FTMO receiver (port 9002).

---

## Verification & Monitoring

* **View Python Hub Logs**: Inspect connection status, volume mapping calculations, and dispatched execution commands in real time directly from the terminal running `copier_hub.py`.
* **View Container Logs**:
  ```bash
  docker compose logs -f
  ```
* **Verify MT5 Execution**: Inspect terminal trade history and journal logs within `ftmo_data/MQL5/Logs/` or directly from your MT5 mobile application.


## License

MIT License - see LICENSE file for details.