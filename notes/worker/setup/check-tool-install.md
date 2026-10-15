# Check Tool Install

工具包在冻结 base 镜像里逐个安装、并探一次 Python 接口的手册。用来确认每个工具归档在 guest 内装得上、装完还能用，与 agent 运行解耦。

## Scope

一次运行装完 tools 根目录下的全部 `<名字>/<版本>/`，安装顺序写死在探针表。装完再补一次 installer 彼此不做的检查：以 Python 接口问一遍每个工具，`bpy`、`FreeCAD`、`mujoco` 各归各的。虚拟机用 `-nic none` 起，没有网络。

## Run

```sh
python src/setup/check/tool_install.py \
    --config src/configuration.yaml \
    --run-dir <运行目录> \
    --output-dir <输出目录>
```

- `--run-dir`：虚拟机运行目录，放本次运行的 overlay、socket 与 vm.json，运行完即删。
- `--output-dir`：输出目录，收 tool-install-report.json。

常用参数：

- `--smp`、`--memory`：vCPU 数与内存 MiB，默认 4 与 8192。
- `--accel tcg`：宿主无 KVM 时加，功能等价，速度慢得多。
- `--vnc`：实时看虚拟机桌面，例如 `--vnc :5`。
- `--keep-vm`：保留运行目录供排查。

`--run-dir` 必须在本机盘上。网络文件系统建不了 unix socket，QEMU 的 QMP 与 QGA 会在 bind 时就失败。

## Flow

一台虚拟机，按表序循环每个工具：

1. 清掉 guest 里的 `/task/bundles/<名字>/`，把整个工具目录（`install.sh` 加版本目录）打成 tar 上传解包。这条路径与 agent 运行一致，包含哪些文件也一致。
2. 就地执行它自带的 `install.sh`，退出码非 0 时立即终止；installer 的 stdout 与 stderr 原样转发到本进程的 stdout 与 stderr。
3. 写一条记录。

全部装完后进入探针轮：对每个装成功的工具，按表里给的候选命令依次试，第一个退出码 0 的就是证据，探针结果回填进对应记录。候选以 agent 身份执行，整条命令套 `timeout`。

## Verdict

每个工具的接口见 `src/setup/check/tool_install.py` 的 `PROBES`。表里空元组表示该工具没有可问的 Python 接口：`sdf-view` 是 GUI 查看器，安装脚本里的 `--version` 已经证明二进制能运行。

`verdict` 三档：

- `probe_failed`：安装成功，但探针全部候选都没退出 0。记录里留的是最后一个候选的输出，例如缺共享库、`import bpy` 报错。
- `usable`：探针退出码 0。
- `installed`：没有声明的 Python 接口，安装即结论。

全部工具的 `verdict` 均为 `usable` 或 `installed`，这项自查才通过。

`probe` 记下试的是哪条命令、退出码与输出尾部。上传、解包、安装脚本、虚拟机或部署环节失败时，脚本立即终止，退出码非 0；已完成的记录写入报告。`probe_failed` 的结论写在报告里，退出码仍为 0。

## Result

输出目录里的 `tool-install-report.json` 逐个工具记下 `spec`、`tool`、`version`、`payloads`、`install`、`probe` 与 `verdict`，外加本轮的 `tools_root`、`base_image`、`run_dir`、`specs`、`started_at` 与 `finished_at`。安装轮中断时，已装工具的记录缺 `probe` 与 `verdict`。
