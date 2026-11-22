#!/usr/bin/env bash
set -euo pipefail
python -m pip check
python -m ruff check app tests scripts e2e
python -m ruff format --check app tests scripts e2e
python -m pytest --cov=app --cov=scripts --cov-branch --cov-report=term-missing --cov-report=xml "$@"
report=$(mktemp)
trap 'rm -f "$report"' EXIT
python -m app.evaluation_suite --output "$report"
