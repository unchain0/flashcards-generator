#!/usr/bin/env bash
set -euo pipefail
export FLASHCARDS_SENTRY_ENABLED=false

cd "$(dirname "$0")/.."
export FLASHCARDS_RUST_BIN_DIR="${CARGO_TARGET_DIR:-$PWD/target}/debug"
container="flashcards-rust-qa-$RANDOM-$RANDOM"
mkdir -p target/quality
check_root="${XDG_CACHE_HOME:-$HOME/.cache}"
if [[ "$check_root" != /* ]]; then check_root="$HOME/.cache"; fi
mkdir -p "$check_root"
check_dir="$(mktemp -d --tmpdir="$check_root" fcqa.XXXXXXXX)"
mkdir -m 700 "$check_dir/tmp"
export TMPDIR="$check_dir/tmp"
server_pid=""
companion_pid=""
phase="workspace checks"
trap 'printf "Rust validation failed during %s (exit %s).\n" "$phase" "$?" >&2' ERR
cleanup() {
  if [[ -n "$companion_pid" ]]; then
    kill "$companion_pid" 2>/dev/null || true
    wait "$companion_pid" 2>/dev/null || true
  fi
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  timeout 30 docker rm --force "$container" >/dev/null 2>&1 || true
  find "$check_dir" -depth -delete
}
trap cleanup EXIT

export POSTGRES_PASSWORD
POSTGRES_PASSWORD="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
timeout 60 docker run --detach --name "$container" \
  --tmpfs /var/lib/postgresql/data -p 127.0.0.1::5432 \
  -e POSTGRES_PASSWORD -e POSTGRES_USER=flashcards -e POSTGRES_DB=flashcards \
  postgres:16-alpine >/dev/null
ready=false
for ((attempt = 0; attempt < 60; attempt++)); do
  if timeout 5 docker exec "$container" pg_isready -h 127.0.0.1 -U flashcards >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[[ "$ready" == true ]]
db_address="$(timeout 10 docker port "$container" 5432/tcp)"
export FLASHCARDS_TEST_DATABASE_URL="postgresql://flashcards:$POSTGRES_PASSWORD@127.0.0.1:${db_address##*:}/flashcards"
timeout 600 cargo test --workspace --all-targets --locked
timeout 600 cargo test --workspace --doc --locked
timeout 600 cargo build --workspace --locked --bins

export FLASHCARDS_ENVIRONMENT=production
export FLASHCARDS_DATABASE_URL="$FLASHCARDS_TEST_DATABASE_URL"
export FLASHCARDS_SESSION_SECRET
export FLASHCARDS_AUTH_LOOKUP_SECRET
FLASHCARDS_SESSION_SECRET="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
FLASHCARDS_AUTH_LOOKUP_SECRET="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
export FLASHCARDS_AUTO_CREATE_SCHEMA=false
export FLASHCARDS_BOOTSTRAP_PASSWORD=""
export FLASHCARDS_HOST=127.0.0.1
export FLASHCARDS_PORT="$(shuf -i 20000-45000 -n 1)"
export FLASHCARDS_STATIC_DIR="$PWD/src/flashcards_generator/delivery/web/static/dist"
export FLASHCARDS_PROVISION_PASSWORD=runtime-test-password
timeout 60 "$FLASHCARDS_RUST_BIN_DIR/flashcards-web" --migrate
timeout 60 "$FLASHCARDS_RUST_BIN_DIR/flashcards-web" --provision-user
unset FLASHCARDS_PROVISION_PASSWORD

base_url="http://127.0.0.1:$FLASHCARDS_PORT"
start_server() {
  "$FLASHCARDS_RUST_BIN_DIR/flashcards-web" >"$check_dir/server.log" 2>&1 &
  server_pid=$!
  local started=false
  for ((attempt = 0; attempt < 60; attempt++)); do
    kill -0 "$server_pid"
    if rg --quiet 'Hosted web server started' "$check_dir/server.log"; then
      started=true
      break
    fi
    sleep 1
  done
  [[ "$started" == true ]]
}
start_server
phase="hosted health and assets"
curl --max-time 10 --fail --silent --show-error "$base_url/health/live" >/dev/null
curl --max-time 10 --fail --silent --show-error "$base_url/health/ready" >/dev/null
curl --max-time 10 --fail --silent --show-error --dump-header "$check_dir/headers" \
  "$base_url/" >"$check_dir/index.html"
rg --quiet --ignore-case '^strict-transport-security: max-age=31536000' "$check_dir/headers"
for asset in $(rg --only-matching '/assets/[^" ]+\.(js|css)' "$check_dir/index.html"); do
  curl --max-time 10 --fail --silent --show-error "$base_url$asset" >/dev/null
done
phase="hosted password login"
curl --max-time 60 --fail --silent --show-error --dump-header "$check_dir/headers" \
  --header 'Content-Type: application/json' \
  --data '{"password":"runtime-test-password"}' "$base_url/api/v1/auth/login" >/dev/null
session_cookie="$(sed -n 's/^[Ss]et-[Cc]ookie: \([^;]*\).*/\1/p' "$check_dir/headers" | tr -d '\r')"
[[ -n "$session_cookie" ]]
phase="hosted session and logout"
curl --max-time 10 --fail --silent --show-error --header "Cookie: $session_cookie" \
  "$base_url/api/v1/auth/me" >/dev/null
curl --max-time 10 --fail --silent --show-error --request POST \
  --header "Cookie: $session_cookie" "$base_url/api/v1/auth/logout" >/dev/null
[[ "$(curl --max-time 10 --silent --output /dev/null --write-out '%{http_code}' \
  --header "Cookie: $session_cookie" "$base_url/api/v1/auth/me")" == 401 ]]
kill "$server_pid"
wait "$server_pid"
server_pid=""
start_server
phase="hosted password login after restart"
curl --max-time 60 --fail --silent --show-error --dump-header "$check_dir/headers" --header 'Content-Type: application/json' \
  --data '{"password":"runtime-test-password"}' "$base_url/api/v1/auth/login" >/dev/null
session_cookie="$(sed -n 's/^[Ss]et-[Cc]ookie: \([^;]*\).*/\1/p' "$check_dir/headers" | tr -d '\r')"
curl --max-time 10 --fail --silent --show-error --request POST --header "Cookie: $session_cookie" \
  "$base_url/api/v1/auth/companion/token" >"$check_dir/token.json"
companion_token="$(jq --exit-status --raw-output '.access_token' "$check_dir/token.json")"
phase="companion startup"
export FLASHCARDS_COMPANION_WEB_ORIGIN="$base_url"
export FLASHCARDS_COMPANION_DATA_DIR="$check_dir/companion"
export FLASHCARDS_COMPANION_BROWSER="$check_dir/missing-browser"
"$FLASHCARDS_RUST_BIN_DIR/flashcards-companion" >"$check_dir/companion.log" 2>&1 &
companion_pid=$!
started=false
for ((attempt = 0; attempt < 60; attempt++)); do
  kill -0 "$companion_pid"
  if rg --quiet 'Local Companion started' "$check_dir/companion.log"; then
    started=true
    break
  fi
  sleep 1
done
[[ "$started" == true ]]
phase="companion health and status"
curl --max-time 10 --fail --silent --show-error --header "Origin: $base_url" \
  http://127.0.0.1:8766/v1/health >/dev/null
[[ "$(curl --max-time 10 --silent --output /dev/null --write-out '%{http_code}' \
  --header 'Origin: https://attacker.example' http://127.0.0.1:8766/v1/health)" == 401 ]]
curl --max-time 15 --fail --silent --show-error --header "Origin: $base_url" \
  --header "Authorization: Bearer $companion_token" \
  http://127.0.0.1:8766/v1/notebooklm/status >"$check_dir/status.json"
jq --exit-status '.authenticated == false and .status == "login_required" and .message == "login required"' \
  "$check_dir/status.json" >/dev/null
phase="companion login boundary"
curl --max-time 15 --fail --silent --show-error --request POST --header "Origin: $base_url" \
  --header "Authorization: Bearer $companion_token" \
  http://127.0.0.1:8766/v1/notebooklm/login >"$check_dir/login.json"
jq --exit-status '.authenticated == false and .status == "login_required" and .message == "Chrome or Chromium is required for NotebookLM login"' \
  "$check_dir/login.json" >/dev/null
phase="companion job access"
[[ "$(curl --max-time 15 --silent --output "$check_dir/upload.json" --write-out '%{http_code}' \
  --request POST --header "Origin: $base_url" --header "Authorization: Bearer $companion_token" \
  --form 'files=@rust/integrations/tests/fixtures/chapters.pdf' \
  "http://127.0.0.1:8766/v1/jobs")" == 409 ]]
jq --exit-status '.detail == "Conecte sua conta do NotebookLM antes de gerar flashcards."' "$check_dir/upload.json" >/dev/null
missing_job="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
[[ "$(curl --max-time 15 --silent --output "$check_dir/job.json" --write-out '%{http_code}' \
  --header "Origin: $base_url" --header "Authorization: Bearer $companion_token" \
  "http://127.0.0.1:8766/v1/jobs/$missing_job")" == 404 ]]
jq --exit-status '.detail == "Geração não encontrada"' "$check_dir/job.json" >/dev/null
[[ "$(curl --max-time 15 --silent --output "$check_dir/artifact.json" --write-out '%{http_code}' \
  --header "Origin: $base_url" --header "Authorization: Bearer $companion_token" \
  "http://127.0.0.1:8766/v1/jobs/$missing_job/artifacts/deck.csv")" == 404 ]]
jq --exit-status '.detail == "Arquivo não encontrado"' "$check_dir/artifact.json" >/dev/null
phase="companion access revocation"
curl --max-time 10 --fail --silent --show-error --request POST \
  --header "Cookie: $session_cookie" "$base_url/api/v1/auth/logout" >/dev/null
[[ "$(curl --max-time 10 --silent --output /dev/null --write-out '%{http_code}' \
  --header "Origin: $base_url" --header "Authorization: Bearer $companion_token" \
  http://127.0.0.1:8766/v1/notebooklm/status)" == 401 ]]
[[ "$(curl --max-time 15 --silent --output /dev/null --write-out '%{http_code}' \
  --request POST --header "Origin: $base_url" --header "Authorization: Bearer $companion_token" \
  "http://127.0.0.1:8766/v1/jobs")" == 401 ]]
for job_path in "/v1/jobs/$missing_job" "/v1/jobs/$missing_job/artifacts/deck.csv"; do
  [[ "$(curl --max-time 15 --silent --output /dev/null --write-out '%{http_code}' \
    --header "Origin: $base_url" --header "Authorization: Bearer $companion_token" \
    "http://127.0.0.1:8766$job_path")" == 401 ]]
done
kill "$companion_pid"
wait "$companion_pid"
companion_pid=""
phase="Rust hosted browser contracts with simulated Companion"
timeout 180 corepack pnpm@12.5.1 --dir frontend exec playwright test --config playwright.rust.config.ts
printf 'Rust workspace, hosted/Companion executables, and hosted browser checks passed.\n'
