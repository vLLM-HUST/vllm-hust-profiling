#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 SUITE_DIR OUTPUT_ROOT" >&2
  exit 2
fi

SUITE_DIR="$(realpath -- "$1")"
OUTPUT_ROOT="$2"
MANIFEST="$SUITE_DIR/suite_manifest.json"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ARM_RUNNER="$SCRIPT_DIR/run_g0_real_arm.sh"
DRY_RUN="${G0_DRY_RUN:-0}"
INCLUDE_FRONTIER="${G0_INCLUDE_FRONTIER:-1}"
SEQUENCE_START="${G0_SEQUENCE_START:-1}"
SEQUENCE_END="${G0_SEQUENCE_END:-9999}"

if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: suite manifest not found: $MANIFEST" >&2
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required" >&2
  exit 2
fi
if [[ "$DRY_RUN" != 1 ]]; then
  mkdir -p "$OUTPUT_ROOT"
fi

check_service_port() {
  python3 - "$1" <<'PY'
import socket
import sys

with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=2):
    pass
PY
}

wait_for_run_port() {
  for _ in $(seq 1 120); do
    if python3 - 2>/dev/null <<'PY'
import socket

sock = socket.socket()
try:
    sock.bind(("127.0.0.1", 18169))
finally:
    sock.close()
PY
    then
      return
    fi
    sleep 1
  done
  echo "ERROR: vLLM run port 18169 did not become bindable" >&2
  exit 3
}

run_is_complete() {
  local run_dir="$1"
  local arm="$2"
  [[ -s "$run_dir/benchmark/random-online.json" ]] || return 1
  [[ -s "$run_dir/benchmark/request_results.jsonl" ]] || return 1
  [[ -s "$run_dir/parsed/g0_connector_summary.json" ]] || return 1
  jq -e --arg arm "$arm" \
    '.arm == $arm and .requests == 64 and .correctness_rate == 1' \
    "$run_dir/benchmark/random-online.json" >/dev/null
}

run_one() {
  local sequence="$1"
  local workload="$2"
  local intensity="$3"
  local arm="$4"
  local repeat="$5"
  local budget="${6:-}"
  local suffix=""
  if [[ -n "$budget" ]]; then
    suffix="-budget${budget}"
  fi
  local run_name
  printf -v run_name '%03d-%s-%s-%s-r%s%s' \
    "$sequence" "$workload" "$intensity" "$arm" "$repeat" "$suffix"
  local run_dir="$OUTPUT_ROOT/$run_name"
  local trace="$SUITE_DIR/${workload}_${intensity}_overlay.jsonl"

  if (( sequence < SEQUENCE_START || sequence > SEQUENCE_END )); then
    return
  fi
  if [[ ! -f "$trace" ]]; then
    echo "ERROR: overlay not found: $trace" >&2
    exit 2
  fi
  if run_is_complete "$run_dir" "$arm"; then
    echo "[g0-matrix] skip complete $run_name"
    return
  fi
  if [[ -e "$run_dir" ]]; then
    echo "ERROR: refusing incomplete/non-verifiable run directory: $run_dir" >&2
    exit 4
  fi
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "[g0-matrix] plan $run_name"
    return
  fi

  check_service_port 5000
  check_service_port 6000
  wait_for_run_port
  echo "[g0-matrix] start $run_name"
  if [[ -n "$budget" ]]; then
    env -u G0_KV_TRANSFER_CONFIG G0_ORACLE_BYTE_BUDGET="$budget" \
      "$ARM_RUNNER" "$trace" "$arm" "$run_dir"
  else
    env -u G0_KV_TRANSFER_CONFIG "$ARM_RUNNER" "$trace" "$arm" "$run_dir"
  fi
  if ! run_is_complete "$run_dir" "$arm"; then
    echo "ERROR: run completed without passing the artifact gate: $run_dir" >&2
    exit 5
  fi
}

while IFS=$'\t' read -r sequence workload intensity repeat arm; do
  run_one "$sequence" "$workload" "$intensity" "$arm" "$repeat"
done < <(
  jq -r '.schedule | sort_by(.sequence)[] |
    [.sequence, .workload, .burst_intensity, .repeat, .arm] | @tsv' "$MANIFEST"
)

if [[ "$INCLUDE_FRONTIER" == 1 ]]; then
  sequence=97
  for workload in burstgpt servegen; do
    for intensity in moderate high; do
      for repeat in 1 2 3; do
        for budget in 63700992 127401984 254803968; do
          run_one "$sequence" "$workload" "$intensity" \
            materialization_paced_oracle "$repeat" "$budget"
          sequence=$((sequence + 1))
        done
      done
    done
  done
fi

echo "[g0-matrix] complete"
