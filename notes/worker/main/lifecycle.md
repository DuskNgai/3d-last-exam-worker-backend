# Process Lifecycle

Worker 进程生命周期。

## Launch

启动 Worker：

```sh
conda activate 3dle-worker
python src/worker/main.py --config src/configuration.yaml
```

Worker 进程状态见 [status.md](../definition/status.md#worker-process-status)。

启动时按顺序做：

### 1. Configuration

- 读取并检查配置，装载 TLS 证书与私钥，绑定监听地址和端口。
- 提供接口，进程状态为 `starting`。

配置缺项或者值不合格、TLS 文件无法装载、监听地址与端口无法绑定时，进程不启动并报告原因。

### 2. Residual Cleanup

- 检查运行目录。目录不存在时创建；没有 `.worker-owned` 文件时，仅接受空目录并写入该文件；已有该文件时检查其内容。
- 扫描运行层启动的 `qemu-system-x86_64` 进程。命令行参数里带着运行目录下某个路径的，先按 SIGTERM 终止，不退出再按 SIGKILL 终止。
- 确认相关进程全部退出，删除这些临时运行目录。

运行目录不为空且没有 `.worker-owned` 文件、文件内容不符，或者无法读取 QEMU 进程信息时，启动检查失败，不删除目录。不能终止的进程、不能删除的目录，写清是哪一条，停止启动检查。

### 3. Attempt Recovery

- 读取输入目录中保存的尝试记录。未完态尝试记为 `failed`，`reason` 写明 Worker 重启中断了执行；其余状态保持原样。
- 尝试记录无法读取或内容不合格时，报告对应路径与原因，停止启动检查。

### 4. Material Inventory

- 扫描材料目录，把基础镜像、harness 与工具的清单记在内存里。

目录读不了、清单建不起来时，报告目录与原因，停止启动检查。

### 5. Ready

- 状态转为 `idle`，开始接受派发。

## Running

Worker 启动完成时初始化心跳时间。此后 Master 定期调用 `GET /status`，Worker 只在成功应答通过认证的状态查询后更新心跳时间。距最近心跳超过 5 分钟时，Worker 暂停本机尚未暂停的未完态尝试，在 `reason` 中写明 Master 心跳超时，停止安排执行，并拒绝新的派发；已经暂停的尝试保留原有的暂停原因和恢复阶段，已经结束的尝试保持原状态。

超时期间，Worker 继续提供状态查询，拒绝恢复尝试。新的状态查询恢复心跳后，Worker 重新接收派发；超时造成的暂停仍然保留，由 Master 对需要继续的尝试逐条调用 `POST /resume`。

## Shutdown

- 收到终止信号后，停止接收请求。
- 终止本机正在运行的虚拟机与执行进程，然后退出。

没有终止的虚拟机与运行目录留给下次启动清理。常驻进程异常退出时，虚拟机与运行目录可能残留，也由下次启动清理。
