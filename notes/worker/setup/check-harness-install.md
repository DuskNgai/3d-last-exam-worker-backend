# Check Harness Install

harness 安装包在冻结 base 镜像里逐个安装的手册。用来确认每个 harness 归档在 guest 内能解包、能运行，与 agent 运行解耦。

## Scope

一次运行装完 harness 根目录下的全部 `<名字>/<版本>/`。每个只做安装：解包、统计文件数与顶层条目、试入口。虚拟机用 `-nic none` 起，没有网络。

## Run

```sh
python src/setup/check/harness_install.py \
    --config src/configuration.yaml \
    --run-dir <运行目录> \
    --output-dir <输出目录>
```

- `--run-dir`：虚拟机运行目录，放本次运行的 overlay、socket 与 vm.json，运行完即删。
- `--output-dir`：输出目录，收 harness-install-report.json。

常用参数：

- `--smp`、`--memory`：vCPU 数与内存 MiB，默认 4 与 8192。
- `--accel tcg`：宿主无 KVM 时加，功能等价，速度慢得多。
- `--vnc`：实时看虚拟机桌面，例如 `--vnc :5`。
- `--keep-vm`：保留运行目录供排查。

`--run-dir` 必须在本机盘上。网络文件系统建不了 unix socket，QEMU 的 QMP 与 QGA 会在 bind 时就失败。

## Flow

一台虚拟机，按序循环每个 harness：

1. 清掉上一轮的 `/task/harness` 与 `/task/bundles/<名字>/`。
2. 用与 agent 运行相同的 bundle 机制上传材料，在 `/task/bundles/<名字>/` 就地执行 harness 根目录携带的 `install.sh`。
3. 统计 `/task/harness` 的文件数与字节数，列出顶层条目。
4. 按 `ENTRY_POINTS` 找到该 harness 的入口，确认入口安装后存在。
5. 以 agent 身份试入口：先 `--version`，失败退 `--help`；整条命令套 `timeout`。
6. 写一条记录。

## Verdict

每个 harness 的入口见 `src/sandbox/deploy/harness.py` 的 `ENTRY_POINTS`：

- 值是从 `/task/harness` 起的相对路径。
- 裸载荷（单个二进制或 AppImage）记 `None`，意思是发行文件本身就是入口。

两处入口取自发行包的实际内容：`qwen-code` 的 npm `bin` 字段指向的 `scripts/cli-entry.js` 在发行包里不存在，实际入口是 `qwen-code/bin/qwen` 这个 launcher；`mistral-vibe` 的归档成员名带 `./` 前缀。

`verdict` 三档：

- `entry_missing`：入口在安装后缺失，要么路径写错，要么归档不完整。
- `entry_failed`：入口存在，但退出码非 0。原因在 `output` 里，例如 `env: node: No such file or directory`、node 的 ESM 解析栈、Electron 的沙箱报错。
- `runnable`：入口退出码 0。

全部 harness 的 `verdict` 均为 `runnable`，这项自查才通过。

`entry` 与 `probe` 记下试的是哪个文件、命令、退出码与输出尾部。上传、解包、安装脚本、虚拟机或部署环节失败时，脚本立即终止，退出码非 0；已完成的记录写入报告。`entry_missing` 与 `entry_failed` 的结论写在报告里，退出码仍为 0。

## Result

输出目录里的 `harness-install-report.json` 逐个已完成的 harness 记下 `spec`、`harness`、`version`、`payload`、`file_count`、`total_bytes`、`top_level`、`entry`、`entry_present`、`probe` 与 `verdict`，外加本轮的 `harness_root`、`base_image`、`run_dir`、`specs`、`started_at` 与 `finished_at`。
