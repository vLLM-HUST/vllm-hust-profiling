# 团队共享 Profiling 工作区

本目录提供统一的 Ascend vLLM profiling 入口。代码仓库位于 `/workspace`，profiling
脚本、配置、运行结果和报告位于 `/workspace/profiling`。

## 目录约定

```text
config/       路径、版本、profiler 和场景配置
scripts/      环境检查、采集、解析、分析脚本
workloads/    团队统一 workload 参数
runs/         每个用户/时间/NPU 的独立结果目录
reports/      报告模板和生成报告
figures/      报告图片
```

## 一键运行

在 Ascend profiling 容器中执行：

```bash
cd /workspace/profiling

source config/paths.env
source config/versions.env

export DEVICE=1
export PORT=18167
export TEAM_USER="$(id -un)"
export RUN_TAG="$(date -u +%Y%m%dT%H%M%SZ)-npu${DEVICE}"
export ROOT_DIR="$PROFILING_ROOT/runs/$TEAM_USER/$RUN_TAG"

bash scripts/check_environment.sh
bash scripts/run_profiling.sh
```

脚本会运行 6 个在线场景，并分别采集 `enforce_eager=true/false`。`eager=false` 的
CPU binding 配置由入口脚本自动设置为 `{"enable_cpu_binding":false}`。

## 快速 smoke test

```bash
cd /workspace/profiling
source config/paths.env
source config/versions.env

export DEVICE=1
export PORT=18167
export TEAM_USER="$(id -un)"
export ROOT_DIR="$PROFILING_ROOT/runs/$TEAM_USER/smoke-npu${DEVICE}"

SCENARIO_FILTER=random-online \
MODE_FILTER=both \
RANDOM_PROMPTS=32 \
bash scripts/run_profiling.sh
```

不同成员同时运行时必须使用不同的 NPU、端口和 `ROOT_DIR`。

## 结果位置

```text
runs/<user>/<run-id>/
├── versions.txt
├── <scenario>-eager-true/
│   ├── raw/
│   ├── parsed/
│   ├── benchmark/
│   └── logs/
└── <scenario>-eager-false/
```

运行脚本在服务停止后自动执行 msServiceProfiler 解析。只有 `benchmark/random-online.json`、
`parsed/request.csv`、`parsed/batch.csv` 和 `parsed/chrome_tracing.json` 均存在时，结果才
适合进入后续分析。

## 当前固定版本

版本记录在 [`config/versions.env`](config/versions.env)。环境检查失败时不要继续运行，先
将两个代码仓库切换到指定 commit，或由团队负责人更新版本文件并重新确认基线。
