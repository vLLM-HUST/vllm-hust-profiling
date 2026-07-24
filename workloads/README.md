# Workload 说明

当前入口固定运行以下在线 workload：

- random-online
- logprobs-online
- prefix-repetition-online
- kv-tiering-prefix-online
- agent-research-online
- sharegpt-online

模型和数据集路径统一由 `config/paths.env` 提供。不要在场景脚本中写入个人目录；如果
容器中的模型或数据集位置不同，只修改 `paths.env` 或在 shell 中覆盖对应变量。
