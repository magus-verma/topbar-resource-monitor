# Contributing

Useful contributions include desktop compatibility reports, installer improvements, accessibility fixes and focused bug fixes. For substantial behaviour changes, open an issue with the proposed user experience first.

## Run the checks

From the repository root, with Python 3.10 or newer:

```sh
python3 -m unittest discover -v
python3 -m compileall -q monitor.py metrics.py scripts
sh -n install.sh uninstall.sh
```

Optional lint in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/pip install ruff==0.16.10
.venv/bin/ruff check .
```

Tests for resource maths and installation require only the Python standard library and do not start a desktop process. Installer tests create temporary homes, exercise upgrade backups, preserve existing settings and verify removal. They never stress the CPU or fill RAM/swap.

## Desktop validation

On a desktop with the dependencies and active tray support, run `./install.sh`, verify all three labels and icon colours, open their menus, and check service logs. Check that stopping/restarting the service removes/re-registers its indicators and that automatic startup survives a normal login. Record the distribution, GNOME/desktop version, display protocol and CPU architecture in your report.

Do not induce memory exhaustion to test warnings. Add synthetic counter/timing tests for edge cases instead. Screenshots added to this repository should show the relevant widget area and exclude unrelated application content; clearly label any illustrated or simulated values.

## Pull requests

- Describe the user-visible problem, resulting behaviour and checks you ran.
- Keep changes focused and preserve current settings during upgrades.
- Add tests for changed resource calculations, alert timing or installation behaviour.
- Update the README and Unreleased changelog when user behaviour changes.
- Do not add runtime telemetry, network access, automatic process termination or privileged installation without an explicit design discussion.

Contributions are licensed under the repository's MIT licence. Participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
