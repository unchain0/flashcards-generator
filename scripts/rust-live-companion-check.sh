#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
storage="${1:?Provide a local NotebookLM storage-state file}"
format="${2:-pdf}"
browser_profile="${3:-}"
[[ "$#" -le 3 && ( "$format" == pdf || "$format" == pptx ) ]]
[[ -z "$browser_profile" || ( "$format" == pdf && -d "$browser_profile" && ! -L "$browser_profile" ) ]]
[[ -f "$storage" && ! -L "$storage" ]]
[[ "$(stat -c %s "$storage")" -le 4194304 ]]
target_root="${CARGO_TARGET_DIR:-$PWD/target}"
if [[ "$target_root" != /* ]]; then target_root="$PWD/$target_root"; fi
export FLASHCARDS_RUST_BIN_DIR="$target_root/debug"
export FLASHCARDS_LIVE_COMPANION_BIN_DIR="$target_root/release"
case "${FLASHCARDS_LIVE_PREBUILT:-false}" in
  true) FLASHCARDS_LIVE_COMPANION_BIN_DIR="$target_root/debug" ;;
  false)
    timeout 600 cargo build --locked --bin flashcards-web
    timeout 600 cargo build --release --locked --bin flashcards-companion
    if [[ -n "$browser_profile" ]]; then
      timeout 600 cargo build --locked -p flashcards-delivery --example notebooklm_browser_smoke
    fi
    ;;
  *) printf 'FLASHCARDS_LIVE_PREBUILT must be true or false.\n' >&2; exit 2 ;;
esac
[[ -x "$FLASHCARDS_RUST_BIN_DIR/flashcards-web" ]]
[[ -x "$FLASHCARDS_LIVE_COMPANION_BIN_DIR/flashcards-companion" ]]
scope="flashcards-rust-live-$RANDOM-$RANDOM"
check_root="${XDG_CACHE_HOME:-$HOME/.cache}"
if [[ "$check_root" != /* ]]; then check_root="$HOME/.cache"; fi
mkdir -p "$check_root"
check_dir="$(mktemp -d --tmpdir="$check_root" fclive.XXXXXXXX)"
mkdir -m 700 "$check_dir/tmp"
export TMPDIR="$check_dir/tmp"
phase="live Companion setup"
trap 'printf "Live Rust Companion validation failed during %s (exit %s).\n" "$phase" "$?" >&2' ERR
cleanup() {
  timeout 30 docker rm --force "$scope" >/dev/null 2>&1 || true
  find "$check_dir" -depth -delete
}
trap cleanup EXIT
export POSTGRES_PASSWORD FLASHCARDS_SESSION_SECRET FLASHCARDS_AUTH_LOOKUP_SECRET
POSTGRES_PASSWORD="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
FLASHCARDS_SESSION_SECRET="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
FLASHCARDS_AUTH_LOOKUP_SECRET="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
timeout 60 docker run --detach --name "$scope" --tmpfs /var/lib/postgresql/data \
  -p 127.0.0.1::5432 -e POSTGRES_PASSWORD -e POSTGRES_USER=flashcards \
  -e POSTGRES_DB=flashcards postgres:16-alpine >/dev/null
ready=false
for ((attempt = 0; attempt < 60; attempt++)); do
  if timeout 5 docker exec "$scope" pg_isready -h 127.0.0.1 -U flashcards >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[[ "$ready" == true ]]
address="$(timeout 10 docker port "$scope" 5432/tcp)"
export FLASHCARDS_TEST_DATABASE_URL="postgresql://flashcards:$POSTGRES_PASSWORD@$address/flashcards"
export FLASHCARDS_DATABASE_URL="$FLASHCARDS_TEST_DATABASE_URL"
export FLASHCARDS_SENTRY_ENABLED=false FLASHCARDS_ENVIRONMENT=test FLASHCARDS_AUTO_CREATE_SCHEMA=false
export FLASHCARDS_PROVISION_PASSWORD=e2e-password-123
timeout 60 "$FLASHCARDS_RUST_BIN_DIR/flashcards-web" --migrate
timeout 60 "$FLASHCARDS_RUST_BIN_DIR/flashcards-web" --provision-user
unset FLASHCARDS_PROVISION_PASSWORD
user="$(timeout 10 docker exec "$scope" psql -U flashcards -d flashcards -Atc 'SELECT id FROM web_users')"
[[ "$user" =~ ^[a-f0-9]{32}$ ]]
export FLASHCARDS_LIVE_COMPANION_DATA_DIR="$check_dir/companion"
profile="$FLASHCARDS_LIVE_COMPANION_DATA_DIR/profiles/$user/profiles/default"
install -d -m 700 "$profile"
if [[ -n "$browser_profile" ]]; then
  phase="private native browser session preparation"
  export FLASHCARDS_LIVE_EXPECT_BROWSER_LOGIN=true
  install -d -m 700 "$profile/browser_profile"
  timeout 60 "$FLASHCARDS_RUST_BIN_DIR/examples/notebooklm_browser_smoke" \
    --copy-profile "$browser_profile" "$profile/browser_profile"
else
  export FLASHCARDS_LIVE_EXPECT_BROWSER_LOGIN=false
  install -m 600 "$storage" "$profile/storage_state.json"
fi
phase="synthetic document preparation"
install -d -m 700 "$check_dir/source"
if [[ "$format" == pptx ]]; then
  export FLASHCARDS_LIVE_SOURCE_FILE="$check_dir/source/FLASHCARDS-RUST-PPTX-QA.pptx"
  install -m 600 rust/integrations/tests/fixtures/minimal.pptx "$FLASHCARDS_LIVE_SOURCE_FILE"
else
  export FLASHCARDS_LIVE_SOURCE_FILE="$check_dir/source/FLASHCARDS-RUST-PDF-QA.pdf"
  timeout --kill-after=5 60 corepack pnpm@12.5.1 --dir frontend exec node --input-type=module -e '
import { chromium } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(resolve("e2e/fixtures/cell-biology.html")).href);
  await page.pdf({ path: process.env.FLASHCARDS_LIVE_SOURCE_FILE, format: "A4" });
} finally { await browser.close(); }
'
  timeout 15 qpdf --check "$FLASHCARDS_LIVE_SOURCE_FILE" >/dev/null
  timeout 15 pdftotext "$FLASHCARDS_LIVE_SOURCE_FILE" "$check_dir/source/content.txt"
  rg --quiet 'Mitochondria produce ATP' "$check_dir/source/content.txt"
fi
export FLASHCARDS_E2E_DIRECTORY="$check_dir"
phase="native browser upload and real Google generation"
timeout 450 corepack pnpm@12.5.1 --dir frontend exec playwright test --config playwright.live.config.ts
printf 'Native Companion browser upload, real Google generation, and CSV download passed.\n'
