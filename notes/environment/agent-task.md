# Agent Task Run

Agent 任务在沙盒 VM 内的运行手册（harness 层，代码在 `src/sandbox/deploy/agent_run.py` 与 `src/sandbox/deploy/api_relay.py`）。

## Model Endpoint

仓库根目录的 `.env` 写四个键（该文件被 git 忽略，值只在 relay 进程内存中使用，不进 Guest）。

```text
MODEL=<模型名>
EFFORT=<推理档位>
BASE_URL=<真实 endpoint>
API_KEY=<真实 key>
```

## Assets

- `artifacts/harnesses/codex/0.156.1.tar.gz`：codex harness 分发包，运行时上传进 Guest 安装。
- `artifacts/tools/python/`、`artifacts/tools/mujoco/`：离线软件 bundle（含安装脚本），运行时部署进 Guest。

## Run

```sh
python3 src/sandbox/cli/agent_run.py --task-dir local/tasks --run-id <id>
```

任务目录含 `instruction.md` 与可选的 `input/`。一条命令完成：建 VM（gateway 网络）、部署软件、安装 codex、跑 agent、取回 `/task/output`、扫描 key 泄漏、关 VM 并删运行目录。产物在 `artifacts/runs/agent-runs/<id>/`（run-summary.json 与 task-output/）。

常用参数：

- `--timeout 14400`：agent 阶段 wall-clock 秒数，默认 4 小时，超时硬终止。
- `--smp`、`--memory`：vCPU 数与内存 MiB。
- `--accel tcg`：宿主无 KVM 时加，功能等价、速度大幅降级。
- `--runs-root`：运行目录根，默认随 `VMSTORE`。
- `--env-file`：`.env` 路径，默认仓库根目录下的 `.env`。
- `--keep-vm`：保留运行目录供排查。

## Result

结束原因看 run-summary.json 的 `end_reason`：

- `completed`：agent 退出码 0。
- `harness_error`：agent 退出码非 0。
- `timeout`：超时硬终止。
- `environment_failure`：VM、部署或网关环节失败。

`key_leak_hits` 列出产物中出现真实 API key 的文件，应为空。
