# Tool Install Test

工具包在冻结 base 镜像里逐个安装、并探一次 Python 接口的手册。用来确认每个工具归档在 guest 内装得上、装完还能用，与 agent 运行解耦。

## Scope

一次跑完 tools 根目录下的全部 `<名字>/<版本>/`。安装这一半不做额外判定：每个工具自带的 `install.sh` 已经带 `set -euo pipefail` 并在结尾自检（版本号、`import mujoco`、conda 环境断言），退出码就是结论。装完再补一次 installer 彼此不做的检查：以 Python 接口问一遍每个工具，`bpy`、`FreeCAD`、`mujoco` 各归各的。虚拟机用 `-nic none` 起，没有网络。

安装顺序写死在探针表里，不按目录名排序：`python` 必须先于 `mujoco`，因为 mujoco 的 installer 把 wheel 装进 python 工具解出来的那个解释器。

## Run

```sh
python3 scripts/check_tool_install.py \
    --base-image <base镜像路径> \
    --run-dir <运行目录> \
    --tools-root <工具包目录> \
    --output-dir <输出目录>
```

- `--base-image`：冻结的 base qcow2。
- `--run-dir`：虚拟机运行目录，放本次运行的 overlay、socket 与 vm.json，跑完即删。
- `--tools-root`：工具包根目录，按 `<名字>/install.sh` 与 `<名字>/<版本>/` 组织。
- `--output-dir`：输出目录，收 tool-install-report.json。
- `--tool`：可重复，只测指定的工具名字；不给就扫全目录。给的名字顺序无所谓，脚本仍按表序安装。`python` 与 `mujoco` 互相依赖（前者是后者 wheel 的宿主，后者的探针跑在前者的解释器上），要选就一直选。

常用参数：

- `--smp`、`--memory`：vCPU 数与内存 MiB，默认 4 与 8192。
- `--accel tcg`：宿主无 KVM 时加，功能等价，速度慢得多。
- `--vnc`：实时看虚拟机桌面，例如 `--vnc :5`。
- `--keep-vm`：保留运行目录供排查。

`--run-dir` 与 `--base-image` 都必须在本机盘上。网络文件系统建不了 unix socket，QEMU 的 QMP 与 QGA 会在 bind 时就失败。这一轮的 --run-dir 要留出几 GiB：工具包比 harness 包大，python 的 conda 闭包与 freecad 的 AppImage 都要走一遍上传。

## Flow

一台虚拟机，按表序循环每个工具：

1. 清掉 guest 里的 `/task/bundles/<名字>`，把整个工具目录（`install.sh` 加版本目录）打成 tar 上传解包。这条路径与真实运行一致，包含哪些文件也一致。
2. 就地执行它自带的 `install.sh`，退出码非 0 即判 `install_failed`，installer 的完整输出打到本进程 stdout，不进报告。
3. 全部装完后进入探针轮：对每个装成功的工具，按表里给的候选命令依次试，第一个退出码 0 的就是证据。候选以 agent 身份执行，整条命令套 `timeout`。
4. 写一条记录。

一台机器循环装完是刻意的取舍：只付一次启动开销。代价是前一个工具若留下痕迹，后一个就不是从绝对干净的状态开始；工具彼此只共享 `/task/software` 这个父目录，python 与 mujoco 之间还刻意有依赖，所以这个代价目前是零。

## Verdict

每个工具问什么接口是写死的，见 `scripts/check_tool_install.py` 的 `PROBES`。表里没有的工具名，在开虚拟机之前就报错，不去猜。表里空元组表示该工具没有可问的 Python 接口：`sdf-view` 是 GUI 查看器，安装脚本里的 `--version` 已经证明二进制能跑。

`verdict` 四档：

- `install_failed`：上传、解包或 `install.sh` 非 0 退出，记录里带错误原文。
- `probe_failed`：装上了，但探针全部候选都没退出 0。记录里留的是最后一个候选的输出，例如缺共享库、`import bpy` 报错。
- `usable`：探针退出码 0。
- `installed`：没有声明的 Python 接口，安装即结论。

`probe` 记下试的是哪条命令、退出码与输出尾部。退出码非 0 只有两种情况：本轮有 `install_failed`，或虚拟机与部署环节本身失败；`probe_failed` 不改变退出码，它是要读的结论，不是要失败的错误。

## Result

输出目录里的 `tool-install-report.json` 逐个工具记下 `payloads`、`install`、`probe`、`verdict` 与 `error`，外加本轮的 `specs` 与 `finished_at`。
