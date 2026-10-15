# Setup

Setup 在 Worker 部署时完成。

## Worker Python Environment

在仓库根目录创建并启用 Worker 的 Conda 环境：

```sh
PYTHONNOUSERSITE=1 conda env create -f environment-worker.yml
conda activate 3dle-worker
```

该环境供 Worker 进程和部署命令使用。

## Configuration

配置文件及配置项见 [configuration.md](../definition/configuration.md)，确保必要的配置项填好。

## Identity

Master 签发的设备标识与设备令牌、本机生成的 TLS 自签证书与私钥。步骤见 [identity-setup.md](identity-setup.md)。

## Download

评测所需材料及对应的下载命令见 [materials.md](../definition/materials.md)。部署时可用统一脚本一次下载全部材料：

```sh
python src/setup/materials.py --config src/configuration.yaml
```

下载默认直连各官方源，网络受限时按渠道绕开：

- HTTP 下载走代理，在命令前带 `https_proxy` / `http_proxy` 环境变量；
- Python 包下载换 PyPI 镜像源，设 `PIP_INDEX_URL`；
- npm 包下载换镜像 registry，设 `npm_config_registry`。

## Base Image

以 `isos/` 里的安装镜像为输入制作基础镜像。命令与检查项见 [qemu-setup.md](qemu-setup.md)。

### Self-check

镜像制作完成后，在没有网络的虚拟机里逐个安装 harness 发行包并试入口，逐个安装工具并用各自的 Python 接口问一遍。手册见 [harness-install-test.md](harness-install-test.md) 与 [tool-install-test.md](tool-install-test.md)。

