#!/usr/bin/env bash
set -Eeuo pipefail

# Fresh TOP10 data collection against the current vllm/vllm-ascend main trees.
# Run inside the Ascend container.  Every scenario/mode has an isolated dir.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

# Shared team configuration. Individual users override DEVICE, PORT and
# ROOT_DIR on the command line or in their shell.
# shellcheck disable=SC1091
source "$TASK_DIR/config/paths.env"
# shellcheck disable=SC1091
source "$TASK_DIR/config/versions.env"
# shellcheck disable=SC1091
source "$TASK_DIR/workloads/manifest.env"
ROOT_DIR="${ROOT_DIR:-$TASK_DIR/outputs/top10-all-scenes-20260723}"
RUNNER="$SCRIPT_DIR/run_msserviceprofiler_random_online.sh"
DEVICE="${DEVICE:-1}"
PORT="${PORT:-18167}"
COMMON_MODEL_14B="${MODEL_14B:-/root/.cache/huggingface/hub/models--Qwen--Qwen2.5-14B-Instruct/snapshots/cf98f3b3bbb457ad9e2bb7baf9a0125b6b88caa8}"
COMMON_MODEL_7B="${MODEL_7B:-/root/.cache/huggingface/hub/models--Qwen--Qwen2.5-7B-Instruct/snapshots/a09a35458c702b33eeacc393d103063234e8bc28}"
SHAREGPT_DATASET="${SHAREGPT_DATASET:-/workspace/.ai-workspace/tasks/2026-07-13-opt-leaderboard/worktree/vllm-hust-benchmark/.benchmarks/runtime-data/current-benchmark-datasets/ShareGPT_V3_unfiltered_cleaned_split.json}"
AGENT_DATASET="${AGENT_DATASET:-/workspace/vllm-hust-benchmark/scripts/traces/evoscientist-workload-custom.jsonl}"
MODE_FILTER="${MODE_FILTER:-both}"
SCENARIO_FILTER="${SCENARIO_FILTER:-all}"
RANDOM_PROMPTS="${RANDOM_PROMPTS:-32}"
LOGPROBS_PROMPTS="${LOGPROBS_PROMPTS:-32}"
PREFIX_PROMPTS="${PREFIX_PROMPTS:-32}"
KV_PREFIX_PROMPTS="${KV_PREFIX_PROMPTS:-32}"
AGENT_PROMPTS="${AGENT_PROMPTS:-32}"
SHAREGPT_PROMPTS="${SHAREGPT_PROMPTS:-32}"

mkdir -p "$ROOT_DIR"
{
  date -Is
  printf 'vllm=%s\n' "$(git -C /workspace/vllm-hust rev-parse HEAD)"
  printf 'vllm_ascend=%s\n' "$(git -C /workspace/vllm-ascend-hust rev-parse HEAD)"
  printf 'device=%s\n' "$DEVICE"
} > "$ROOT_DIR/versions.txt"

run_one() {
  local scenario="$1" mode="$2" model="$3" prompts="$4" rate="$5" input="$6" output="$7" max_len="$8" max_seqs="$9" max_tokens="${10}" extra_env="${11:-}"
  local label="${scenario}-eager-${mode}"
  local run_dir="$ROOT_DIR/$label"
  # On this Ascend host, concurrent default NUMA binding can block the false
  # path at [migrate] NPU:X -> NUMA. Keep the team entrypoint reproducible by
  # applying the known-good setting automatically; an explicit environment
  # value still takes precedence.
  local mode_additional_config="${ADDITIONAL_CONFIG:-}"
  if [[ "$mode" == false && -z "$mode_additional_config" ]]; then
    mode_additional_config='{"enable_cpu_binding":false}'
  fi
  if [[ -e "$run_dir/benchmark/random-online.json" && -e "$run_dir/parsed/chrome_tracing.json" ]]; then
    echo "[skip] $label already has benchmark and parsed trace"
    return 0
  fi
  echo "[run] $label"
  local dataset="$AGENT_DATASET"
  [[ "$scenario" == sharegpt-online ]] && dataset="$SHAREGPT_DATASET"
  # shellcheck disable=SC2086
  env RUN_DIR="$run_dir" PROFILE_SCENARIO="$scenario" ENFORCE_EAGER="$mode" \
    MODEL_PATH="$model" DEVICE="$DEVICE" PORT="$PORT" TP_SIZE=1 \
    NUM_PROMPTS="$prompts" REQUEST_RATE="$rate" RANDOM_INPUT_LEN="$input" \
    BENCH_INPUT_LEN="$input" RANDOM_OUTPUT_LEN="$output" BENCH_OUTPUT_LEN="$output" \
    MAX_MODEL_LEN="$max_len" MAX_NUM_SEQS="$max_seqs" MAX_NUM_BATCHED_TOKENS="$max_tokens" \
    GPU_MEMORY_UTILIZATION=0.75 PROFILER_TIMELIMIT=600 HF_HUB_OFFLINE=1 \
    DATASET_PATH="$dataset" ADDITIONAL_CONFIG="$mode_additional_config" \
    $extra_env bash "$RUNNER"
}

should_run() {
  if [[ "$SCENARIO_FILTER" != all ]]; then
    case " $SCENARIO_FILTER " in
      *" $1 "*) ;;
      *) return 1 ;;
    esac
  fi
  [[ "$MODE_FILTER" == both || "$MODE_FILTER" == "$2" ]] || return 1
}

for mode in true false; do
  should_run random-online "$mode" && run_one random-online "$mode" "$COMMON_MODEL_14B" "$RANDOM_PROMPTS" 1 1024 256 4096 8 4096
  should_run logprobs-online "$mode" && run_one logprobs-online "$mode" "$COMMON_MODEL_14B" "$LOGPROBS_PROMPTS" 1 1024 256 4096 8 4096 'BENCH_LOGPROBS=20'
  should_run prefix-repetition-online "$mode" && run_one prefix-repetition-online "$mode" "$COMMON_MODEL_14B" "$PREFIX_PROMPTS" 1 4096 256 8192 8 8192 'PREFIX_LEN=4096 PREFIX_SUFFIX_LEN=256 PREFIX_NUM_PREFIXES=10'
  should_run kv-tiering-prefix-online "$mode" && run_one kv-tiering-prefix-online "$mode" "$COMMON_MODEL_7B" "$KV_PREFIX_PROMPTS" 1 3840 256 8192 8 8192 'PREFIX_LEN=3840 PREFIX_SUFFIX_LEN=256 PREFIX_NUM_PREFIXES=10'
  should_run agent-research-online "$mode" && run_one agent-research-online "$mode" "$COMMON_MODEL_14B" "$AGENT_PROMPTS" 1 1024 256 4096 8 4096
  should_run sharegpt-online "$mode" && run_one sharegpt-online "$mode" "$COMMON_MODEL_14B" "$SHAREGPT_PROMPTS" 1 1024 128 8192 8 8192
done

echo "[done] root=$ROOT_DIR"
