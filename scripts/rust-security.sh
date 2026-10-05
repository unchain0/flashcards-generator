#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
if [[ $# -ne 1 || ! "$1" =~ ^sha256:[a-f0-9]{64}$ ]]; then
  printf 'Supply the immutable Docker image ID to scan its distributed contents.\n' >&2
  exit 2
fi
image_id="$1"
[[ "$(timeout 30 docker image inspect --format '{{.Id}}' "$image_id")" == "$image_id" ]]
[[ "$(cargo deny --version)" == 'cargo-deny 0.20.2' ]]
timeout 300 cargo deny --locked --all-features check advisories sources

scanner='aquasec/trivy@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa'
scanner_uid="$(id -u)"
scanner_cache="trivy-cache-$scanner_uid"
mkdir -p "target/quality/$scanner_cache"
scanner_output="$(mktemp -d "$PWD/target/quality/trivy-results.XXXXXXXX")"
archive=""
cleanup() {
  if [[ -n "$archive" ]]; then rm -f "$archive"; fi
  find "$scanner_output" -depth -delete
}
trap cleanup EXIT
scan() {
  local status=0
  timeout 600 docker run --rm --read-only --tmpfs /tmp \
    --user "$scanner_uid:$(id -g)" --entrypoint /bin/sh \
    --mount "type=bind,src=$PWD,dst=/scan,readonly" \
    --mount "type=bind,src=$PWD/target/quality,dst=/quality" \
    --mount "type=bind,src=$scanner_output,dst=/reports" \
    "$scanner" -c 'umask 077; exec trivy "$@"' -- \
    --cache-dir "/quality/$scanner_cache" "$@" || status=$?
  for report in "$scanner_output"/*.json; do
    if [[ -f "$report" ]]; then mv "$report" "target/quality/${report##*/}"; fi
  done
  return "$status"
}

scan fs --scanners vuln --include-dev-deps \
  --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --ignore-unfixed=false --exit-code 1 \
  --timeout 5m --skip-dirs /scan/frontend/node_modules \
  --format json --output /reports/trivy-frontend.json /scan/frontend
scan fs --scanners secret --exit-code 1 --timeout 5m \
  --skip-dirs /scan/.git,/scan/.venv,/scan/target,/scan/frontend/node_modules \
  --format json --output /reports/trivy-secrets.json /scan

archive="$(mktemp "$PWD/target/quality/image.XXXXXXXX.tar")"
timeout 300 docker image save --output "$archive" "$image_id"
scan image --input "/quality/${archive##*/}" --scanners vuln,secret \
  --severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL --ignore-unfixed=false \
  --exit-code 1 --exit-on-eol 1 --timeout 5m \
  --format json --output /reports/trivy-image.json
printf 'Rust sources, frontend lockfile, secrets, and immutable image checks passed.\n'
