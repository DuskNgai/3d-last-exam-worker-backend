# Worker

Worker 是评测的执行者。评测分为两个阶段：运行与核验。本文档定义了 Worker 的部署与评测流程。

## Overview

推荐以下顺序阅读文档：

### Definition

1. [definition/configuration.md](definition/configuration.md)：Worker 的配置项定义。
2. [definition/status.md](definition/status.md)：进程与评测的状态取值、错误码。
3. [definition/api.md](definition/api.md)：Master 访问 Worker 各请求的字段与应答。
4. [definition/materials.md](definition/materials.md)：部署时下载、评测时使用的材料。

### Setup

2. [setup/identity-setup.md](setup/identity-setup.md)：Worker 身份的安装手册。
3. [setup/qemu-setup.md](setup/qemu-setup.md)：QEMU/KVM 沙盒的安装与配置。

### Main

1. [main/main.md](main/main.md)：Worker 主流程：启动、运行、核验、交付与关机。
