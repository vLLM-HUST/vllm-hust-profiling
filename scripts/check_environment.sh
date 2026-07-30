#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROFILING_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$PROFILING_ROOT/config/paths.env"
# shellcheck disable=SC1091
source "$PROFILING_ROOT/config/versions.env"

if [[ -f /usr/local/Ascend/ascend-toolkit/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
fi
if [[ -f /usr/local/Ascend/cann-9.0.0/set_env.sh ]]; then
  # shellcheck disable=SC1091
  source /usr/local/Ascend/cann-9.0.0/set_env.sh
fi

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ -d "$VLLM_ROOT" ]] || fail "vLLM repository missing: $VLLM_ROOT"
[[ -d "$VLLM_ASCEND_ROOT" ]] || fail "vllm-ascend repository missing: $VLLM_ASCEND_ROOT"
[[ -f "$PROFILING_ROOT/config/ms_service_profiler_config.json" ]] || fail "profiler config missing"
command -v vllm >/dev/null 2>&1 || fail "vllm command is unavailable"

actual_vllm="$(git -C "$VLLM_ROOT" rev-parse HEAD)"
actual_ascend="$(git -C "$VLLM_ASCEND_ROOT" rev-parse HEAD)"
if [[ -n "${VLLM_COMMIT:-}" && "$actual_vllm" != "$VLLM_COMMIT" ]]; then
  fail "vllm commit $actual_vllm != requested $VLLM_COMMIT"
fi
if [[ -n "${VLLM_ASCEND_COMMIT:-}" && "$actual_ascend" != "$VLLM_ASCEND_COMMIT" ]]; then
  fail "vllm-ascend commit $actual_ascend != requested $VLLM_ASCEND_COMMIT"
fi

for path in "$MODEL_14B" "$MODEL_7B" "$AGENT_DATASET" "$SHAREGPT_DATASET"; do
  [[ -e "$path" ]] || fail "required model/dataset path missing: $path"
done

if command -v python >/dev/null 2>&1; then
  python -c 'import ms_service_profiler' >/dev/null 2>&1 || fail "ms_service_profiler is unavailable in the active container"
  python -c 'import msguard' >/dev/null 2>&1 || fail "msguard is unavailable in the active container; msServiceProfiler parser requires it"
fi

echo "environment_ok"
echo "vllm=$actual_vllm"
echo "vllm_ascend=$actual_ascend"
echo "vllm_commit_check=${VLLM_COMMIT:-auto}"
echo "vllm_ascend_commit_check=${VLLM_ASCEND_COMMIT:-auto}"
echo "model_14b=$MODEL_14B"
echo "model_7b=$MODEL_7B"
