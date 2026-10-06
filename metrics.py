"""Linux resource sampling and sustained, rate-limited warning decisions."""

from dataclasses import dataclass
from pathlib import Path


def cpu_counters(text):
    fields = text.splitlines()[0].split()
    if fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("Missing aggregate CPU counters")
    # Guest time is already included in user/nice: do not count it twice.
    values = [int(value) for value in fields[1:9]]
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def cpu_percent(previous, current):
    total = current[0] - previous[0]
    idle = current[1] - previous[1]
    if total <= 0 or idle < 0:
        return None
    return max(0.0, min(100.0, 100.0 * (total - idle) / total))


def memory_usage(text):
    values = {}
    for line in text.splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.split()[0])
    total = values["MemTotal"]
    available = values["MemAvailable"]
    if total <= 0:
        raise ValueError("Invalid total memory")
    used = max(0, min(total, total - available))
    swap_total = max(0, values.get("SwapTotal", 0))
    swap_used = max(0, min(swap_total, swap_total - values.get("SwapFree", 0)))
    swap_percent = 100.0 * swap_used / swap_total if swap_total else None
    return 100.0 * used / total, used, total, available, swap_used, swap_total, swap_percent


def level_for(value, warning, critical):
    if value is None:
        return 0
    return 2 if value >= critical else 1 if value >= warning else 0


@dataclass
class AlertState:
    warning: float = 80
    critical: float = 95
    sustain_seconds: float = 10
    repeat_seconds: float = 300
    hysteresis: float = 5
    warning_since: float | None = None
    critical_since: float | None = None
    notified_level: int = 0
    last_notice: float = float("-inf")

    def update(self, value, now):
        if value is None:
            # A gap in sampling must not count as sustained usage.
            self.warning_since = self.critical_since = None
            return 0
        level = level_for(value, self.warning, self.critical)
        if level >= 1:
            if self.warning_since is None:
                self.warning_since = now
        else:
            self.warning_since = None
        if level == 2:
            if self.critical_since is None:
                self.critical_since = now
        else:
            self.critical_since = None
        if value < self.warning - self.hysteresis:
            self.notified_level = 0
        candidate = 0
        if self.critical_since is not None and now - self.critical_since >= self.sustain_seconds:
            candidate = 2
        elif self.warning_since is not None and now - self.warning_since >= self.sustain_seconds:
            candidate = 1
        if candidate and (candidate > self.notified_level or now - self.last_notice >= self.repeat_seconds):
            self.notified_level = candidate
            self.last_notice = now
            return candidate
        return 0


class Sampler:
    def __init__(self):
        self.previous_cpu = cpu_counters(Path("/proc/stat").read_text())

    def sample(self):
        current = cpu_counters(Path("/proc/stat").read_text())
        cpu = cpu_percent(self.previous_cpu, current)
        self.previous_cpu = current
        ram, used, total, available, swap_used, swap_total, swap = memory_usage(Path("/proc/meminfo").read_text())
        return {
            "cpu": cpu,
            "ram": ram,
            "swap": swap,
            "used_kib": used,
            "total_kib": total,
            "available_kib": available,
            "swap_used_kib": swap_used,
            "swap_total_kib": swap_total,
            "swap_available_kib": swap_total - swap_used,
        }
