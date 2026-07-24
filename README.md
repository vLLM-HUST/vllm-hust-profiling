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

运行前请确保 `MODEL_14B`、`MODEL_7B`、`AGENT_DATASET` 和 `SHAREGPT_DATASET` 指向容器内真实存在的路径。未显式设置模型路径时，`config/paths.env` 会优先从 `/root/.cache/huggingface/hub` 的 `refs/main` 动态解析对应 snapshot；也可通过 `HF_CACHE_ROOT` 指定其他 Hugging Face cache 根目录。Agent 数据默认使用 benchmark 仓库中的 `scripts/traces/evoscientist-workload-custom.jsonl`；ShareGPT 会依次从 `/workspace/datasets`、benchmark 数据集目录和 `/data/shared_datasets` 查找标准 `ShareGPT_V3_unfiltered_cleaned_split.json`，不使用 benchmark 仓库内的定制 ShareGPT 样本。若路径不适用，可通过环境变量覆盖。

脚本默认采集 6 个在线场景，并分别运行 `enforce_eager=true/false`。每个成员必须使用不同的 NPU、端口和结果目录。`DEVICE` 不设置时脚本使用 0 号卡，不会自动探测空闲 NPU。

启动前会 fail fast 检查 `DEVICE` 是否能被 `npu-smi` 查询且健康状态为 `OK`，并检查 `PORT` 是否可以绑定。任一检查失败都会直接退出，不启动 vLLM，也不会开始 workload。

## 是否需要 worktree

- 只做 profiling：不需要 worktree。
- 多个分支并行测试：建议使用 worktree，避免切换主仓。
- profiling 前后修改源码、提交实验 patch：建议使用 worktree。
- 只切换到最新代码并运行：直接使用 `/workspace/vllm-hust` 和 `/workspace/vllm-ascend-hust` 即可。

profiling 脚本不会修改 vLLM 或 vLLM-Ascend 源码，运行数据写入本仓库的 `runs/` 目录。

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

版本配置见 [`config/versions.env`](config/versions.env)。默认不固定 commit，环境检查会使用并记录两个代码仓库当前的 `HEAD`；只有显式设置 `VLLM_COMMIT` 或 `VLLM_ASCEND_COMMIT` 时，才会执行对应的 commit 强校验。需要复现实验时，可以在运行前设置这两个变量。

更短的命令示例见 [`QUICKSTART.md`](QUICKSTART.md)，workload 说明见 [`workloads/README.md`](workloads/README.md)。
