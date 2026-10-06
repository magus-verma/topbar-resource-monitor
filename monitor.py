#!/usr/bin/python3
"""CPU/RAM/swap percentages and coloured status icons in Ubuntu's top bar."""

import argparse
import fcntl
import json
import logging
import math
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from metrics import AlertState, Sampler, level_for

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "topbar-resource-monitor/config.json"
DEFAULTS = {
    "warning_percent": 80,
    "critical_percent": 95,
    "sustain_seconds": 10,
    "repeat_seconds": 300,
    "poll_seconds": 2,
}
LOG = logging.getLogger("topbar-resource-monitor")
RESOURCES = ("CPU", "RAM", "SWAP")


def load_config(path=CONFIG_FILE):
    config = DEFAULTS.copy()
    if path.exists():
        custom = json.loads(path.read_text())
        if not isinstance(custom, dict) or set(custom) - set(DEFAULTS):
            raise ValueError("Config must be an object containing the documented settings")
        config.update(custom)
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in config.values()
    ):
        raise ValueError("Settings must be finite numbers")
    if not (0 < config["warning_percent"] < config["critical_percent"] <= 100):
        raise ValueError("Thresholds must satisfy 0 < warning < critical <= 100")
    if any(
        config[key] < minimum for key, minimum in [("sustain_seconds", 2), ("repeat_seconds", 60), ("poll_seconds", 1)]
    ):
        raise ValueError("Timing settings are too short")
    if not isinstance(config["poll_seconds"], int):
        raise ValueError("poll_seconds must be a whole number")
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true", help="Print a real sample without opening the tray")
    args = parser.parse_args()
    config = load_config()
    if args.sample:
        sampler = Sampler()
        time.sleep(1)
        print(json.dumps(sampler.sample(), indent=2))
        return

    # Import desktop libraries only for the live tray process.
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("AyatanaAppIndicator3", "0.1")
    gi.require_version("Notify", "0.7")
    from gi.repository import AyatanaAppIndicator3 as Indicator
    from gi.repository import GLib, Gtk, Notify

    # GLibUnix is available on current systems; GLib is the older fallback.
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix

        add_signal = GLibUnix.signal_add
    except (ValueError, ImportError):
        add_signal = GLib.unix_signal_add

    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".cache"))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock = (runtime_dir / "topbar-resource-monitor.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    Notify.init("CPU, RAM and Swap Monitor")
    Gtk.init([])

    class Monitor:
        def __init__(self):
            self.sampler = Sampler()
            self.indicators = {}
            self.rows = {}
            self.alerts = {}
            self.notifications = {}
            self.muted = False
            self.last_sample = None
            self.last_error = float("-inf")
            for resource in RESOURCES:
                indicator = Indicator.Indicator.new(
                    f"topbar-resource-{resource.lower()}",
                    str(APP_DIR / "icons" / f"{resource.lower()}-normal.svg"),
                    Indicator.IndicatorCategory.SYSTEM_SERVICES,
                )
                indicator.set_title(f"{resource} usage")
                indicator.set_label(f"{resource} --%", f"{resource} 100%")
                indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
                menu = Gtk.Menu()
                detail = Gtk.MenuItem(label="Reading usage…")
                detail.set_sensitive(False)
                menu.append(detail)
                ram_detail = Gtk.MenuItem(label="")
                ram_detail.set_sensitive(False)
                menu.append(ram_detail)
                menu.append(Gtk.SeparatorMenuItem())
                for text in (
                    f"Green: below {config['warning_percent']}% · Amber: {config['warning_percent']}–{config['critical_percent'] - 1}% · Red: {config['critical_percent']}–100%",
                    f"Warn after {config['sustain_seconds']}s; repeat at most every {config['repeat_seconds'] // 60} min",
                ):
                    row = Gtk.MenuItem(label=text)
                    row.set_sensitive(False)
                    menu.append(row)
                mute = Gtk.CheckMenuItem(label="Mute usage warnings")
                mute.connect("toggled", self.toggle_mute)
                menu.append(mute)
                open_monitor = Gtk.MenuItem(label="Open System Monitor")
                open_monitor.connect("activate", self.open_system_monitor)
                menu.append(open_monitor)
                menu.show_all()
                indicator.set_menu(menu)
                self.indicators[resource] = indicator
                self.rows[resource] = (detail, ram_detail, mute)
                self.alerts[resource] = AlertState(
                    warning=config["warning_percent"],
                    critical=config["critical_percent"],
                    sustain_seconds=config["sustain_seconds"],
                    repeat_seconds=config["repeat_seconds"],
                    hysteresis=min(5, config["warning_percent"] / 2),
                )
            GLib.timeout_add_seconds(config["poll_seconds"], self.tick)

        def toggle_mute(self, item):
            self.muted = item.get_active()
            for _, _, mute in self.rows.values():
                if mute.get_active() != self.muted:
                    mute.set_active(self.muted)

        def open_system_monitor(self, *_):
            try:
                for command in ("gnome-system-monitor", "missioncenter"):
                    executable = shutil.which(command)
                    if executable:
                        subprocess.Popen([executable])
                        return
                btop = shutil.which("btop")
                if btop:
                    for terminal, arguments in (
                        ("ptyxis", ["--new-window", "--"]),
                        ("gnome-terminal", ["--"]),
                        ("kgx", ["--"]),
                        ("xterm", ["-e"]),
                    ):
                        executable = shutil.which(terminal)
                        if executable:
                            subprocess.Popen([executable, *arguments, btop])
                            return
                notification = Notify.Notification.new(
                    "System Monitor is not installed",
                    "Install gnome-system-monitor, or btop with a supported terminal.",
                    str(APP_DIR / "icons" / "cpu-unknown.svg"),
                )
                notification.show()
                self.notifications["missing-monitor"] = notification
            except OSError:
                LOG.exception("Could not open System Monitor")

        def notify(self, resource, value, level, sample):
            if self.muted:
                return
            adjective = (
                "almost full" if resource in ("RAM", "SWAP") and level == 2 else "very high" if level == 2 else "high"
            )
            title = f"{resource} usage {adjective}: {round(value)}%"
            body = f"Usage has stayed at or above {config['critical_percent'] if level == 2 else config['warning_percent']}% for {config['sustain_seconds']} seconds. "
            if resource == "RAM":
                body += f"{sample['available_kib'] / 1048576:.1f} GiB available. Close unused apps or browser tabs."
            elif resource == "SWAP":
                body += f"{sample['swap_available_kib'] / 1048576:.1f} GiB swap free. Check System Monitor; close unused apps if RAM usage is also high."
            else:
                body += "Your computer may slow down. Check System Monitor for busy processes."
            notification = self.notifications.get(resource)
            icon = str(APP_DIR / "icons" / f"{resource.lower()}-{'critical' if level == 2 else 'warning'}.svg")
            if notification is None:
                notification = Notify.Notification.new(title, body, icon)
                notification.add_action("open", "Open System Monitor", self.open_system_monitor, None)
                self.notifications[resource] = notification
            else:
                notification.update(title, body, icon)
            notification.set_urgency(Notify.Urgency.CRITICAL if level == 2 else Notify.Urgency.NORMAL)
            notification.set_timeout(Notify.EXPIRES_DEFAULT)
            try:
                notification.show()
                LOG.info("%s", title)
            except GLib.Error:
                LOG.exception("Could not show usage warning")

        def tick(self):
            # Include suspend time so a resumed session starts a fresh warning window.
            now = time.clock_gettime(time.CLOCK_BOOTTIME)
            # Resume after suspend must start a fresh sustained-usage window.
            if self.last_sample is not None and now - self.last_sample > config["poll_seconds"] * 3:
                for alert in self.alerts.values():
                    alert.update(None, now)
            self.last_sample = now
            try:
                sample = self.sampler.sample()
                for resource in RESOURCES:
                    value = sample[resource.lower()]
                    level = level_for(value, config["warning_percent"], config["critical_percent"])
                    state = ("normal", "warning", "critical")[level] if value is not None else "unknown"
                    indicator = self.indicators[resource]
                    label = f"{resource} {int(value)}%" if value is not None else f"{resource} --%"
                    if resource == "SWAP" and sample["swap_total_kib"] == 0:
                        label = "SWAP off"
                    if level:
                        label += " !" if level == 1 else " !!"
                    indicator.set_label(label, f"{resource} 100% !!")
                    indicator.set_icon_full(str(APP_DIR / "icons" / f"{resource.lower()}-{state}.svg"), label)
                    self.rows[resource][0].set_label(
                        f"{resource} usage: {value:.1f}%" if value is not None else f"{resource} sampling…"
                    )
                    if resource == "RAM":
                        self.rows[resource][1].set_label(
                            f"{sample['used_kib'] / 1048576:.1f} / {sample['total_kib'] / 1048576:.1f} GiB used · "
                            f"{sample['available_kib'] / 1048576:.1f} GiB available · {sample['swap_used_kib'] / 1048576:.1f} GiB swap used"
                        )
                    elif resource == "SWAP":
                        if sample["swap_total_kib"]:
                            self.rows[resource][1].set_label(
                                f"{sample['swap_used_kib'] / 1048576:.2f} / {sample['swap_total_kib'] / 1048576:.2f} GiB used · "
                                f"{sample['swap_available_kib'] / 1048576:.2f} GiB free"
                            )
                        else:
                            self.rows[resource][0].set_label("Swap is disabled")
                            self.rows[resource][1].set_label("No swap space configured · usage warnings inactive")
                    else:
                        self.rows[resource][1].set_label(
                            f"Average across all CPU cores · refreshes every {config['poll_seconds']} seconds"
                        )
                    notice = self.alerts[resource].update(value, now)
                    if notice:
                        self.notify(resource, value, notice, sample)
            except (OSError, ValueError, KeyError):
                for resource, indicator in self.indicators.items():
                    indicator.set_label(f"{resource} --%", f"{resource} 100% !!")
                    indicator.set_icon_full(
                        str(APP_DIR / "icons" / f"{resource.lower()}-unknown.svg"), "Usage unavailable"
                    )
                    self.rows[resource][0].set_label("Usage unavailable — waiting for the next sample")
                    self.alerts[resource].update(None, now)
                if now - self.last_error >= 60:
                    LOG.exception("Could not read resource usage")
                    self.last_error = now
            return GLib.SOURCE_CONTINUE

        def stop(self, *_):
            for indicator in self.indicators.values():
                indicator.set_status(Indicator.IndicatorStatus.PASSIVE)
            Notify.uninit()
            Gtk.main_quit()
            return GLib.SOURCE_REMOVE

    monitor = Monitor()
    for signum in (signal.SIGTERM, signal.SIGINT):
        add_signal(GLib.PRIORITY_DEFAULT, signum, monitor.stop)
    LOG.info(
        "CPU, RAM and swap indicators started; warnings at %s%%, critical at %s%%",
        config["warning_percent"],
        config["critical_percent"],
    )
    Gtk.main()


if __name__ == "__main__":
    main()
