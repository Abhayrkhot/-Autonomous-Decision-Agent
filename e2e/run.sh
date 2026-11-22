#!/usr/bin/env bash
# End-to-end run of the compose stack: real PostgreSQL, Redis, API and worker containers,
# plus a test-only HTTPS webhook sink and Responses API stub. Needs Docker Compose v2,
# openssl and python3 on the host. Writes e2e-out/report.json and leaves nothing running.
set -euo pipefail
cd "$(dirname "$0")/.."
out=$PWD/e2e-out
rm -rf "$out"
mkdir -p "$out/certs"

# Throwaway CA and server certificate so the webhook is real HTTPS with verification.
# Python 3.13 verifies strictly (VERIFY_X509_STRICT), so both carry key usage extensions.
certs=$out/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=ada-e2e-ca \
  -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign \
  -keyout "$certs/ca.key" -out "$certs/ca.pem" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj /CN=webhook \
  -keyout "$certs/webhook.key" -out "$certs/webhook.csr" 2>/dev/null
printf '%s\n' subjectAltName=DNS:webhook keyUsage=critical,digitalSignature,keyEncipherment \
  extendedKeyUsage=serverAuth >"$certs/san.ext"
openssl x509 -req -in "$certs/webhook.csr" -CA "$certs/ca.pem" -CAkey "$certs/ca.key" \
  -CAcreateserial -days 1 -extfile "$certs/san.ext" -out "$certs/webhook.pem" 2>/dev/null
chmod 644 "$certs"/*

E2E_USER_TOKEN=$(openssl rand -hex 24)
E2E_REVIEWER_TOKEN=$(openssl rand -hex 24)
E2E_OTHER_TOKEN=$(openssl rand -hex 24)
API_TOKENS_JSON=$(printf '{"%s":{"owner_id":"acme","actor":"alice","role":"user"},"%s":{"owner_id":"acme","actor":"rita","role":"reviewer"},"%s":{"owner_id":"globex","actor":"bob","role":"user"}}' \
  "$E2E_USER_TOKEN" "$E2E_REVIEWER_TOKEN" "$E2E_OTHER_TOKEN")
POSTGRES_PASSWORD=$(openssl rand -hex 16)
export E2E_OUT=$out E2E_UID=$(id -u) E2E_GID=$(id -g)
export E2E_USER_TOKEN E2E_REVIEWER_TOKEN E2E_OTHER_TOKEN API_TOKENS_JSON POSTGRES_PASSWORD
export ACTION_WEBHOOK_URL=https://webhook:8443/receive
export API_PORT=${API_PORT:-18765}

compose() { docker compose -p ada-e2e -f compose.yaml -f e2e/compose.yaml "$@"; }
cleanup() {
  compose logs --no-color >"$out/compose.log" 2>&1 || true
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$certs"/*.key
}
trap cleanup EXIT
drive() { compose run --rm --no-deps driver python /e2e/driver.py "$1"; }
stop_worker() {
  local id started elapsed code
  id=$(compose ps -q worker)
  started=$(date +%s.%N)
  compose stop -t 30 worker >/dev/null
  elapsed=$(python3 -c "print(round($(date +%s.%N) - $started, 2))")
  code=$(docker inspect -f '{{.State.ExitCode}}' "$id")
  echo "worker stop: exit $code after ${elapsed}s"
  python3 - "$out/checks.jsonl" "$code" "$elapsed" <<'PY'
import json, sys
path, code, elapsed = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
ok = code == 0 and elapsed < 10
with open(path, "a") as log:
    log.write(json.dumps({"phase": "lifecycle", "check": "worker exits 0 on SIGTERM well before the 30s grace period", "passed": ok, "exit_code": code, "seconds": elapsed}) + "\n")
sys.exit(0 if ok else 1)
PY
}

started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
compose build -q
compose up -d --wait postgres redis webhook model-stub migrate api worker
drive core
stop_worker
drive queued
compose start worker >/dev/null
drive drain
compose restart postgres >/dev/null
compose up -d --wait postgres >/dev/null
drive recovery
compose logs --no-color api worker >"$out/deterministic-mode.log" 2>&1
AGENT_MODE=openai OPENAI_API_KEY=e2e-model-key OPENAI_MODEL=e2e-stub \
  OPENAI_BASE_URL=http://model-stub:8080/v1 compose up -d --wait --no-deps --force-recreate api worker
drive model
stop_worker

python3 - "$out" "$started" "$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
  "$(git status --porcelain 2>/dev/null | grep -c . || true)" "$(docker compose version --short)" <<'PY'
import json, platform, sys
from datetime import datetime, timezone
out, started, revision, dirty, compose = sys.argv[1:6]
checks = [json.loads(line) for line in open(f"{out}/checks.jsonl")]
report = {
    "started_utc": started,
    "finished_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "revision": revision,
    "uncommitted_paths": int(dirty),
    "host": f"{platform.system()} {platform.machine()}",
    "docker_compose": compose,
    "checks_total": len(checks),
    "checks_passed": sum(c["passed"] for c in checks),
    "checks": checks,
}
json.dump(report, open(f"{out}/report.json", "w"), indent=2)
print(f"e2e: {report['checks_passed']}/{report['checks_total']} checks passed")
sys.exit(0 if report["checks_passed"] == report["checks_total"] else 1)
PY
