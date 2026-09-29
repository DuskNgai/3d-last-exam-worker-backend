# Harness Install Test

harness 安装包在冻结 base 镜像里逐个安装的手册。用来确认每个 harness 归档在 Guest 内解得出、跑得起来，与 agent 运行解耦。

## Scope

一次跑完 harness 根目录下的全部 `<名字>/<版本>/`。每个只做安装与冒烟：解包、统计文件数与顶层条目、试入口。虚拟机用 `-nic none` 起，没有网络。

## Run

```sh
python3 scripts/check_harness_install.py \
    --base-image <base镜像路径> \
    --run-dir <运行目录> \
    --harness-root <harness目录> \
    --output-dir <输出目录>
```

- `--base-image`：冻结的 base qcow2。
- `--run-dir`：虚拟机运行目录，放本次运行的 overlay、socket 与 vm.json，跑完即删。
- `--harness-root`：harness 根目录，按 `<名字>/<版本>/<单一发行文件>` 组织，版本目录内可放 manifest.json 记录 sha256，根目录下放共用的 `install.sh`。
- `--output-dir`：输出目录，收 harness-install-report.json。
- `--harness`：可重复，只测指定的 `<名字>@<版本>`；不给就扫全目录。

常用参数：

- `--smp`、`--memory`：vCPU 数与内存 MiB，默认 4 与 8192。
- `--accel tcg`：宿主无 KVM 时加，功能等价，速度慢得多。
- `--vnc`：实时看虚拟机桌面，例如 `--vnc :5`。
- `--keep-vm`：保留运行目录供排查。

`--run-dir` 与 `--base-image` 都必须在本机盘上。网络文件系统建不了 unix socket，QEMU 的 QMP 与 QGA 会在 bind 时就失败。

## Flow

一台虚拟机，按序循环每个 harness：

1. 清掉上一轮的 `/task/harness` 与 `/task/bundles/<名字>/`。
2. 用与 agent 运行相同的 bundle 机制上传 material，就地执行材料根下的 `install.sh`。
3. 统计 `/task/harness` 的文件数与字节数，列出顶层条目。
4. 按 `ENTRY_POINTS` 找到该 harness 的入口，确认它装完还在。
5. 以 agent 身份试入口：先 `--version`，失败退 `--help`；整条命令套 `timeout`。
6. 写一条记录。

一台机器循环装完是刻意的取舍：只付一次启动开销。代价是前一个 harness 若在 `/task/harness` 之外留下痕迹，后一个就不是从绝对干净的状态开始；通用安装脚本只写 `/task/harness`，所以这个代价目前是零。

## Verdict

每个 harness 试哪个文件是写死的，见 `sandbox.deploy.harness.ENTRY_POINTS`：

- 值是从 `/task/harness` 起的相对路径。
- 裸载荷（单个二进制或 AppImage）记 `None`，意思是发行文件本身就是入口。
- 表里没有的名字，`check_harness_install.py` 在开虚拟机之前就报错，不去猜。

两处入口不是推断出来的，是查出来的：`qwen-code` 的 npm `bin` 字段指向的 `scripts/cli-entry.js` 在发行包里不存在，真入口是 `bin/qwen` 这个 launcher；`mistral-vibe` 的归档成员名带 `./` 前缀。

`verdict` 四档：

- `install_failed`：解包或安装脚本非 0 退出，记录里带错误原文。
- `entry_missing`：表里的入口装完不在，要么路径写错，要么归档不完整。
- `entry_failed`：入口在，但退出码非 0。原因在 `output` 里，例如 `env: node: No such file or directory`、node 的 ESM 解析栈、Electron 的沙箱报错。
- `runnable`：入口退出码 0。

`entry` 与 `probe` 记下试的是哪个文件、命令、退出码与输出尾部。退出码非 0 只有两种情况：本轮有 `install_failed`，或虚拟机与部署环节本身失败；`entry_missing` 与 `entry_failed` 不改变退出码，它们是要读的结论，不是要失败的错误。

## Result

输出目录里的 `harness-install-report.json` 逐个 harness 记下 `payload`、`file_count`、`total_bytes`、`top_level`、`entry`、`entry_present`、`probe`、`verdict` 与 `error`，外加本轮的 `specs` 与 `finished_at`。
