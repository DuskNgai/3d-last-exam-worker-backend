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

KVM 不可用时，在启动命令上加 `--accel tcg`，功能等价、速度变慢。

GPU 负载另走 VFIO 直通（依赖 KVM 与 IOMMU），属管理员一次性配置。

3. 下载 ISO 到 `artifacts/isos/`；同页的 SHA256SUMS 里有官方哈希，传给构建命令的 `--iso-sha256` 即可核对。

## Build the Base Image

以 Ubuntu 24.04.5.1 Desktop 为例：

```sh
python3 src/sandbox/cli/build_base_image.py \
  --iso artifacts/isos/ubuntu-24.04.5.1-desktop-amd64.iso \
  --iso-sha256 4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7 \
  --out artifacts/images/ubuntu-24.04.5-desktop-base
```

- `--iso`：输入镜像，只读。
- `--iso-sha256`：官方哈希，传入则构建先核对、不匹配直接失败；不传则只在 manifest 记录计算值，不做来源校验。
- `--out`：输出目录，必须在本机文件系统上，网络文件系统不支持。
- `--accel`：`kvm`（默认）或 `tcg`；无 KVM 时用 tcg，功能等价、速度变慢。
- `--smp`、`--memory`、`--disk`：构建资源，默认 4 vCPU、8192 MiB 内存、64G 磁盘。
- `--skip-install`：中断恢复用，复用输出目录里已有的 base.qcow2，跳过安装直接进验证。

装好的系统盘 `base.qcow2` 会写入输出目录，已预置 admin 管理员账号、uid 1001 的 agent 账号、qemu-guest-agent 自启与桌面自动登录。admin 的登录密码就是该 ISO 的 sha256，不另存密码文件；agent 账号密码锁定，只走桌面自动登录。构建完成后冻结为只读（444）：之后每次任务以它为 backing file 叠 overlay 启动虚拟机，运行写入全落在 overlay 上，base 永不改动。同目录另有 manifest.json（哈希与 guest 信息）与 seed/ 构建日志。
