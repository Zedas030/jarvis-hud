"""
Live figures of this PC for the HUD's top strip — polled every two seconds, so
nothing here may block: CPU and network come from psutil counters (difference to
the previous call), the GPU from one nvidia-smi call.

  sample() -> {cpu, ram, gpu, vram, gpu_temp, net_down, net_up}   (percent, °C, Mbit/s)

Network is this PC's own traffic over all real adapters (not the whole household —
that is the FRITZ!Box figure in the Homelab tile).
"""
import threading
import time

_LOCK = threading.Lock()
_PREV = {"t": 0.0, "rx": 0, "tx": 0, "busy": 0.0, "total": 0.0}
_GPU = {"t": 0.0, "row": None}
GPU_EVERY_S = 1.5    # nvidia-smi takes ~0.1 s; two HUDs polling must not double that


def _net():
    import psutil
    rx = tx = 0
    for name, c in psutil.net_io_counters(pernic=True).items():
        low = name.lower()
        if "loopback" in low or low.startswith(("lo", "vethernet", "vmware", "virtualbox")):
            continue
        rx += c.bytes_recv
        tx += c.bytes_sent
    return rx, tx


def _gpu():
    if time.time() - _GPU["t"] > GPU_EVERY_S:
        import infra
        rows = infra.gpu()
        _GPU.update(t=time.time(), row=rows[0] if rows else None)
    return _GPU["row"]


def _cpu_times():
    """(busy, total) seconds over all cores. Own snapshot instead of psutil.cpu_percent():
    that keeps one module-wide baseline, and the dashboard's call in between made it 0 %."""
    import psutil
    t = psutil.cpu_times()
    total = sum(t)
    return total - t.idle, total


def sample():
    import psutil
    with _LOCK:
        now = time.time()
        rx, tx = _net()
        busy, total = _cpu_times()
        cpu = None
        if _PREV["total"] and total > _PREV["total"]:
            cpu = max(0.0, min(100.0, (busy - _PREV["busy"]) * 100 / (total - _PREV["total"])))
        dt = now - _PREV["t"]
        down = up = None
        if _PREV["t"] and 0 < dt < 60:
            down = max(0, rx - _PREV["rx"]) * 8 / dt / 1e6
            up = max(0, tx - _PREV["tx"]) * 8 / dt / 1e6
        _PREV.update(t=now, rx=rx, tx=tx, busy=busy, total=total)
        g = _gpu()
        mem = psutil.virtual_memory()
        return {
            "cpu": cpu, "cores": psutil.cpu_count(),
            "ram": mem.percent, "ram_used": mem.used / 2 ** 30, "ram_total": mem.total / 2 ** 30,
            "gpu": g["util"] if g else None, "gpu_name": g["name"] if g else None,
            "vram": round(g["vram_used"] * 100 / max(g["vram_total"], 1)) if g else None,
            "vram_used": g["vram_used"] / 1024 if g else None, "vram_total": g["vram_total"] / 1024 if g else None,
            "gpu_temp": g["temp"] if g else None,
            "net_down": down, "net_up": up,
        }
