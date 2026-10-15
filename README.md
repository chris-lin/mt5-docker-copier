# Headless MT5 Docker Copier

## Overview

A lightweight, low-latency, cross-broker automated copy trading system running on Linux via Docker and Wine. This project demonstrates bridging two MetaTrader 5 (MT5) instances in headless mode using a central Python TCP hub.

The reference configuration uses **Vantage** as the Master (Signal Source) and **FTMO** as the Slave (Execution Receiver).

---

## Architecture & Key Features

* **Headless Docker Environment**: Runs MT5 instances inside minimal Linux containers via Wine without requiring an X11 server or GUI.
* **Web Dashboard & Hot-Reload**: Built-in, zero-dependency browser management interface (`http://127.0.0.1:8080`) to configure symbol mappings and lot rules dynamically with immediate in-memory hot-reload, live EA status indicators, and Basic Auth security.
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
├── search_servers.sh              # Headless broker server search automation
├── copier_hub.py                  # Python TCP routing hub & Web Dashboard
├── lot_rules.json                 # Lot size conversion rules
├── symbol_map.json                # Cross-broker symbol map
├── web/
│   └── index.html                 # Single-page Web Dashboard
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

#### Discover Broker Servers (`search_servers.sh`)
> [!IMPORTANT]
> If you want to connect to a specific broker's server, please make sure to search for the broker server first (e.g., `./search_servers.sh Vantage`), otherwise MT5 might fail to connect.
>
> In headless mode, MT5 requires broker network endpoints and server definitions to be cached in `servers.dat`. When connecting to a broker for the first time, use `search_servers.sh` while the container is running to trigger GUI-based server search automation via Xvfb:
>
> ```bash
> # Copy script into container (if not already mounted)
> docker cp search_servers.sh mt5_vantage:/mt5/
>
> # Run server discovery for the target broker
> docker exec -it mt5_vantage ./search_servers.sh Vantage
> ```
> *(Repeat for FTMO or other brokers as needed, e.g., `docker exec -it mt5_ftmo ./search_servers.sh FTMO`).*


### 6. Start the TCP Bridge
Launch the central router on the host machine:

```bash
python3 copier_hub.py
```

Once the containers start up, you will see `[CONNECTED]` events from both the Vantage sender (port 9001) and the FTMO receiver (port 9002).

### 7. Access Web Dashboard (Live Monitoring & Dynamic Config)
`copier_hub.py` includes a built-in web management interface running on `http://127.0.0.1:8080`.

#### Accessing from Local Machine (Cloud VPS via SSH Tunnel)
Because the dashboard listens securely on `127.0.0.1:8080`, forward the port to your local machine using an SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 user@YOUR_VPS_IP
```

Then open your local browser at:
```text
http://localhost:8080
```

* **Default Credentials**:
  * Username: `admin`
  * Password: `admin123` *(changeable in the dashboard's "Security" tab or via `WEB_USER` / `WEB_PASS` env variables)*
* **Key Capabilities**:
  * **Real-time Status Badges**: Live indicators showing whether `copier_hub.py` is running and whether Vantage/FTMO EAs are actively connected.
  * **Visual Configuration**: Add, edit, and delete symbol mappings (`symbol_map.json`) and lot sizing rules (`lot_rules.json`) directly with form validation.
  * **Immediate In-Memory Hot-Reload**: Saving updates the files and reloads configuration into memory instantly without restarting the hub or interrupting trades.
  * **Automatic Timestamped Backups**: Automatically creates `.json` backups in `backups/` before applying any changes.
  * **Live Activity Console**: Stream real-time trade signals and connection logs.

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