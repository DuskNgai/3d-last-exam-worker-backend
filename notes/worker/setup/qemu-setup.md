# QEMU Sandbox Setup

QEMU/KVM 沙盒的安装与配置手册。

## Prerequisites

1. 宿主机装工具链：

```sh
sudo apt install -y qemu-system-x86 qemu-utils genisoimage openssl
```

2. 确认 KVM 可用：

```sh
ls -l /dev/kvm   # 存在且当前用户可读写
```

如果 KVM 不可用，分三种情况：

- 普通主机有 sudo：加载模块 `sudo modprobe kvm_intel`（AMD 为 `kvm_amd`，写入 `/etc/modules-load.d/` 持久化）；BIOS 里开 VT-x/SVM；把当前用户加入 kvm 组获得 `/dev/kvm` 读写；
- 普通主机无 sudo：只能放弃 KVM；
- 容器里 `/dev/kvm` 被设备过滤器拦截（容器内 mknod 无效），需管理员重建容器加 `--device=/dev/kvm`。

KVM 不可用时，在构建命令上加 `--accel tcg`，功能等价，速度慢得多。

GPU 负载另走 VFIO 直通（依赖 KVM 与 IOMMU），属管理员一次性配置。

3. 下载 ISO 到材料目录的 `isos/`；[materials.md](../definition/materials.md) 有官方哈希，传给构建命令的 `--iso-sha256` 即可核对。

## Setup the Base Image

以 Ubuntu 24.04.5.1 Desktop 为例：

```sh
python src/setup/setup_base_image.py \
  --config src/configuration.yaml \
  --iso ubuntu-24.04.5.1-desktop-amd64.iso \
  --iso-sha256 4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7 \
  --out ubuntu-24.04.5.1-desktop-base
```

- `--iso`：配置的材料目录下 `isos/` 中的输入镜像文件名，只读。
- `--iso-sha256`：官方哈希，传入则构建先核对、不匹配直接失败；不传则只在 manifest 记录计算值，不做来源校验。
- `--out`：配置的材料目录下 `images/` 中的输出目录名，必须在本机文件系统上，网络文件系统不支持。
- `--accel`：`kvm`（默认）或 `tcg`。无 KVM 时按前文说明使用 `tcg`。
- `--smp`、`--memory`、`--disk`：构建资源，默认 4 vCPU、8192 MiB 内存、64G 磁盘。
- `--skip-install`：复用已有的 base.qcow2，跳过安装直接进检查。已冻结的镜像也能用：启动前重新开放写权限，检查完再冻回 444；镜像必须已写入完成标记，未装完的不能恢复。复用镜像做检查会真写盘，所以检查完冻结哈希会变、manifest 会重写。

这个命令具体做了如下事情：

1. 无人值守安装一块空盘，装完 guest 自行关机。
2. 立刻断网启动同一块盘做检查：完成标记、系统与账号、桌面自动登录、Node.js、libfuse2t64、每个 harness 的依赖，任一项不过即终止构建。
3. 通过后把系统盘冻结为只读（444）。此后每次任务以它为 backing file 创建 overlay，写入留在 overlay 里，base 镜像不动。

### Miscellaneous

镜像里两个账号：

- 特权账号 `sandboxadm`，密码是安装镜像的 SHA256。
- 评测用的 `agent` 桌面自动登录，无 sudo，密码锁定。

镜像里预装了 harness 的 npm 依赖闭包：`src/setup/base_image/harness-deps.json`。需要 npm 依赖的每个 harness 一节，构建时逐个装到 `/opt/harness-deps/<名字>/node_modules`，装 harness 时链接进 `/task/harness/node_modules`。需要更新依赖的话，修改这个文件并重建镜像。
