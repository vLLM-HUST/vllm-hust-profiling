# Quickstart

```bash
cd /workspace/vllm-hust-profiling
source config/paths.env
source config/versions.env
export DEVICE=0 PORT=18167
export ROOT_DIR="$PROFILING_ROOT/runs/$(id -un)/$(date -u +%Y%m%dT%H%M%SZ)-npu${DEVICE}"
bash scripts/check_environment.sh
bash scripts/run_profiling.sh
```

只运行 random-online：

```bash
SCENARIO_FILTER=random-online MODE_FILTER=both bash scripts/run_profiling.sh
```

分析结果：

```bash
python scripts/analyze_top10.py --data-root "$ROOT_DIR"
python scripts/compare_v018_baseline.py --data-root "$ROOT_DIR"
```

`DEVICE` 和 `PORT` 必须与其他并发任务错开；未设置 `DEVICE` 时默认使用 0 号 NPU，不自动选择空闲卡。
