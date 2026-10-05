#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:-flashcards-generator:rust-qa}"
timeout 30 docker image inspect "$image" >/dev/null
scope="flashcards-rust-container-$RANDOM-$RANDOM"
mkdir -p target/quality
check_dir="$(mktemp -d --tmpdir="$PWD/target/quality" flashcards-rust-container.XXXXXXXX)"
phase="container setup"
trap 'printf "Rust container validation failed during %s (exit %s).\n" "$phase" "$?" >&2' ERR
cleanup() {
  timeout 30 docker rm --force "$scope-app" "$scope-db" >/dev/null 2>&1 || true
  timeout 10 docker network rm "$scope" >/dev/null 2>&1 || true
  find "$check_dir" -depth -delete
}
trap cleanup EXIT
export POSTGRES_PASSWORD FLASHCARDS_SESSION_SECRET FLASHCARDS_AUTH_LOOKUP_SECRET
export FLASHCARDS_DATABASE_URL FLASHCARDS_BOOTSTRAP_PASSWORD FLASHCARDS_SENTRY_ENABLED
POSTGRES_PASSWORD="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
FLASHCARDS_SESSION_SECRET="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
FLASHCARDS_AUTH_LOOKUP_SECRET="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
FLASHCARDS_DATABASE_URL="postgresql+asyncpg://flashcards:$POSTGRES_PASSWORD@$scope-db:5432/flashcards"
FLASHCARDS_BOOTSTRAP_PASSWORD=runtime-container-test-password
FLASHCARDS_SENTRY_ENABLED=false
timeout 15 docker network create "$scope" >/dev/null
timeout 60 docker run --detach --name "$scope-db" --network "$scope" \
  --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD \
  -e POSTGRES_USER=flashcards -e POSTGRES_DB=flashcards postgres:16-alpine >/dev/null
ready=false
for ((attempt = 0; attempt < 60; attempt++)); do
  if timeout 5 docker exec "$scope-db" pg_isready -h 127.0.0.1 -U flashcards >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[[ "$ready" == true ]]
[[ "$(timeout 10 docker image inspect --format '{{.Config.User}}' "$image")" == app ]]
timeout 60 docker run --detach --name "$scope-app" --network "$scope" \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev -p 127.0.0.1::8000 \
  -e FLASHCARDS_DATABASE_URL -e FLASHCARDS_SESSION_SECRET -e FLASHCARDS_AUTH_LOOKUP_SECRET \
  -e FLASHCARDS_BOOTSTRAP_PASSWORD -e FLASHCARDS_SENTRY_ENABLED "$image" >/dev/null
wait_for_health() {
  local ready=false
  for ((attempt = 0; attempt < 60; attempt++)); do
    if [[ "$(timeout 5 docker inspect --format '{{.State.Health.Status}}' "$scope-app")" == healthy ]]; then
      ready=true
      break
    fi
    [[ "$(timeout 5 docker inspect --format '{{.State.Running}}' "$scope-app")" == true ]]
    sleep 1
  done
  [[ "$ready" == true ]]
}
phase="migration and runtime health"
wait_for_health
address="$(timeout 10 docker port "$scope-app" 8000/tcp)"
base_url="http://$address"
timeout 10 docker exec "$scope-app" sh -c '! command -v python && ! command -v cargo'
curl --max-time 10 --fail --silent --show-error "$base_url/health/live" >/dev/null
curl --max-time 10 --fail --silent --show-error --dump-header "$check_dir/headers" \
  "$base_url/" >"$check_dir/index.html"
rg --quiet --ignore-case '^strict-transport-security: max-age=31536000' "$check_dir/headers"
for asset in $(rg --only-matching '/assets/[^" ]+\.(js|css)' "$check_dir/index.html"); do
  curl --max-time 10 --fail --silent --show-error "$base_url$asset" >/dev/null
done
phase="password login and session cookies"
curl --max-time 60 --fail --silent --show-error --dump-header "$check_dir/headers" \
  --header 'Content-Type: application/json' --data '{"password":"runtime-container-test-password"}' \
  "$base_url/api/v1/auth/login" >/dev/null
rg --quiet --ignore-case '^set-cookie: .*HttpOnly.*SameSite=Lax.*Secure' "$check_dir/headers"
session_cookie="$(sed -n 's/^[Ss]et-[Cc]ookie: \([^;]*\).*/\1/p' "$check_dir/headers" | tr -d '\r')"
[[ -n "$session_cookie" ]]
curl --max-time 10 --fail --silent --show-error --header "Cookie: $session_cookie" \
  "$base_url/api/v1/auth/me" >/dev/null
phase="graceful shutdown and session preservation after restart"
timeout 30 docker stop --time 15 "$scope-app" >/dev/null
[[ "$(timeout 5 docker inspect --format '{{.State.ExitCode}}' "$scope-app")" == 0 ]]
timeout 15 docker start "$scope-app" >/dev/null
wait_for_health
address="$(timeout 10 docker port "$scope-app" 8000/tcp)"
base_url="http://$address"
curl --max-time 10 --fail --silent --show-error --header "Cookie: $session_cookie" \
  "$base_url/api/v1/auth/me" >/dev/null
[[ "$(timeout 10 docker exec "$scope-db" psql -U flashcards -d flashcards -Atc 'SELECT COUNT(*) FROM web_users')" == 1 ]]
phase="logout revocation"
curl --max-time 10 --fail --silent --show-error --request POST --header "Cookie: $session_cookie" \
  "$base_url/api/v1/auth/logout" >/dev/null
[[ "$(curl --max-time 10 --silent --output /dev/null --write-out '%{http_code}' \
  --header "Cookie: $session_cookie" "$base_url/api/v1/auth/me")" == 401 ]]
printf 'Rust container migration, health, assets, login, restart, and logout checks passed.\n'
