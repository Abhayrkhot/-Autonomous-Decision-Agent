#!/usr/bin/env bash
# Regenerate requirements.lock (hash-pinned runtime dependencies for the Docker image,
# resolved for its Python 3.13 base) and audit the result. Requires Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
ADA_PYTHON=3.13 exec scripts/dev.sh bash -c '
pip-compile --quiet --upgrade --strip-extras --generate-hashes --allow-unsafe --no-header \
  --output-file=requirements.lock requirements.txt
pip-audit --strict --require-hashes --disable-pip -r requirements.lock'
