# vLLM Ascend Profiling 工作区

本仓库是团队共享的 profiling 工具和配置。它只管理脚本、workload 定义和配置，不提交模型、原始 trace、数据库或生成图片。

## 目录约定

```text
/workspace/
├── vllm-hust/                 # vLLM 主仓
├── vllm-ascend-hust/          # vLLM-Ascend 插件仓
├── vllm-hust-benchmark/       # workload、基线和 benchmark 工具
└── vllm-hust-profiling/       # 本仓库
    ├── config/                # 路径、版本、Profiler 和场景配置
    ├── scripts/               # 检查、采集、解析、分析脚本
    ├── workloads/             # 团队统一 workload 清单
    ├── runs/<user>/<run-id>/  # 本地运行数据（不入 Git）
    ├── reports/               # 报告模板和生成报告
    └── figures/               # 生成图片
```

默认根目录是 `/workspace/vllm-hust-profiling`。代码仓库、模型和数据集路径都可以通过环境变量覆盖；团队成员不应把个人缓存路径写进脚本。

## 首次使用

在 vLLM-Ascend 容器内执行：

```bash
cd /workspace/vllm-hust-profiling
source config/paths.env
source config/versions.env

export DEVICE=0                 # NPU 卡号
export PORT=18167               # 本次运行独占端口
export ROOT_DIR="$PROFILING_ROOT/runs/$(id -un)/$(date -u +%Y%m%dT%H%M%SZ)-npu${DEVICE}"

bash scripts/check_environment.sh
bash scripts/run_profiling.sh
```

运行前请确保 `MODEL_14B`、`MODEL_7B`、`AGENT_DATASET` 和 `SHAREGPT_DATASET` 指向容器内真实存在的路径。若默认 `/workspace/models` 或 `/workspace/datasets` 不适用，可在 shell 中覆盖，或修改本地未提交的 `config/paths.env`。

脚本默认采集 6 个在线场景，并分别运行 `enforce_eager=true/false`。每个成员必须使用不同的 NPU、端口和结果目录。`DEVICE` 不设置时脚本使用 0 号卡，不会自动探测空闲 NPU。

## Smoke test

```bash
cd /workspace/vllm-hust-profiling
source config/paths.env
export DEVICE=0 PORT=18167
export ROOT_DIR="$PROFILING_ROOT/runs/$(id -un)/smoke-npu${DEVICE}"
SCENARIO_FILTER=random-online MODE_FILTER=both RANDOM_PROMPTS=32 \
  bash scripts/run_profiling.sh
```

## 分析已有数据

`run_profiling.sh` 完成后，使用实际的运行根目录执行：

```bash
RUN_DIR=/workspace/vllm-hust-profiling/runs/<user>/<run-id>
cd /workspace/vllm-hust-profiling
python scripts/analyze_top10.py --data-root "$RUN_DIR"
python scripts/compare_v018_baseline.py \
  --data-root "$RUN_DIR" \
  --benchmark-root /workspace/vllm-hust-benchmark
```

分析结果写入该运行目录的 `analysis/` 和 `figures/`。来源是 benchmark JSON、msServiceProfiler 导出的 `request.csv`、`batch.csv`、`chrome_tracing.json` 及 `profiler.db`；这些文件和生成图片默认被 Git 忽略。

## 更新代码后使用

```bash
cd /workspace/vllm-hust-profiling
git pull --ff-only
bash scripts/check_environment.sh
```

版本约束见 [`config/versions.env`](config/versions.env)。环境检查失败时不要继续采集；先确认主仓和 vLLM-Ascend 仓库 commit、模型、数据集以及容器内 `ms_service_profiler` 是否匹配。

更短的命令示例见 [`QUICKSTART.md`](QUICKSTART.md)，workload 说明见 [`workloads/README.md`](workloads/README.md)。
