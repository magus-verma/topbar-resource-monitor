#!/bin/sh
set -eu
exec /usr/bin/python3 "$(dirname "$0")/scripts/manage.py" uninstall "$@"
