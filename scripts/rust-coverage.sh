#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "$#" -le 2 ]]
storage="${1:-}"
browser_profile="${2:-}"
[[ -z "$browser_profile" || ( -n "$storage" && -d "$browser_profile" && ! -L "$browser_profile" ) ]]
if [[ -n "$storage" ]]; then
  [[ -f "$storage" && ! -L "$storage" ]]
  [[ "$(stat -c %s "$storage")" -le 4194304 ]]
fi
[[ -z "${RUSTC_BOOTSTRAP:-}" ]]
[[ "$(cargo llvm-cov --version)" == 'cargo-llvm-cov 0.9.1' ]]
coverage_toolchain="$(cat ci/coverage-toolchain)"
[[ "$coverage_toolchain" =~ ^nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]
mkdir -p target/quality
timeout 60 cargo metadata --format-version 1 --locked --all-features > target/quality/metadata.json
rm -f target/quality/coverage-snapshot.json target/quality/rust-coverage.json target/quality/coverage-probe.json
timeout 120 cargo run --locked -p xtask -- snapshot-coverage \
  target/quality/metadata.json target/quality/coverage-snapshot.json
(
export RUSTUP_TOOLCHAIN="$coverage_toolchain"
export CARGO_TARGET_DIR="$PWD/target/coverage"
timeout 60 cargo llvm-cov show-env --sh --branch --no-cfg-coverage --no-cfg-coverage-nightly \
  > target/quality/coverage-env.sh
source target/quality/coverage-env.sh
timeout 60 cargo llvm-cov clean --workspace
bash scripts/rust-check.sh
if [[ -n "$storage" ]]; then
  export FLASHCARDS_LIVE_PREBUILT=true
  timeout 600 cargo build --workspace --all-targets --locked
  for format in pdf pptx; do
    live_arguments=("$storage" "$format")
    if [[ "$format" == pdf && -n "$browser_profile" ]]; then
      live_arguments+=("$browser_profile")
    fi
    timeout 900 "$CARGO_TARGET_DIR/debug/examples/notebooklm_companion_smoke" "${live_arguments[@]}"
  done
fi
timeout 120 cargo llvm-cov report --json --failure-mode any \
  --ignore-filename-regex '(^|/)rust/xtask/' \
  --output-path target/quality/rust-coverage.json
)
bash scripts/rust-coverage-probe.sh
timeout 120 cargo run --locked -p xtask -- seal-coverage \
  target/quality/metadata.json target/quality/rust-coverage.json target/quality/coverage-probe.json \
  target/quality/coverage-snapshot.json
timeout 120 cargo run --locked -p xtask -- check-coverage \
  target/quality/metadata.json target/quality/rust-coverage.json target/quality/coverage-probe.json \
  target/quality/coverage-snapshot.json
