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

上面是最简单的 profiling 方式：只运行 `random-online`，并分别采集
`enforce_eager=true/false`。

完整多场景 profiling：

```bash
bash scripts/check_environment.sh
bash scripts/run_profiling.sh
```

该方式会运行全部在线场景和两种 `enforce_eager` 模式，耗时更长。

分析结果：

```bash
python scripts/analyze_top10.py --data-root "$ROOT_DIR"
python scripts/compare_v018_baseline.py --data-root "$ROOT_DIR"
```

`DEVICE` 和 `PORT` 必须与其他并发任务错开；未设置 `DEVICE` 时默认使用 0 号 NPU，不自动选择空闲卡。

未设置 `MODEL_14B` 或 `MODEL_7B` 时，配置会自动从 `/root/.cache/huggingface/hub` 查找对应模型的 snapshot；也可以在运行前显式覆盖模型路径。

启动脚本会先检查 NPU 健康状态和端口绑定情况；检查失败会立即退出，不启动服务。

Quickstart 不需要 worktree，直接使用容器中的 `/workspace/vllm-hust` 和
`/workspace/vllm-ascend-hust`。只有需要并行测试多个分支，或需要修改源码并提交实验
patch 时，才建议创建 worktree。

默认使用 vLLM 和 vLLM-Ascend 当前 checkout 的 commit；需要固定版本时，运行前设置 `VLLM_COMMIT` 和/或 `VLLM_ASCEND_COMMIT`。
