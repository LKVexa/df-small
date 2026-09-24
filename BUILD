#!/bin/sh
# DF_Small/BUILD -- compile the embedded VM in place (vm/<package>/.build); no-op where nothing compiles
# Offline. Nothing here opens a socket (NETWORK=deny, BACKEND=none).
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
exec "$PY" -B adapter/dfabric/cli.py node-build "$@"
