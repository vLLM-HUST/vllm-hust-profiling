# Quickstart

```bash
cd /workspace/vllm-hust-profiling
source config/paths.env
source config/versions.env
export DEVICE=0 PORT=18167
export ROOT_DIR="$PROFILING_ROOT/runs/$(id -un)/$(date -u +%Y%m%dT%H%M%SZ)-npu${DEVICE}"
bash scripts/check_environment.sh
SCENARIO_FILTER=random-online MODE_FILTER=both bash scripts/run_profiling.sh
```
