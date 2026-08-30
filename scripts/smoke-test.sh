#!/usr/bin/env bash

set -euo pipefail

web_base_url="${WEB_BASE_URL:-http://localhost:3000}"
response_file="$(mktemp)"

cleanup() {
  rm -f "${response_file}"
}

trap cleanup EXIT

assert_response() {
  local path="$1"
  local expected_status="$2"
  shift 2

  local actual_status
  actual_status="$(
    curl --silent --show-error \
      --output "${response_file}" \
      --write-out "%{http_code}" \
      "${web_base_url}${path}"
  )"

  if [[ "${actual_status}" != "${expected_status}" ]]; then
    printf 'Expected %s from %s, received %s.\n' "${expected_status}" "${path}" "${actual_status}" >&2
    printf 'Response: ' >&2
    cat "${response_file}" >&2
    printf '\n' >&2
    return 1
  fi

  local expected_fragment
  for expected_fragment in "$@"; do
    if ! grep --fixed-strings --quiet "${expected_fragment}" "${response_file}"; then
      printf 'Response from %s is missing %s.\n' "${path}" "${expected_fragment}" >&2
      printf 'Response: ' >&2
      cat "${response_file}" >&2
      printf '\n' >&2
      return 1
    fi
  done

  printf 'PASS %s -> %s\n' "${path}" "${actual_status}"
}

assert_response "/api/proxy/health/live" "200" '"status":"ok"'
assert_response "/api/proxy/health/ready" "200" '"status":"ready"' '"database":"ready"'
assert_response "/api/proxy/phase-0-deliberate-error" "404" '"code":"NOT_FOUND"' '"correlation_id"'

printf 'Galaxy Frog Phase 0 browser-to-FastAPI smoke test passed.\n'
