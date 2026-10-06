"""Exercise installs/upgrades/removal in a temporary home, without a desktop."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import load_config
from scripts.manage import install, service_text, uninstall


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "desktop user % test"
        self.home.mkdir()
        self.environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home / ".config")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_install_upgrade_preserves_settings_and_creates_backup(self):
        locations = install(self.home, no_start=True)
        self.assertTrue((locations["app"] / "monitor.py").is_file())
        self.assertEqual(len(list((locations["app"] / "icons").glob("*.svg"))), 12)
        custom = {"warning_percent": 85, "critical_percent": 98}
        locations["config"].write_text(json.dumps(custom))
        (locations["app"] / "old-version").write_text("old")
        install(self.home, no_start=True)
        self.assertEqual(json.loads(locations["config"].read_text()), custom)
        backups = list(locations["backups"].iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "app/old-version").read_text(), "old")
        self.assertFalse((locations["app"] / "old-version").exists())

    def test_removal_preserves_config_unless_purged(self):
        locations = install(self.home, no_start=True)
        uninstall(self.home, no_service=True)
        self.assertFalse(locations["app"].exists())
        self.assertFalse(locations["unit"].exists())
        self.assertTrue(locations["config"].exists())
        uninstall(self.home, purge=True, no_service=True)
        self.assertFalse(locations["config"].exists())

    def test_invalid_existing_config_is_left_untouched(self):
        locations = install(self.home, no_start=True)
        locations["config"].write_text('{"poll_seconds": 1.5}')
        before = (locations["app"] / "monitor.py").read_bytes()
        with self.assertRaises(ValueError):
            install(self.home, no_start=True)
        self.assertEqual((locations["app"] / "monitor.py").read_bytes(), before)

    def test_unit_escapes_home_paths(self):
        unit = service_text(self.home)
        self.assertIn('desktop user %% test/monitor.py"', unit)

    def test_config_rejects_nonfinite_and_unknown_values(self):
        config = self.home / "config.json"
        for data in ('{"warning_percent": NaN}', '{"poll_seconds": true}', '{"unknown": 1}', "[]"):
            config.write_text(data)
            with self.assertRaises(ValueError):
                load_config(config)


if __name__ == "__main__":
    unittest.main()
