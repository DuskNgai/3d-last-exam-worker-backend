# Agent Run

Agent 任务在沙盒虚拟机内的运行手册。

## Model Endpoint

`.keys.json`：

```json
{
  "<厂商标识>": {"BASE_URL": "<真实 endpoint>", "API_KEY": "<真实 key>"}
}
```

厂商在凭据表里没有条目就在开虚拟机之前报错。

## Run

一条命令按序完成：

1. 建运行目录，起虚拟机。
2. 探一次模型端点。
3. 部署软件。
4. 安装 harness。
5. agent 跑任务。
6. 取回输出。
7. 关虚拟机，删运行目录。
8. 扫描 key 泄漏。

端点探测是让 guest 经 guestfwd 打一次 `/v1/models`，要求上游返回 200（最多试 3 次；元数据请求，不产生 token 消耗）。不过就停在 agent 启动前，`end_reason` 记 `environment_failure`。只实现对话接口、没有模型列表的端点会先在这里被拦下。

```bash
python3 src/sandbox/cli/run_agent.py \
    --base-image <base镜像路径> \
    --run-dir <运行目录> \
    --output-dir <输出目录> \
    --tools-root <工具包目录> \
    --harness-root <harness目录> \
    --task-dir <任务目录> \
    --agent-config <agent配置路径>
```

- `--base-image`：冻结的 base qcow2（本机盘）。
- `--run-dir`：虚拟机运行目录（本机盘），放本次运行的临时状态（overlay、socket、vm.json），跑完即删。一个目录同时只服务一个 run：目录里的 run.lock 被活着的 run 持有时，新的 run 在动任何东西之前就报错退出。
- `--output-dir`：输出目录，留存产物：`run-summary.json` 与 `output/`。
- `--tools-root`：工具包根目录，按 `<名字>/install.sh` 与 `<名字>/<版本>/` 组织。
- `--harness-root`：harness 安装包根目录，按 `<名字>/<版本>/<厂商原始发行文件>` 组织，版本目录内只放一个发行文件，可加 manifest.json 记录 sha256；根目录下放共用的 `install.sh`。跑哪个 harness 由 agent 配置的 `harness` 字段给出，写成 `<名字>@<版本>`，定位 `<harness-root>/<名字>/<版本>/`。
- `--task-dir`：任务目录，内含 `instruction.md`、可选的 `input/`，以及 `task.json`。
- `--agent-config`：agent 配置路径。
- `--keys`：凭据表路径，默认仓库根 `.keys.json`。

常用参数：

- `--time-budget`：agent 阶段 wall-clock 秒数上限，默认取任务卡的 `budget.maximum_minutes`（分钟 * 60），显式给定时覆盖；超限硬终止。
- `--token-budget`：agent 阶段累计模型 token 上限，单位 M tokens，默认取 `budget.maximum_tokens`，显式给定时覆盖；超限硬终止。
- `--smp`、`--memory`：vCPU 数与内存 MiB，默认 4 与 16384。
- `--accel tcg`：宿主无 KVM 时加，功能等价，速度慢得多。
- `--vnc`：实时看虚拟机桌面。加 `--vnc :5`，本机用 VNC 客户端连 `localhost:5905`。要让局域网里别的机器也能连，命令改成 `--vnc 0.0.0.0:5`，在另一台机器上连 `<本机 IP>:5905`。
- `--keep-vm`：保留运行目录供排查。

### Task Layout

任务目录在 guest 内统一用 `/task` 目录：

- `instruction.md` 与 `input/` 是任务输入目录
- `software/` 是软件安装目录
- `output/` 是任务产出目录
- `harness/` 是 harness 安装目录
- `work/` 是 agent 工作目录
- `bundles/` 是安装包解包目录

每个安装包解到 `/task/bundles/<名字>/`，工具包自带的 `install.sh` 在该目录就地运行。安装脚本装完软件后自检，失败即中断安装，agent 不会启动。

## Result

运行结束后输出目录留下三样东西：`run-summary.json`（本次运行的摘要）、`output/`（guest 里 `/task/output` 的全部产物，也正好是评分要的 `output/`）、以及 relay 写的 `usage.jsonl` 与 `relay.log`（用量与请求记录）。

### End Reason

`run-summary.json` 的 `end_reason`：

| 值 | 含义 |
| --- | --- |
| `completed` | agent 退出码 0 |
| `harness_error` | agent 退出码非 0 |
| `time_budget` | agent 阶段超过 wall-clock 上限，硬终止 |
| `token_budget` | agent 阶段累计 token 超过上限，硬终止 |
| `environment_failure` | 虚拟机、部署或 gateway 环节失败 |

### Summary

| 字段 | 含义 |
| --- | --- |
| `agent_wall_seconds`、`agent_exitcode` | agent 阶段自身的时长与退出码，两个预算的判定都以 agent 阶段为准 |
| `time_budget`、`token_budget` | 本次生效的两个上限，来自任务卡，`--time-budget`/`--token-budget` 可覆盖 |
| `tokens_input`、`tokens_output`、`tokens_used` | relay 计量的累计用量；上游若不回 usage，就是 0 |
| `tools`、`harness`、`harness_version`、`model`、`effort`、`provider` | 这次跑的是什么 |
| `gateway_status`、`deploy`、`stop_level`、`retrieved` | 环境探测、部署、关机档位与取回清单 |
| `key_leak_hits` | 产物中出现真实 API key 的文件，应为空 |

### Token

relay 顺带计量每次模型调用：转发响应时解析 payload 里的标准 usage（Responses 的 `input_tokens`/`output_tokens`，Chat Completions 的 `prompt_tokens`/`completion_tokens`，重试的调用照算），每完成一个响应就往 `usage.jsonl` 追一行（`{"path", "status", "input_tokens", "output_tokens"}`）；relay 自己的请求日志每行一条 `METHOD /path -> status`，写在 `relay.log`。两个文件都在运行目录里生成，运行结束复制进输出目录。

## Cleanup

宿主进程被杀或终端断开时，虚拟机不会自己消失；`--keep-vm` 留下的运行目录也一样，都要手动收拾。

锁跟着进程走，进程一死锁就没了：残留目录里的 run.lock 不会拦住新的 run，新的 run 照样进去，然后因为 overlay 已存在而报错。

```sh
pgrep -af qemu-system-x86_64      # 残留的 QEMU，命令行里带着它用的运行目录
kill "$(cat <运行目录>/vm.pid)"    # 先 SIGTERM，不退再 kill -9
rm -rf <运行目录>
```
