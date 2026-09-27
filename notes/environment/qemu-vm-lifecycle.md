# QEMU VM Lifecycle

一次任务的虚拟机完整生命周期：启动、运行、取出输出、回收。

标准任务用 `agent_run` 一条命令跑完整个生命周期（建 VM、部署软件、跑 agent、取回输出、关 VM 并删 run 目录）：

```sh
python3 src/sandbox/cli/agent_run.py --task-dir local/tasks --run-id <id>
```

手动或调试时按下面的阶段逐步操作。

## Start

目的：从冻结的 base 镜像拉出一台属于本次运行的 VM，等到 Guest 里的 QGA 可应答。之后所有进出 Guest 的操作都走 QGA。

```sh
RUN=artifacts/runs/manual-01
python3 src/sandbox/cli/qemu_vm.py create $RUN        # 建 run 目录与 overlay，base.qcow2 全程只读
python3 src/sandbox/cli/qemu_vm.py start $RUN --network none   # 启动 QEMU 进程
python3 src/sandbox/cli/qemu_vm.py wait-ready $RUN    # 阻塞到 QGA 可应答
```

`--network` 三档：
- `none` 离线（默认）
- `nat` 出网
- `gateway` 离线加模型转发（agent_run 自动用 gateway，手工一般不用）。

无 KVM 的机器 start 加 `--accel tcg`。base 镜像与 runs 根目录单次覆盖用 `--base-image`、`--runs-root`。

## Work

目的：把软件和输入送进 Guest 并在里面执行。产出按约定写到 Guest 的 `/task/output`，宿主只从那里取回。

可选先离线部署软件。写配置 `local/deploy-config.json`（`run_dir` 必须是本节的 `$RUN`，填实际路径，JSON 里 `$VMSTORE` 不会被展开。照抄别的配置会把软件装进别的 VM）。

```json
{
  "run_dir": "/data/vmstore/runs/manual-01",
  "bundles": [
    {"name": "python", "path": "artifacts/tools/python", "script": "install_python.sh"},
    {"name": "mujoco", "path": "artifacts/tools/mujoco", "script": "install_mujoco.sh", "checks": true}
  ],
  "artifacts": ["/work/software-check.json", "/work/render-check.json", "/work/render-check.png"]
}
```

```sh
python3 src/sandbox/cli/deploy.py --config local/deploy-config.json   # 上传 bundle、Guest 内安装、跑检查
```

只复跑检查并取回报告加 `--check-only`。报告取回到 `<run_dir>/deploy-artifacts/<时间戳>/`。

然后传输入、解包、跑命令。

```sh
G="python3 src/sandbox/cli/guest.py --run-dir $RUN"
$G upload ./inputs.tar.gz /tmp/inputs.tar.gz          # 传进 Guest
$G run 'mkdir -p /work /task/output && tar xzf /tmp/inputs.tar.gz -C /work'
$G run 'cd /work && /task/software/python/bin/python myscript.py' --user agent  # 降权执行
$G screenshot desktop.png                           # 截桌面
$G processes                                        # 进程列表
```

## Get Output

目的：把 `/task/output` 里的产物取回宿主。

单文件直接取。

```sh
$G download /task/output/result.json ./result.json
```

整目录先在 Guest 内打包再取。

```sh
$G run 'tar czf /tmp/output.tar.gz -C /task output'
$G download /tmp/output.tar.gz ./output.tar.gz
```

## Reclaim

目的：关 VM、删 run 目录，把本次运行占的 CPU、内存、磁盘全部还掉，并确认 base 镜像没被碰过。以 Ubuntu 24.04.5 Desktop base 为例：

```sh
python3 src/sandbox/cli/qemu_vm.py stop $RUN      # ACPI -> SIGTERM -> SIGKILL 自动升级
python3 src/sandbox/cli/qemu_vm.py destroy $RUN   # 删 run 目录
```

确认回收干净。

```sh
test ! -d $RUN && echo run dir gone
pgrep -af qemu-system || echo no qemu process
sha256sum artifacts/images/ubuntu-24.04.5-desktop-base/base.qcow2   # 与同目录 manifest.json 里的 sha256 一致
```

## Failure Cleanup

目的：宿主进程被杀或终端断开时 VM 不会自己消失，找出残留逐个回收。

```sh
pgrep -af qemu-system
ls artifacts/runs/
python3 src/sandbox/cli/qemu_vm.py destroy <残留 run 目录>
```

gateway 模式的 relay 进程由 QEMU 按连接 spawn，QEMU 退出即消失，不留监听端口。
