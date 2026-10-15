#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/pytest -q
