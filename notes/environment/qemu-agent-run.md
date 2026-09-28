# Agent Run

Agent 任务在沙盒虚拟机内的运行手册。

## Model Endpoint

仓库根目录的 `.env` 写四个键：

```text
MODEL=<模型名>
EFFORT=<思考强度>
BASE_URL=<真实 endpoint>
API_KEY=<真实 key>
```

## Run

一条命令完成：建运行目录、起虚拟机、部署软件、安装 harness、agent 跑任务、取回输出、关虚拟机并删运行目录、扫描 key 泄漏。

```sh
python3 src/sandbox/cli/run_agent.py \
    --base-image <base镜像路径> \
    --run-dir <运行目录> \
    --output-dir <输出目录> \
    --task-dir <任务目录> \
    --tools-root <工具目录> \
    --tools <名字@版本列表> \
    --harness-root <harness目录> \
    --harness <harness名字@版本>
```

- `--base-image`：冻结的 base qcow2（本机盘）。
- `--run-dir`：虚拟机运行目录（本机盘），放本次运行的临时状态（overlay、socket、vm.json），跑完即删。
- `--output-dir`：输出目录，留存产物：run-summary.json 与 task-output/。
- `--tools-root`：工具安装包根目录，`<名字>/` 放该工具的安装脚本 `install.sh`，`<名字>/<版本>/` 放安装包。
- `--tools`：要装的工具，逗号分隔的 `<名字>@<版本>`、按序安装；每个定位安装包目录 `<工具目录>/<名字>/<版本>/` 与安装脚本 `<工具目录>/<名字>/install.sh`，脚本以安装包路径和版本为参数。
- `--harness-root`：harness 安装包根目录，按 `<名字>/<版本>/<厂商原始发行文件>` 组织，版本目录内只放一个发行文件，可加 manifest.json 记录 sha256。发行文件是归档就地解包，是裸二进制或 AppImage 就直接落到 `/task/harness` 并加可执行位。
- `--harness`：要跑的 harness，写成 `<名字>@<版本>`，定位 `<harness-root>/<名字>/<版本>/`；当前支持 codex，其他 harness 按同一布局预留。

常用参数：

- `--timeout`：agent 阶段 wall-clock 秒数，默认 14400（4 小时），超时硬终止。
- `--smp`、`--memory`：vCPU 数与内存 MiB，默认 4 与 16384。
- `--accel tcg`：宿主无 KVM 时加，功能等价、速度大幅降级。
- `--env-file`：`.env` 路径，默认当前目录的 `.env`。
- `--vnc`：实时看虚拟机桌面。加 `--vnc :5`，本机用 VNC 客户端连 `localhost:5905`。要让局域网里别的机器也能连，命令改成 `--vnc 0.0.0.0:5`，然后在那边连这台机器的 IP 加 5905。
- `--keep-vm`：保留运行目录供排查。

任务目录在 Guest 内统一用 `/task` 目录：

- `instruction.md` 与 `input/` 是任务输入目录
- `software/` 是软件安装目录
- `output/` 是任务产出目录
- `harness/` 是 harness 安装目录
- `work/` 是 agent 工作目录
- `bundles/` 是安装包解包目录

每个 bundle 的安装包解到 `/task/bundles/<名字>/`，`install.sh` 也放进同一层就地运行。安装脚本装完软件后自检，失败即中断安装，agent 不会启动。

## Result

结束原因看 run-summary.json 的 `end_reason`：

- `completed`：agent 退出码 0。
- `harness_error`：agent 退出码非 0。
- `timeout`：超时硬终止。
- `environment_failure`：虚拟机、部署或 gateway 环节失败。

`key_leak_hits` 列出产物中出现真实 API key 的文件，应为空。
