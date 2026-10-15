#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ "$#" -eq 0 ]; then
    exec .venv/bin/python -m autotrader.ml.evaluate --synthetic
fi
exec .venv/bin/python -m autotrader.ml.evaluate "$@"
