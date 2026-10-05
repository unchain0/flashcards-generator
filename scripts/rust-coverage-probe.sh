#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -z "${RUSTC_BOOTSTRAP:-}" ]]
export RUSTUP_TOOLCHAIN
RUSTUP_TOOLCHAIN="$(cat ci/coverage-toolchain)"
[[ "$RUSTUP_TOOLCHAIN" =~ ^nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]
mkdir -p target/quality
find target/quality -maxdepth 1 -name 'coverage-probe-*.profraw' -type f -delete
timeout 60 rustc --test ci/coverage-probe.rs --edition=2024 --crate-name coverage_probe \
  -C instrument-coverage -Z coverage-options=branch -o target/quality/coverage-probe
LLVM_PROFILE_FILE="$PWD/target/quality/coverage-probe-%p.profraw" \
  timeout 30 target/quality/coverage-probe
sysroot="$(rustc --print sysroot)"
host="$(rustc -vV | sed -n 's/^host: //p')"
tools="$sysroot/lib/rustlib/$host/bin"
timeout 30 "$tools/llvm-profdata" merge -sparse target/quality/coverage-probe-*.profraw \
  -o target/quality/coverage-probe.profdata
timeout 30 "$tools/llvm-cov" export target/quality/coverage-probe \
  -instr-profile target/quality/coverage-probe.profdata > target/quality/coverage-probe.json
