# QEMU Sandbox Setup

QEMU/KVM 沙盒的安装与配置手册。

## Prerequisites

1. 宿主机装工具链。

```sh
sudo apt install -y qemu-system-x86 qemu-utils ovmf genisoimage python3-pil
```

2. 确认 KVM 可用。

```sh
ls -l /dev/kvm   # 存在且当前用户可读写
```

如果 KVM 不可用，分三种情况：

- 普通主机有 sudo：加载模块 `sudo modprobe kvm_intel`（AMD 为 `kvm_amd`，写入 `/etc/modules-load.d/` 持久化）、BIOS 里开 VT-x/SVM、把当前用户加入 kvm 组获得 `/dev/kvm` 读写；
- 普通主机无 sudo：只能放弃 KVM；
- 容器里 `/dev/kvm` 被设备过滤器拦截（容器内 mknod 无效），需管理员重建容器加 `--device=/dev/kvm`。

无论哪种，拿到 KVM 前启动时一律加 `--accel tcg`（构建、手动起 VM、agent_run 都接受这个参数），功能等价、速度降级。构建在 TCG 下是小时级，更实际的做法是在有 KVM 的主机上构建，再把冻结的 base 目录整个复制过来。

GPU 负载另走 VFIO 直通（依赖 KVM 与 IOMMU），属管理员一次性配置。

3. 下载 ISO 到 `artifacts/isos/`；同页的 SHA256SUMS 里有官方哈希，传给构建命令的 `--iso-sha256` 即可核对。

## Build the Base Image

以 Ubuntu 24.04.5 Desktop 为例：

```sh
python3 src/sandbox/cli/build_base_image.py \
  --iso artifacts/isos/ubuntu-24.04.5.1-desktop-amd64.iso \
  --iso-sha256 4da4a0c9035da8e68a59a838674f403f0a54472c78a83b4fb7f78d03588f85a7 \
  --out artifacts/images/ubuntu-24.04.5-desktop-base
```

- `--iso` 是输入镜像：Ubuntu 官方安装镜像，通用、只读，构建完成后即可删除。
- `--iso-sha256` 是官方哈希：传入则构建先核对、不匹配直接失败；不传则只在 manifest 记录计算值，不做来源校验。
- `--out` 是输出目录：装好的系统盘 `base.qcow2` 写入这里，已预置 sandboxadm 管理员账号、uid 1001 的 agent 账号、qemu-guest-agent 自启与桌面自动登录；构建完成后冻结为只读（444），之后每次任务以它为 backing file 叠 overlay 启动 VM，运行写入全落在 overlay 上，base 永不改动，所以一份 base 可无限复用。同目录另有 manifest.json（哈希与 guest 信息）与 seed/ 构建日志。

可用 `--smp`、`--memory`、`--disk` 调整构建资源。构建中断后恢复：加 `--skip-install` 跳过安装直接进验证。全程约 12 分钟。
