#!/usr/bin/env bash
# Read-only check: is the verification web UI worth driving?
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"
verify_load_state

fail() {
  echo "doctor: not ready: $*" >&2
  exit 1
}

verify_pid_alive "${PID}" || fail "pid ${PID} is not running"
[[ -d "/proc/${PID}" ]] || fail "pid ${PID} has no /proc entry"

listeners="$(verify_listener_pids "${PORT}")"
[[ -n "${listeners}" ]] || fail "nothing is listening on port ${PORT}"

owned=0
while IFS= read -r listener; do
  [[ -z "${listener}" ]] && continue
  if [[ "${listener}" == "${PID}" ]] || verify_is_ancestor "${PID}" "${listener}"; then
    owned=1
    break
  fi
done <<<"${listeners}"
[[ "${owned}" == "1" ]] || fail "port ${PORT} is owned by pid(s) ${listeners//$'\n'/, }, not ${PID}"

live="$(curl -sf "${BASE_URL}/health/live")" || fail "GET /health/live failed"
[[ "${live}" == '{"status":"ok"}' ]] || fail "GET /health/live returned ${live}"

ready="$(curl -sf "${BASE_URL}/health/ready")" || fail "GET /health/ready failed"
[[ "${ready}" == '{"status":"ok"}' ]] || fail "GET /health/ready returned ${ready}"

html="$(curl -sf "${BASE_URL}/")" || fail "GET / failed"
grep -q "Gere flashcards do seu material." <<<"${html}" || fail "dashboard HTML is missing the login heading"

echo "doctor: ok ${BASE_URL} pid=${PID}"
