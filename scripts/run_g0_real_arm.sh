#!/usr/bin/env bash
set -Eeuo pipefail

# Exact real-runtime wrapper for one G0 arm. The caller must provide only the
# immutable trace, output directory, and arm-specific controls.
if [[ $# -ne 3 ]]; then
  echo "usage: $0 TRACE_PATH ARM RUN_DIR" >&2
  exit 2
fi

TRACE_PATH="$(realpath -- "$1")"
ARM="$2"
OUTPUT_DIR="$3"

case "$ARM" in
  no_control|always_recompute|concurrency_cap|request_token_bucket|minimum_retrieve_threshold|fixed_prefetch_depth|materialization_paced_oracle)
    ;;
  *)
    echo "ERROR: unsupported G0 arm: $ARM" >&2
    exit 2
    ;;
esac

if [[ ! -f "$TRACE_PATH" ]]; then
  echo "ERROR: trace does not exist: $TRACE_PATH" >&2
  exit 2
fi
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "ERROR: refusing to overwrite run directory: $OUTPUT_DIR" >&2
  exit 2
fi

export RUN_DIR="$OUTPUT_DIR"
export PROFILE_SCENARIO=g0-trace-online
export G0_TRACE_PATH="$TRACE_PATH"
export G0_ARM="$ARM"
export G0_CONCURRENCY_CAP="${G0_CONCURRENCY_CAP:-4}"
export G0_TOKEN_BUCKET_RATE="${G0_TOKEN_BUCKET_RATE:-8}"
export G0_TOKEN_BUCKET_CAPACITY="${G0_TOKEN_BUCKET_CAPACITY:-4}"
export G0_ORACLE_BYTE_BUDGET="${G0_ORACLE_BYTE_BUDGET:-31850496}"
requested_minimum_retrieve_tokens="${G0_MINIMUM_RETRIEVE_TOKENS:-256}"
requested_fixed_prefetch_depth="${G0_FIXED_PREFETCH_DEPTH:-4}"

export MODEL_PATH="${MODEL_PATH:-/root/models/DeepSeek-V2-Lite}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-g0-deepseek-v2-lite}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-18169}"
export DEVICE="${DEVICE:-2}"
export TP_SIZE=1
export MAX_MODEL_LEN=512
export MAX_NUM_SEQS=64
export MAX_NUM_BATCHED_TOKENS=8192
export GPU_MEMORY_UTILIZATION=0.75
export ENFORCE_EAGER=1

export VLLM_REPO="${VLLM_REPO:-/vllm-workspace/vllm}"
export VLLM_ASCEND_TORCH_PREFLIGHT=0
export G0_VLLM_ASCEND_ROOT="${G0_VLLM_ASCEND_ROOT:-/root/g0-vllm-ascend}"
export G0_VLLM_ASCEND_NATIVE_ROOT="${G0_VLLM_ASCEND_NATIVE_ROOT:-/vllm-workspace/vllm-ascend}"
if [[ -z "${G0_KV_TRANSFER_CONFIG:-}" ]]; then
  minimum_retrieve_tokens=0
  prefetch_depth=1
  case "$ARM" in
    always_recompute)
      minimum_retrieve_tokens=512
      ;;
    minimum_retrieve_threshold)
      minimum_retrieve_tokens="$requested_minimum_retrieve_tokens"
      ;;
    fixed_prefetch_depth)
      prefetch_depth="$requested_fixed_prefetch_depth"
      ;;
  esac
  printf -v G0_KV_TRANSFER_CONFIG \
    '{"kv_connector":"AscendStoreConnector","kv_role":"kv_both","kv_connector_extra_config":{"backend":"memcache","use_layerwise":true,"layerwise_prefetch_layers":%d,"minimum_retrieve_tokens":%d}}' \
    "$prefetch_depth" "$minimum_retrieve_tokens"
  export G0_KV_TRANSFER_CONFIG
  export G0_MINIMUM_RETRIEVE_TOKENS="$minimum_retrieve_tokens"
  export G0_FIXED_PREFETCH_DEPTH="$prefetch_depth"
fi
export G0_REQUIRE_TELEMETRY=1
export MMC_LOCAL_CONFIG_PATH="${MMC_LOCAL_CONFIG_PATH:-/root/kv-materialization-arrival-control/docs/wavemat/configs/mmc-local-tp1.conf}"
export OMP_NUM_THREADS=1

exec "$(dirname -- "$0")/run_msserviceprofiler.sh"
