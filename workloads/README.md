# Workload 说明

当前入口固定运行以下在线 workload：

- random-online
- logprobs-online
- prefix-repetition-online
- kv-tiering-prefix-online
- agent-research-online
- sharegpt-online

模型和数据集路径统一由 `config/paths.env` 提供。Agent workload 使用 benchmark 仓库中的
`scripts/traces/evoscientist-workload-custom.jsonl`。ShareGPT workload 继续使用标准的
`ShareGPT_V3_unfiltered_cleaned_split.json`，不替换为仓库内的定制样本，以保持与基线的
可比性。

不要在场景脚本中写入个人目录；如果容器中的模型或标准 ShareGPT 数据集位置不同，只修改
`paths.env` 或在 shell 中覆盖对应变量。
