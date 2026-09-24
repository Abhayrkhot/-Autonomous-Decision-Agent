#!/usr/bin/env bash
set -euo pipefail
python -m pip check
python -m ruff check app tests scripts
python -m ruff format --check app tests scripts
python -m pytest --cov=app --cov-branch --cov-report=term-missing --cov-report=xml "$@"
python -m app.evaluation_suite --output /tmp/ada-evaluation-$$.json
rm /tmp/ada-evaluation-$$.json
