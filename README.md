# Headless MT5 Docker Copier

## Overview

A lightweight, low-latency, cross-broker automated copy trading system running on Linux via Docker and Wine. This project bridges two MetaTrader 5 (MT5) instances in headless mode using a central Python TCP hub and a mobile-friendly Web Dashboard.

While the default container names use `mt5_vantage` (Master / Signal Source) and `mt5_ftmo` (Slave / Execution Receiver), **v2.0** supports one-click switching across multiple Master brokers (e.g., **Vantage**, **Exness**) and Slave prop firms (e.g., **FTMO**, **FundedNext**).

---

## Architecture & Key Features

* **Headless Docker Environment**: Runs MT5 instances inside minimal Linux containers via Wine without requiring an X11 server or GUI.
* **Web Dashboard & Hot-Reload**: Built-in, zero-dependency browser management interface (`http://127.0.0.1:8080`) optimized for desktop and mobile browsers with live EA status indicators and HTTP Basic Auth security.
* **Lucid Trading Parallel Futures Copier (`v3.0`)**: Broadcasts whitelisted trades (`MGC`/`GC` Gold, `MNQ`/`NQ` Nasdaq, `MBT`/`BTC` Bitcoin) in parallel to **Lucid Trading** (via Tradovate Direct REST API or Webhook Bridge) with automatic integer contract rounding and Netting partial/full close tracking—without needing a third Docker container.
* **Zero-Overhead VPS Load Monitor (`v3.0`)**: 1-line collapsible host resource bar (`CPU`, `RAM`, `Swap`, `Disk`, `Uptime`) powered by 5-second cached `/proc` reads and background tab auto-pause (`document.hidden`).
* **Multi-Broker Profile Presets (`v2.0`)**: Bundle `symbol_map` and `lot_rules` into named presets (`Vantage -> FTMO`, `Vantage -> FundedNext`, `Exness -> FTMO`, `Exness -> FundedNext`) stored in `profiles/presets.json` for instant, zero-downtime hot-swapping.
* **Dual-Side MT5 Account Switcher (`v2.0`)**: Manage and switch both **Master (Sender)** and **Slave (Receiver)** MT5 accounts directly from the Web UI (`👤 Accounts` tab). Automatically generates UTF-16LE `mt5-server.ini` files, activates linked profile presets, and restarts the target Docker container in the background.
* **Decoupled Mapping & Sizing Engine**:
  * **Symbol Mapping**: Resolves contract naming discrepancies across brokers (e.g., `NAS100.r` -> `US100.cash`, `XAUUSDm` -> `XAUUSD`).
  * **Source-Symbol Priority Lot Sizing**: Evaluates rules by **Source Symbol first**, then falls back to **Target Symbol**, allowing instruments with different contract sizes on the master broker (e.g., `XAUUSD247` at `0.25x` and `XAUUSD.tv` at `25x`) to map to the same target symbol (`XAUUSD`) with distinct multipliers.
* **Execution Safeguards (`v1.1`)**:
  * **Proportional Partial Close (`PARTIAL`)**: Detects partial volume reductions on the master account and closes the exact proportional ratio on the slave account, using dual-anchor tracking (`POSITION_MAGIC` + `POSITION_COMMENT`) so positions stay linked even after MT5 clears comments on partial closes.
  * **Stealth SL/TP (Quote Offset Immunity)**: Sends `0;0` for SL/TP on initial `OPEN` commands to prevent `Invalid Stops` rejections or premature stop-outs caused by cross-broker spread/quote offsets, while synchronizing all exits in real time via `CLOSE` commands.
  * **Restart Idempotency Guard**: Verifies existing open positions by `POSITION_MAGIC` / `Src:{ticket}` before executing `OPEN` commands to prevent duplicate trades when `copier_hub.py` restarts or reconnects.

---

## Repository Structure

```text
mt5-docker-copier/
├── .gitignore
├── README.md
├── CHANGELOG.md                   # Release history (v1.0 -> v3.0)
├── docker-compose.yml.example     # Docker Compose template
├── setup_firewall.sh              # Automated host firewall configuration
├── search_servers.sh              # Headless broker server search automation
├── copier_hub.py                  # Python TCP routing hub & Web Dashboard (v3.0)
├── config_templates/              # Tracked configuration templates
│   ├── symbol_map.example.json    # Template for symbol_map.json
│   ├── lot_rules.example.json     # Template for lot_rules.json
│   ├── presets.example.json       # Template for multi-broker profile presets
│   ├── accounts.example.json      # Template for Master/Slave MT5 credentials
│   ├── lucid.example.json         # Template for Lucid Trading parallel copier
│   ├── mt5-server.ini.example     # MT5 startup config template
│   └── common.ini.example         # Global MT5 flags (AllowDllImport, ExpertsEnable)
├── web/
│   ├── index.html                 # Dashboard HTML layout
│   ├── style.css                  # Responsive desktop & mobile stylesheet
│   └── app.js                     # Dashboard frontend application logic
├── mql5/
│   ├── VantageSender.mq5          # Master Sender EA (Winsock-based)
│   └── FTMOReceiver.mq5           # Slave Receiver EA (Winsock + Dual-Anchor Tracking)
└── tests/
    └── test_web_api.py            # Automated unit test suite
```

---

## Configuration Reference

### `symbol_map.json`
Maps source broker symbol names to target broker symbol names:

```json
{
  "NAS100.r": "US100.cash",
  "NAS100": "US100.cash",
  "XAUUSD247": "XAUUSD",
  "XAUUSD.tv": "XAUUSD",
  "US30.r": "US30.cash",
  "GER40.r": "GER40.cash"
}
```

### `lot_rules.json`
Defines lot conversion logic. The engine checks **`SourceSymbol` first**, then falls back to **`TargetSymbol`**. Within a matched rule, evaluation priority is: `fixed_lot` -> `lot_map` -> `multiplier` -> `default_multiplier`.

```json
{
  "default_multiplier": 1.0,
  "min_lot": 0.01,

  "rules": {
    "XAUUSD247": {
      // Source-symbol specific override (e.g., 0.25x for XAUUSD247 -> XAUUSD)
      "multiplier": 0.25
    },
    "XAUUSD.tv": {
      // Source-symbol specific override (e.g., 25x for mini-contract XAUUSD.tv -> XAUUSD)
      "multiplier": 25.0
    },
    "XAUUSD": {
      // Target-symbol fallback (applies when trading regular XAUUSD)
      "multiplier": 1.0
    },
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

You can access the dashboard securely using either of the following two methods:

#### Method A: Local Access via SSH Tunnel (Recommended for Desktop)
Forward port `8080` from your VPS to your local computer:

```bash
ssh -L 8080:127.0.0.1:8080 user@YOUR_VPS_IP
```

Then open your local browser at:
```text
http://localhost:8080
```

#### Method B: Remote Access on Any Device via Cloudflare Tunnel (Recommended for Mobile)
To securely access and configure the dashboard from any smartphone, tablet, or external computer without exposing ports to the public internet:
1. Connect `127.0.0.1:8080` using Cloudflare Zero Trust Tunnels to your custom domain (e.g., `copier.your-domain.com`).
2. Access `https://copier.your-domain.com` directly from any mobile browser (iOS Safari, Android Chrome).
3. The interface features touch-optimized cards, decimal keypads, iOS auto-zoom prevention, and PWA "Add to Home Screen" support.

* **Default Credentials**:
  * Username: `admin`
  * Password: `admin123` *(changeable in the dashboard's "Security" tab or via `WEB_USER` / `WEB_PASS` env variables)*
* **Key Capabilities**:
  * **Real-time Status Badges**: Live indicators showing whether `copier_hub.py` is running, which Master/Slave accounts are currently active, and whether EAs are connected.
  * **Active Profile Switcher Bar (`v2.0`)**: One-click switching (`⚡ Apply Profile`) and saving (`💾 Save Preset`) of `(symbol_map + lot_rules)` broker presets (`Vantage -> FTMO`, `Vantage -> FundedNext`, `Exness -> FTMO`, `Exness -> FundedNext`).
  * **Dual-Side MT5 Account Switcher (`v2.0`)**: Dedicated **`👤 Accounts`** tab to manage and switch both **Master (Sender)** and **Slave (Receiver)** MT5 accounts (`⚡ Switch & Restart`), automatically updating `mt5-server.ini`, applying linked profile presets, and restarting the target Docker container.
  * **Visual Configuration & Hot-Reload**: Add, edit, and delete symbol mappings (`symbol_map.json`) and lot sizing rules (`lot_rules.json`) with immediate in-memory hot-reload and automatic timestamped backups in `backups/`.
  * **Live Activity Console**: Stream real-time trade signals (`OPEN`, `PARTIAL`, `CLOSE`) and connection logs.

---

## Verification & Monitoring

* **Run Automated Unit Tests**:
  ```bash
  python3 -m unittest discover -s tests -v
  ```
* **View Python Hub Logs**: Inspect connection status, volume mapping calculations, and dispatched execution commands in real time directly from the terminal running `copier_hub.py`.
* **View Container Logs**:
  ```bash
  docker compose logs -f
  ```
* **Verify MT5 Execution**: Inspect terminal trade history and journal logs within `ftmo_data/MQL5/Logs/` or directly from your MT5 mobile application.


## License

MIT License - see LICENSE file for details.