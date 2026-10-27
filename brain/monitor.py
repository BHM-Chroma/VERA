"""Мониторинг ПК: CPU, RAM, видеокарта NVIDIA (через nvidia-smi)."""
import shutil
import subprocess
import sys
import time

import psutil

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0   # не мигать консолью


class Monitor:
    def __init__(self, cfg):
        self.cfg = cfg["monitor"]
        self.stats = {}
        self._nvsmi = shutil.which("nvidia-smi")
        self._high_since = {}
        self._last_alert = {}
        psutil.cpu_percent(None)

    def sample(self):
        s = {
            "cpu": psutil.cpu_percent(None),
            "ram": psutil.virtual_memory().percent,
        }
        gpu = self._gpu()
        if gpu:
            s.update(gpu)
        bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if bat:
            s["battery"] = round(bat.percent)
            s["plugged"] = bool(bat.power_plugged)
        self.stats = s
        return s

    def _gpu(self):
        if not self._nvsmi:
            return None
        try:
            out = subprocess.run(
                [self._nvsmi, "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW,
            ).stdout.strip().splitlines()[0]
            util, temp, used, total = (float(x) for x in out.split(","))
            return {"gpu": util, "gpu_temp": temp, "vram": round(used / total * 100)}
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return None

    @staticmethod
    def top_process(by="cpu"):
        procs = []
        for p in psutil.process_iter(["name"]):
            try:
                if by == "cpu":
                    p.cpu_percent(None)
                procs.append(p)
            except psutil.Error:
                pass
        if by == "cpu":
            time.sleep(0.5)
        best, best_v = None, -1.0
        for p in procs:
            try:
                v = p.cpu_percent(None) if by == "cpu" else p.memory_info().rss
                name = p.info["name"] or ""
                if name.lower() in ("system idle process", "idle") or v <= best_v:
                    continue
                best, best_v = name, v
            except psutil.Error:
                pass
        return best

    def check_alerts(self):
        """Возвращает список тревог (текстом), если что-то перегружено долгое время."""
        c, s, now = self.cfg, self.stats, time.time()
        checks = [
            ("cpu", s.get("cpu", 0) >= c["cpu_alert"]),
            ("ram", s.get("ram", 0) >= c["ram_alert"]),
            ("gpu_temp", s.get("gpu_temp", 0) >= c["gpu_temp_alert"]),
        ]
        alerts = []
        for key, high in checks:
            if not high:
                self._high_since.pop(key, None)
                continue
            since = self._high_since.setdefault(key, now)
            if now - since < c["alert_after_sec"]:
                continue
            if now - self._last_alert.get(key, 0) < c["alert_cooldown_sec"]:
                continue
            self._last_alert[key] = now
            alerts.append(self._alert_text(key))
        return alerts

    def _alert_text(self, key):
        s = self.stats
        if key == "cpu":
            top = self.top_process("cpu")
            return f"Процессор загружен на {s['cpu']:.0f}%!" + (f" Больше всех ест {top}." if top else "")
        if key == "ram":
            top = self.top_process("ram")
            return f"Память занята на {s['ram']:.0f}%." + (f" Больше всех — {top}." if top else "")
        return f"Видеокарта нагрелась до {s['gpu_temp']:.0f}°C. Может, дать ей отдохнуть?"

    def describe(self):
        s = self.stats or self.sample()
        parts = [f"Процессор {s['cpu']:.0f}%", f"память {s['ram']:.0f}%"]
        if "gpu" in s:
            parts.append(f"видеокарта {s['gpu']:.0f}% и {s['gpu_temp']:.0f}°C")
        if "battery" in s:
            parts.append(f"батарея {s['battery']}%" + (" (заряжается)" if s.get("plugged") else ""))
        return ", ".join(parts) + "."
