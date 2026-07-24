#!/usr/bin/env bash
set -Eeuo pipefail

# Run this script inside the vLLM-Ascend container.
# It starts vLLM, runs the upstream random online benchmark, stops vLLM,
# and parses the MS Service Profiler output.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

RUN_DIR="${RUN_DIR:-$TASK_DIR/outputs/msserviceprofiler-random-online}"
RAW_DIR="$RUN_DIR/raw"
PARSED_DIR="$RUN_DIR/parsed"
BENCH_DIR="$RUN_DIR/benchmark"
LOG_DIR="$RUN_DIR/logs"
RUNTIME_CONFIG_FILE="$RUN_DIR/ms_service_profiler_config.json"

MODEL_PATH="${MODEL_PATH:-/data/shared_models/Qwen2.5-7B-Instruct}"
if [[ -z "${SERVED_MODEL_NAME:-}" ]]; then
  MODEL_BASENAME="$(basename -- "$MODEL_PATH" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9.' '-')"
  SERVED_MODEL_NAME="profiling-${MODEL_BASENAME%-}"
fi
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-18167}"
DEVICE="${DEVICE:-1}"
TP_SIZE="${TP_SIZE:-1}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-4}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-4096}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.75}"
ENFORCE_EAGER="${ENFORCE_EAGER:-1}"

NUM_PROMPTS="${NUM_PROMPTS:-500}"
REQUEST_RATE="${REQUEST_RATE:-2}"
RANDOM_INPUT_LEN="${RANDOM_INPUT_LEN:-1024}"
RANDOM_OUTPUT_LEN="${RANDOM_OUTPUT_LEN:-128}"
SEED="${SEED:-12345}"
PROFILE_SCENARIO="${PROFILE_SCENARIO:-random-online}"
BENCH_INPUT_LEN="${BENCH_INPUT_LEN:-$RANDOM_INPUT_LEN}"
BENCH_OUTPUT_LEN="${BENCH_OUTPUT_LEN:-$RANDOM_OUTPUT_LEN}"
BENCH_LOGPROBS="${BENCH_LOGPROBS:-20}"
PREFIX_LEN="${PREFIX_LEN:-3840}"
PREFIX_SUFFIX_LEN="${PREFIX_SUFFIX_LEN:-256}"
PREFIX_NUM_PREFIXES="${PREFIX_NUM_PREFIXES:-10}"
DATASET_PATH="${DATASET_PATH:-}"
BENCH_BACKEND="${BENCH_BACKEND:-openai}"
BENCH_ENDPOINT="${BENCH_ENDPOINT:-/v1/completions}"
ADDITIONAL_CONFIG="${ADDITIONAL_CONFIG:-}"

VLLM_REPO="${VLLM_REPO:-/workspace/vllm-hust}"
VLLM_BIN="${VLLM_BIN:-vllm}"
PYTHON_BIN="${PYTHON_BIN:-python}"
CONFIG_FILE="${CONFIG_FILE:-$TASK_DIR/config/ms_service_profiler_config.json}"
PROFILER_TIMELIMIT="${PROFILER_TIMELIMIT:-600}"

export ASCEND_RT_VISIBLE_DEVICES="${ASCEND_RT_VISIBLE_DEVICES:-$DEVICE}"
export ASCEND_VISIBLE_DEVICES="${ASCEND_VISIBLE_DEVICES:-$DEVICE}"
export VLLM_PLUGINS="${VLLM_PLUGINS:-ascend,msserviceprofiler}"
export TORCH_DEVICE_BACKEND_AUTOLOAD="${TORCH_DEVICE_BACKEND_AUTOLOAD:-0}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"
export no_proxy="${no_proxy:-127.0.0.1,localhost}"
export PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-$RUN_DIR/prometheus}"

if [[ -f /usr/local/Ascend/ascend-toolkit/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
fi

if [[ -f /usr/local/Ascend/cann-9.0.0/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/cann-9.0.0/set_env.sh
fi

if [[ -z "${PROFILING_SYMBOLS_PATH:-}" ]]; then
  for candidate in \
    /usr/local/Ascend/ascend-toolkit/latest/python/site-packages/ms_service_profiler/patcher/vllm/config/service_profiling_symbols.yaml \
    /usr/local/Ascend/cann-9.0.0/python/site-packages/ms_service_profiler/patcher/vllm/config/service_profiling_symbols.yaml; do
    if [[ -f "$candidate" ]]; then
      export PROFILING_SYMBOLS_PATH="$candidate"
      break
    fi
  done
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: profiler config not found: $CONFIG_FILE" >&2
  exit 2
fi
if [[ ! -d "$VLLM_REPO" ]]; then
  echo "ERROR: vLLM repository not found: $VLLM_REPO" >&2
  exit 2
fi
if [[ ! -f "${PROFILING_SYMBOLS_PATH:-}" ]]; then
  echo "ERROR: PROFILING_SYMBOLS_PATH is not set to a readable file" >&2
  exit 2
fi

# The current vLLM-Ascend development tree has an optional torch_npu
# preflight.  In this container the probe can hang in a child process even
# when the selected NPU is healthy; keep the historical profiling behavior by
# applying the opt-out after all Ascend set_env.sh files have run.
export VLLM_ASCEND_TORCH_PREFLIGHT="${VLLM_ASCEND_TORCH_PREFLIGHT:-0}"
echo "[profile] VLLM_ASCEND_TORCH_PREFLIGHT=$VLLM_ASCEND_TORCH_PREFLIGHT"

mkdir -p "$RAW_DIR" "$PARSED_DIR" "$BENCH_DIR" "$LOG_DIR"
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"

# The profiler reads its output directory from the JSON config, not from
# RUN_DIR.  Build a per-run config so repeated runs cannot write into an old
# run directory.  Profiling is always enabled by this profiling script.
"$PYTHON_BIN" - "$CONFIG_FILE" "$RUNTIME_CONFIG_FILE" "$RAW_DIR" "$PROFILER_TIMELIMIT" <<'PY'
import json
import pathlib
import sys

source, destination, raw_dir = map(pathlib.Path, sys.argv[1:4])
config = json.loads(source.read_text())
config["enable"] = 1
config["prof_dir"] = str(raw_dir)
config["timelimit"] = int(sys.argv[4]) if len(sys.argv) > 4 else config.get("timelimit", 600)
pathlib.Path(destination).write_text(json.dumps(config, indent=4) + "\n")
PY
export SERVICE_PROF_CONFIG_PATH="$RUNTIME_CONFIG_FILE"

echo "[profile] task_dir=$TASK_DIR"
echo "[profile] run_dir=$RUN_DIR"
echo "[profile] model=$MODEL_PATH"
echo "[profile] port=$PORT"
echo "[profile] device=$DEVICE"
echo "[profile] request_rate=$REQUEST_RATE"
echo "[profile] num_prompts=$NUM_PROMPTS"
echo "[profile] symbols=$PROFILING_SYMBOLS_PATH"
echo "[profile] profiler_config=$SERVICE_PROF_CONFIG_PATH"
echo "[profile] profiler_timelimit=$PROFILER_TIMELIMIT"
echo "[profile] enforce_eager=$ENFORCE_EAGER"
echo "[profile] scenario=$PROFILE_SCENARIO"

if command -v npu-smi >/dev/null 2>&1; then
  npu-smi info || {
    echo "ERROR: npu-smi failed; verify that the container has NPU devices." >&2
    exit 3
  }
fi

health_check() {
  "$PYTHON_BIN" - "$PORT" <<'PY'
import sys
import urllib.request

port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
}

cleanup() {
  if [[ -n "${VLLM_PID:-}" ]] && kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "[profile] stopping vLLM pid=$VLLM_PID"
    kill -TERM "$VLLM_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$VLLM_PID" 2>/dev/null || break
      sleep 1
    done
    kill -KILL "$VLLM_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[profile] starting vLLM"
VLLM_SERVER_ARGS=(
  serve "$MODEL_PATH"
  --served-model-name "$SERVED_MODEL_NAME"
  --host "$HOST"
  --port "$PORT"
  --tensor-parallel-size "$TP_SIZE"
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-seqs "$MAX_NUM_SEQS"
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
)
if [[ -n "$ADDITIONAL_CONFIG" ]]; then
  VLLM_SERVER_ARGS+=(--additional-config "$ADDITIONAL_CONFIG")
fi
if [[ "$ENFORCE_EAGER" == "1" || "$ENFORCE_EAGER" == "true" ]]; then
  VLLM_SERVER_ARGS+=(--enforce-eager)
fi
"$VLLM_BIN" "${VLLM_SERVER_ARGS[@]}" \
  > >(tee "$LOG_DIR/vllm.log") 2>&1 &
VLLM_PID=$!

echo "[profile] waiting for /health"
for _ in $(seq 1 180); do
  if health_check; then
    break
  fi
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "ERROR: vLLM exited before becoming healthy" >&2
    exit 4
  fi
  sleep 2
done
if ! health_check; then
  echo "ERROR: vLLM did not become healthy within 360 seconds" >&2
  exit 4
fi

if command -v "$VLLM_BIN" >/dev/null 2>&1; then
  echo "[profile] running upstream vLLM CLI benchmark: $VLLM_BIN bench serve"
  case "$PROFILE_SCENARIO" in
    agent-research-online)
      BENCH_BACKEND="openai-chat"
      BENCH_ENDPOINT="/v1/chat/completions"
      DATASET_PATH="${DATASET_PATH:-/workspace/vllm-hust-benchmark/scripts/traces/evoscientist-workload-custom.jsonl}"
      ;;
    sharegpt-online)
      BENCH_BACKEND="openai-chat"
      BENCH_ENDPOINT="/v1/chat/completions"
      if [[ -z "$DATASET_PATH" ]]; then
        echo "ERROR: DATASET_PATH is required for sharegpt-online" >&2
        exit 5
      fi
      ;;
  esac
  BENCH_ARGS=(
    bench serve
    --backend "$BENCH_BACKEND"
    --base-url "http://127.0.0.1:$PORT"
    --endpoint "$BENCH_ENDPOINT"
    --model "$SERVED_MODEL_NAME"
    --tokenizer "$MODEL_PATH"
    --num-prompts "$NUM_PROMPTS"
    --request-rate "$REQUEST_RATE"
    --seed "$SEED"
    --save-result
    --result-dir "$BENCH_DIR"
    --result-filename random-online.json
  )
  case "$PROFILE_SCENARIO" in
    random-online)
      BENCH_ARGS+=(--dataset-name random --random-input-len "$RANDOM_INPUT_LEN" --random-output-len "$RANDOM_OUTPUT_LEN")
      ;;
    logprobs-online)
      BENCH_ARGS+=(--dataset-name random --input-len "$BENCH_INPUT_LEN" --output-len "$BENCH_OUTPUT_LEN" --logprobs "$BENCH_LOGPROBS")
      ;;
    prefix-repetition-online|kv-tiering-prefix-online)
      BENCH_ARGS+=(
        --dataset-name prefix_repetition
        --prefix-repetition-prefix-len "$PREFIX_LEN"
        --prefix-repetition-suffix-len "$PREFIX_SUFFIX_LEN"
        --prefix-repetition-num-prefixes "$PREFIX_NUM_PREFIXES"
        --prefix-repetition-output-len "$BENCH_OUTPUT_LEN"
      )
      ;;
    agent-research-online)
      BENCH_ARGS+=(
        --dataset-name custom
        --dataset-path "$DATASET_PATH"
        --output-len "$BENCH_OUTPUT_LEN"
      )
      ;;
    sharegpt-online)
      BENCH_ARGS+=(
        --dataset-name sharegpt
        --dataset-path "$DATASET_PATH"
        --output-len "$BENCH_OUTPUT_LEN"
      )
      ;;
    *)
      echo "ERROR: unsupported PROFILE_SCENARIO for this runner: $PROFILE_SCENARIO" >&2
      exit 5
      ;;
  esac
  set +e
  "$VLLM_BIN" "${BENCH_ARGS[@]}" 2>&1 | tee "$LOG_DIR/benchmark.log"
  BENCHMARK_STATUS=${PIPESTATUS[0]}
  set -e
  echo "[profile] benchmark_exit_code=$BENCHMARK_STATUS"

  # vLLM can return non-zero during its final async shutdown path even after
  # all benchmark requests have completed.  Do not discard valid profiling
  # data in that case; use the saved result JSON as the admission gate.
  if [[ ! -s "$BENCH_DIR/random-online.json" ]]; then
    echo "ERROR: benchmark result JSON was not generated" >&2
    exit 5
  fi
  if ! "$PYTHON_BIN" - "$BENCH_DIR/random-online.json" <<'PY'
import json
import sys

result = json.loads(open(sys.argv[1]).read())
completed = int(result.get("completed", 0))
failed = int(result.get("failed", 0))
if completed <= 0 or failed > 0:
    raise SystemExit(f"invalid benchmark result: completed={completed}, failed={failed}")
PY
  then
    echo "ERROR: benchmark result contains no complete successful workload" >&2
    exit 5
  fi
  if [[ "$BENCHMARK_STATUS" -ne 0 ]]; then
    echo "[profile] warning: benchmark exited $BENCHMARK_STATUS after a valid result; continuing to parse"
  fi
else
  echo "ERROR: vLLM CLI not found: $VLLM_BIN" >&2
  exit 5
fi

echo "[profile] waiting for profiler data to flush"
sleep 10

# Stop the server before parsing so profiler writers have exited and closed
# their database files.
cleanup
VLLM_PID=""

if ! find "$RAW_DIR" -type f -print -quit | grep -q .; then
  echo "ERROR: no profiler data was written under $RAW_DIR" >&2
  exit 6
fi

echo "[profile] parsing profiler data"
PARSER_PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
PYTHONPATH="$PARSER_PYTHONPATH" "$PYTHON_BIN" -m ms_service_profiler parse \
  --input-path "$RAW_DIR" \
  --output-path "$PARSED_DIR" \
  --format db csv json \
  2>&1 | tee "$LOG_DIR/parse.log"

if grep -Eq '[1-9][0-9]* tasks failed' "$LOG_DIR/parse.log"; then
  echo "ERROR: msServiceProfiler completed with failed tasks; inspect $LOG_DIR/parse.log" >&2
  exit 7
fi

echo "[profile] completed"
find "$RUN_DIR" -maxdepth 3 -type f -printf '%p\\n' | sort
