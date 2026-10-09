#!/usr/bin/env bash
# Start one isolated Flashcards Generator web UI for verification.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

UV="$(verify_uv)"
export PATH="$(dirname "${UV}"):${PATH}"

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

if [[ ! -d "${VERIFY_REPO_ROOT}/.venv" ]]; then
  (cd "${VERIFY_REPO_ROOT}" && "${UV}" sync --frozen)
fi

if [[ ! -f "${VERIFY_DIST}" ]]; then
  corepack pnpm@12.5.1 --dir "${VERIFY_REPO_ROOT}/frontend" install --frozen-lockfile
  corepack pnpm@12.5.1 --dir "${VERIFY_REPO_ROOT}/frontend" run build
fi

rm -rf "${VERIFY_SCRATCH}"
mkdir -p "${VERIFY_SCRATCH}/data" "${VERIFY_EVIDENCE}"

DATABASE_PATH="${VERIFY_SCRATCH}/web.db"
LOG_PATH="${VERIFY_SCRATCH}/server.log"
BASE_URL="http://127.0.0.1:${VERIFY_PORT}"

nohup env \
  PATH="${PATH}" \
  FLASHCARDS_ENVIRONMENT=development \
  FLASHCARDS_HOST=127.0.0.1 \
  FLASHCARDS_PORT="${VERIFY_PORT}" \
  FLASHCARDS_DATABASE_URL="sqlite+aiosqlite:///${DATABASE_PATH}" \
  FLASHCARDS_DATA_DIR="${VERIFY_SCRATCH}/data" \
  FLASHCARDS_BOOTSTRAP_PASSWORD="${VERIFY_PASSWORD}" \
  FLASHCARDS_SESSION_SECRET="verify-session-secret-32-characters-min" \
  FLASHCARDS_AUTH_LOOKUP_SECRET="verify-lookup-secret-32-characters-minx" \
  FLASHCARDS_AUTO_CREATE_SCHEMA=true \
  FLASHCARDS_SENTRY_ENABLED=false \
  "${UV}" run --frozen --project "${VERIFY_REPO_ROOT}" flashcards-web \
  >"${LOG_PATH}" 2>&1 </dev/null &
PID="$!"

cat >"${VERIFY_STATE}" <<EOF
PID=${PID}
PORT=${VERIFY_PORT}
BASE_URL=${BASE_URL}
PASSWORD=${VERIFY_PASSWORD}
SCRATCH=${VERIFY_SCRATCH}
DATABASE_PATH=${DATABASE_PATH}
LOG_PATH=${LOG_PATH}
REPO_ROOT=${VERIFY_REPO_ROOT}
EOF

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
