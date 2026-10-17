#!/usr/bin/env bash
# Start one isolated Flashcards Generator web UI for verification.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

if [[ -f "${VERIFY_STATE}" ]]; then
  # shellcheck disable=SC1090
  source "${VERIFY_STATE}"
  if verify_pid_alive "${PID:-}"; then
    echo "Verification server already running at ${BASE_URL} pid=${PID}."
    echo "Refusing to start a second copy. Run scripts/doctor.sh, or scripts/cleanup.sh first."
    exit 0
  fi
fi

listeners="$(verify_listener_pids "${VERIFY_PORT}")"
if [[ -n "${listeners}" ]]; then
  echo "Port ${VERIFY_PORT} is already taken by pid(s): ${listeners//$'\n'/, }." >&2
  echo "That process was not started by this skill. Refusing to kill it or share it." >&2
  exit 1
fi

if [[ ! -f "${VERIFY_DIST}" ]]; then
  corepack pnpm@12.5.1 --dir "${VERIFY_REPO_ROOT}/frontend" install --frozen-lockfile
  corepack pnpm@12.5.1 --dir "${VERIFY_REPO_ROOT}/frontend" run build
fi

if [[ ! -x "${VERIFY_WEB_BIN}" || ! -x "${VERIFY_COMPANION_BIN}" ]]; then
  (cd "${VERIFY_REPO_ROOT}" && CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}" cargo build --workspace --bins --locked)
fi

rm -rf "${VERIFY_SCRATCH}"
mkdir -p "${VERIFY_SCRATCH}/data" "${VERIFY_EVIDENCE}"

LOG_PATH="${VERIFY_SCRATCH}/server.log"
BASE_URL="http://127.0.0.1:${VERIFY_PORT}"
DB_CONTAINER="flashcards-verify-${RANDOM}-${RANDOM}"
POSTGRES_PASSWORD="$(verify_random_token 24)"

docker run --detach --name "${DB_CONTAINER}" \
  --tmpfs /var/lib/postgresql/data \
  -p 127.0.0.1::5432 \
  -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  -e POSTGRES_USER=flashcards \
  -e POSTGRES_DB=flashcards \
  postgres:16-alpine >/dev/null
for _ in $(seq 1 60); do
  if docker exec "${DB_CONTAINER}" pg_isready -h 127.0.0.1 -U flashcards >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
docker exec "${DB_CONTAINER}" pg_isready -h 127.0.0.1 -U flashcards >/dev/null
DB_ADDRESS="$(docker port "${DB_CONTAINER}" 5432/tcp)"
DATABASE_URL="postgresql://flashcards:${POSTGRES_PASSWORD}@127.0.0.1:${DB_ADDRESS##*:}/flashcards"

# Generate per-launch credentials (12+ char password; 32+ char secrets).
VERIFY_PASSWORD="${VERIFY_PASSWORD:-$(verify_random_token 18)}"
SESSION_SECRET="${FLASHCARDS_SESSION_SECRET:-$(verify_random_token 32)}"
AUTH_LOOKUP_SECRET="${FLASHCARDS_AUTH_LOOKUP_SECRET:-$(verify_random_token 32)}"
if [[ "${SESSION_SECRET}" == "${AUTH_LOOKUP_SECRET}" ]]; then
  AUTH_LOOKUP_SECRET="$(verify_random_token 32)"
fi

env \
  PATH="${PATH}" \
  FLASHCARDS_ENVIRONMENT=development \
  FLASHCARDS_HOST=127.0.0.1 \
  FLASHCARDS_PORT="${VERIFY_PORT}" \
  FLASHCARDS_DATABASE_URL="${DATABASE_URL}" \
  FLASHCARDS_BOOTSTRAP_PASSWORD="${VERIFY_PASSWORD}" \
  FLASHCARDS_SESSION_SECRET="${SESSION_SECRET}" \
  FLASHCARDS_AUTH_LOOKUP_SECRET="${AUTH_LOOKUP_SECRET}" \
  FLASHCARDS_AUTO_CREATE_SCHEMA=false \
  FLASHCARDS_SENTRY_ENABLED=false \
  "${VERIFY_WEB_BIN}" --migrate

nohup env \
  FLASHCARDS_ENVIRONMENT=development \
  FLASHCARDS_HOST=127.0.0.1 \
  FLASHCARDS_PORT="${VERIFY_PORT}" \
  FLASHCARDS_DATABASE_URL="${DATABASE_URL}" \
  FLASHCARDS_BOOTSTRAP_PASSWORD="${VERIFY_PASSWORD}" \
  FLASHCARDS_SESSION_SECRET="${SESSION_SECRET}" \
  FLASHCARDS_AUTH_LOOKUP_SECRET="${AUTH_LOOKUP_SECRET}" \
  FLASHCARDS_AUTO_CREATE_SCHEMA=false \
  FLASHCARDS_STATIC_DIR="${VERIFY_REPO_ROOT}/src/flashcards_generator/delivery/web/static/dist" \
  FLASHCARDS_SENTRY_ENABLED=false \
  "${VERIFY_WEB_BIN}" \
  >"${LOG_PATH}" 2>&1 </dev/null &
PID="$!"

cat >"${VERIFY_STATE}" <<EOF
PID=${PID}
PORT=${VERIFY_PORT}
BASE_URL=${BASE_URL}
PASSWORD=${VERIFY_PASSWORD}
SCRATCH=${VERIFY_SCRATCH}
DB_CONTAINER=${DB_CONTAINER}
LOG_PATH=${LOG_PATH}
REPO_ROOT=${VERIFY_REPO_ROOT}
EOF
unset POSTGRES_PASSWORD SESSION_SECRET AUTH_LOOKUP_SECRET

ready=0
for _ in $(seq 1 60); do
  if curl -sf "${BASE_URL}/health/ready" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! verify_pid_alive "${PID}"; then
    echo "Server exited before it was ready. Log follows." >&2
    cat "${LOG_PATH}" >&2 || true
    exit 1
  fi
  sleep 0.5
done

if [[ "${ready}" != "1" ]]; then
  echo "Server did not answer ${BASE_URL}/health/ready within 30s. Log follows." >&2
  cat "${LOG_PATH}" >&2 || true
  exit 1
fi

echo "ready ${BASE_URL} pid=${PID}"
