# Quickstart

运行前请先确认资源：

- 执行 `npu-smi info`，选择空闲且健康的 NPU，并设置对应的 `DEVICE`。
- 选择未被其他同学占用的端口，并设置 `PORT`，避免并发冲突。

```bash
cd /workspace/vllm-hust-profiling
source config/paths.env
source config/versions.env
export DEVICE=0 PORT=18167
export ROOT_DIR="$PROFILING_ROOT/runs/$(id -un)/$(date -u +%Y%m%dT%H%M%SZ)-npu${DEVICE}"
bash scripts/check_environment.sh
SCENARIO_FILTER=random-online MODE_FILTER=both bash scripts/run_profiling.sh
```
