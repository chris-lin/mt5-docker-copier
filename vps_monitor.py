import os
import shutil
import threading
import time

# Lightweight VPS System Load Monitor (Zero-Dependency, 5s Cached)
_metrics_lock = threading.Lock()
_metrics_cache_time = 0.0
_metrics_cache_data = None
_prev_cpu_times = None
_PROCESS_START_TIME = time.time()
SYSTEM_METRICS_TTL = 5.0


def _format_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _read_linux_cpu_times():
    """Read aggregate CPU jiffies from /proc/stat -> (idle_all, total)."""
    with open("/proc/stat", "r", encoding="utf-8") as f:
        first = f.readline()
    parts = first.split()
    if not parts or parts[0] != "cpu":
        return None
    values = [int(x) for x in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return idle, total


def get_vps_metrics(force: bool = False) -> dict:
    """Return cached host CPU, RAM, Swap, Disk, and Uptime metrics without spawning subprocesses."""
    global _metrics_cache_time, _metrics_cache_data, _prev_cpu_times
    now = time.time()
    with _metrics_lock:
        if not force and _metrics_cache_data is not None and (now - _metrics_cache_time) < SYSTEM_METRICS_TTL:
            return _metrics_cache_data

        cpu_cores = os.cpu_count() or 1
        try:
            load_1m, load_5m, _ = os.getloadavg()
        except Exception:
            load_1m, load_5m = 0.0, 0.0

        # 1. CPU %
        cpu_percent = min(100.0, round((load_1m / max(1, cpu_cores)) * 100.0, 1))
        if os.path.exists("/proc/stat"):
            try:
                cur_cpu = _read_linux_cpu_times()
                if cur_cpu and _prev_cpu_times:
                    idle_delta = cur_cpu[0] - _prev_cpu_times[0]
                    total_delta = cur_cpu[1] - _prev_cpu_times[1]
                    if total_delta > 0:
                        cpu_percent = round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)
                _prev_cpu_times = cur_cpu
            except Exception:
                pass

        # 2. RAM & Swap from /proc/meminfo
        ram_total_gb = 0.0
        ram_used_gb = 0.0
        ram_percent = 0.0
        swap_total_gb = 0.0
        swap_used_gb = 0.0
        swap_percent = 0.0

        if os.path.exists("/proc/meminfo"):
            try:
                meminfo = {}
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    for line in f:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            val_parts = v.strip().split()
                            if val_parts and val_parts[0].isdigit():
                                meminfo[k.strip()] = int(val_parts[0])  # kB
                mem_total_kb = meminfo.get("MemTotal", 0)
                mem_avail_kb = meminfo.get(
                    "MemAvailable",
                    meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)
                )
                if mem_total_kb > 0:
                    mem_used_kb = max(0, mem_total_kb - mem_avail_kb)
                    ram_total_gb = round(mem_total_kb / (1024 * 1024), 2)
                    ram_used_gb = round(mem_used_kb / (1024 * 1024), 2)
                    ram_percent = round((mem_used_kb / mem_total_kb) * 100.0, 1)

                swap_total_kb = meminfo.get("SwapTotal", 0)
                swap_free_kb = meminfo.get("SwapFree", 0)
                if swap_total_kb > 0:
                    swap_used_kb = max(0, swap_total_kb - swap_free_kb)
                    swap_total_gb = round(swap_total_kb / (1024 * 1024), 2)
                    swap_used_gb = round(swap_used_kb / (1024 * 1024), 2)
                    swap_percent = round((swap_used_kb / swap_total_kb) * 100.0, 1)
            except Exception:
                pass

        # 3. Disk Usage (/)
        disk_total_gb = 0.0
        disk_used_gb = 0.0
        disk_percent = 0.0
        try:
            du = shutil.disk_usage("/")
            if du.total > 0:
                disk_total_gb = round(du.total / (1024 ** 3), 1)
                disk_used_gb = round(du.used / (1024 ** 3), 1)
                disk_percent = round((du.used / du.total) * 100.0, 1)
        except Exception:
            pass

        # 4. System Uptime
        uptime_secs = now - _PROCESS_START_TIME
        if os.path.exists("/proc/uptime"):
            try:
                with open("/proc/uptime", "r", encoding="utf-8") as f:
                    uptime_secs = float(f.readline().split()[0])
            except Exception:
                pass

        _metrics_cache_data = {
            "cpu_percent": cpu_percent,
            "cpu_cores": cpu_cores,
            "load_1m": round(load_1m, 2),
            "load_5m": round(load_5m, 2),
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "ram_percent": ram_percent,
            "swap_used_gb": swap_used_gb,
            "swap_total_gb": swap_total_gb,
            "swap_percent": swap_percent,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "disk_percent": disk_percent,
            "uptime_str": _format_uptime(uptime_secs)
        }
        _metrics_cache_time = now
        return _metrics_cache_data
