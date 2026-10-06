#!/usr/bin/python3
"""Install/remove this application for the current user; never install as root."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "topbar-resource-monitor"
UNIT = f"{NAME}.service"


def paths(home=None):
    home = home or Path.home()
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return {
        "app": home / ".local/share" / NAME,
        "unit": config_root / "systemd/user" / UNIT,
        "config": config_root / NAME / "config.json",
        "backups": home / ".local/state" / NAME / "backups",
    }


def systemd_quote(path):
    text = str(path)
    if "\n" in text or "\r" in text:
        raise ValueError("Installation paths cannot contain newlines")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def service_text(app):
    return f"""[Unit]
Description=CPU, RAM and swap percentages with coloured top-bar usage warnings
PartOf=graphical-session.target
After=graphical-session-pre.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {systemd_quote(app / "monitor.py")}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""


def systemctl(*arguments, check=True):
    return subprocess.run(["systemctl", "--user", *arguments], check=check, capture_output=True, text=True)


def dependencies():
    code = """
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
gi.require_version('Notify', '0.7')
from gi.repository import AyatanaAppIndicator3, Gtk, Notify, GdkPixbuf
import sys
GdkPixbuf.Pixbuf.new_from_file(sys.argv[1])
"""
    result = subprocess.run(
        ["/usr/bin/python3", "-c", code, str(ROOT / "icons/cpu-normal.svg")],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Desktop dependencies are missing. Follow the README dependency instructions.\n" + result.stderr
        )
    if not shutil.which("systemctl"):
        raise RuntimeError("systemctl is unavailable; use --no-start for a manual installation")
    systemctl("show-environment")


def backup_existing(locations):
    existing = {key: locations[key] for key in ("app", "unit", "config") if locations[key].exists()}
    if not existing:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{time.time_ns() % 1000000000:09d}"
    destination = locations["backups"] / stamp
    destination.mkdir(parents=True)
    for key, source in existing.items():
        if source.is_dir():
            shutil.copytree(source, destination / key, symlinks=True)
        else:
            shutil.copy2(source, destination / key)
    return destination


def install(home=None, no_start=False):
    locations = paths(home)
    for key in ("app", "unit"):
        if locations[key].is_symlink():
            raise RuntimeError(f"Refusing to replace a symlink: {locations[key]}")
    sys.path.insert(0, str(ROOT))
    from monitor import load_config

    # Validate existing settings before modifying anything.
    load_config(locations["config"])
    unit_text = service_text(locations["app"])
    if not no_start:
        dependencies()
    backup = backup_existing(locations)
    app = locations["app"]
    app.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{NAME}-install-", dir=app.parent) as temporary:
        staged = Path(temporary) / "new"
        staged.mkdir()
        for filename in ("monitor.py", "metrics.py"):
            shutil.copy2(ROOT / filename, staged / filename)
        shutil.copytree(ROOT / "icons", staged / "icons")
        if not no_start and locations["unit"].exists():
            stopped = systemctl("stop", UNIT, check=False)
            if stopped.returncode and "not loaded" not in stopped.stderr and "not found" not in stopped.stderr:
                raise RuntimeError(stopped.stderr.strip())
        previous = Path(temporary) / "previous"
        if app.exists():
            app.rename(previous)
        try:
            staged.rename(app)
        except OSError:
            if previous.exists():
                previous.rename(app)
            raise
    locations["unit"].parent.mkdir(parents=True, exist_ok=True)
    locations["unit"].write_text(unit_text)
    if not locations["config"].exists():
        locations["config"].parent.mkdir(parents=True, exist_ok=True)
        locations["config"].write_text((ROOT / "config.example.json").read_text())
    if not no_start:
        systemctl("daemon-reload")
        systemctl("enable", UNIT)
        systemctl("restart", UNIT)
        systemctl("is-active", UNIT)
    print(f"Installed to {app}")
    print(f"Settings preserved at {locations['config']}")
    if backup:
        print(f"Previous installation saved at {backup}")
    if no_start:
        print(f"Start later: systemctl --user daemon-reload && systemctl --user enable --now {UNIT}")
    else:
        print("Running now and enabled for future graphical sessions.")
    return locations


def uninstall(home=None, purge=False, no_service=False):
    locations = paths(home)
    if not no_service:
        systemctl("disable", "--now", UNIT)
    if locations["app"].is_symlink():
        locations["app"].unlink()
    elif locations["app"].exists():
        shutil.rmtree(locations["app"])
    locations["unit"].unlink(missing_ok=True)
    if not no_service:
        systemctl("daemon-reload")
    if purge:
        locations["config"].unlink(missing_ok=True)
        try:
            locations["config"].parent.rmdir()
        except OSError:
            pass
    print("Removed application and user service.")
    print("Settings removed." if purge else f"Settings retained at {locations['config']}")
    print(f"Backups retained at {locations['backups']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    install_parser = subcommands.add_parser("install", help="Install and start for the current user")
    install_parser.add_argument(
        "--no-start", action="store_true", help="Copy files only, without dependencies or session checks"
    )
    uninstall_parser = subcommands.add_parser("uninstall", help="Stop, disable and remove the application")
    uninstall_parser.add_argument("--purge", action="store_true", help="Also remove config.json")
    uninstall_parser.add_argument(
        "--no-service", action="store_true", help="Skip systemd commands; for offline/manual installations"
    )
    arguments = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run as your desktop user, without sudo")
    try:
        if arguments.command == "install":
            install(no_start=arguments.no_start)
        else:
            uninstall(purge=arguments.purge, no_service=arguments.no_service)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Installation/removal failed: {error}\n")


if __name__ == "__main__":
    main()
