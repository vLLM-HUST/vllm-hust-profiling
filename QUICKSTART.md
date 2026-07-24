# Profiling Quickstart

```bash
cd /workspace/profiling
source config/paths.env
source config/versions.env

export DEVICE=1
export PORT=18167
export TEAM_USER="$(id -un)"
export ROOT_DIR="$PROFILING_ROOT/runs/$TEAM_USER/$(date -u +%Y%m%dT%H%M%SZ)-npu${DEVICE}"

bash scripts/check_environment.sh
bash scripts/run_profiling.sh
```

只运行 `random-online`：

```bash
SCENARIO_FILTER=random-online MODE_FILTER=both bash scripts/run_profiling.sh
```

查看结果：

```bash
find "$ROOT_DIR" -maxdepth 3 -type f | sort
```
