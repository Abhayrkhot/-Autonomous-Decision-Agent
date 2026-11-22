#!/usr/bin/env bash
# Run a command in a disposable Python container next to real PostgreSQL 17 and Redis 7.
#   scripts/dev.sh bash scripts/verify.sh
#   ADA_PYTHON=3.13 scripts/dev.sh python -m pytest -q tests/test_jobs.py
# Services persist between calls (named ada-dev-*); `scripts/dev.sh --down` removes them.
set -euo pipefail
cd "$(dirname "$0")/.."
py=${ADA_PYTHON:-3.11}
net=ada-dev
image=ada-dev:py$py
if [ "${1:-}" = "--down" ]; then
  docker rm -f ada-dev-postgres ada-dev-redis >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  exit 0
fi
docker network inspect "$net" >/dev/null 2>&1 || docker network create "$net" >/dev/null
if ! docker inspect ada-dev-postgres >/dev/null 2>&1; then
  docker run -d --name ada-dev-postgres --network "$net" \
    -e POSTGRES_PASSWORD=local-test-only -e POSTGRES_DB=agent postgres:17-alpine >/dev/null
fi
if ! docker inspect ada-dev-redis >/dev/null 2>&1; then
  docker run -d --name ada-dev-redis --network "$net" redis:7-alpine >/dev/null
fi
docker start ada-dev-postgres ada-dev-redis >/dev/null
until docker exec ada-dev-postgres pg_isready -U postgres -d agent >/dev/null 2>&1; do sleep 0.5; done
hash=$(cat requirements.txt requirements-dev.txt | sha256sum | cut -c1-12)
if [ "$(docker image inspect -f '{{index .Config.Labels "reqs"}}' "$image" 2>/dev/null)" != "$hash" ]; then
  context=$(mktemp -d)
  cp requirements.txt requirements-dev.txt "$context/"
  cat > "$context/Dockerfile" <<DOCKERFILE
FROM python:$py-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt /tmp/
RUN pip install -q --no-cache-dir -r /tmp/requirements-dev.txt
DOCKERFILE
  docker build -q -t "$image" --label "reqs=$hash" "$context" >/dev/null
  rm -r "$context"
fi
tty=-i
[ -t 0 ] && tty=-it
exec docker run --rm $tty --network "$net" -v "$PWD:/src" -w /src \
  -e TEST_DATABASE_URL=postgresql://postgres:local-test-only@ada-dev-postgres:5432/agent \
  -e TEST_REDIS_URL=redis://ada-dev-redis:6379/0 \
  -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -u "$(id -u):$(id -g)" \
  "$image" "$@"
