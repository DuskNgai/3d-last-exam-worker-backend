# VM Deployment

2026-09-27：本文已被 QEMU/KVM 沙盒方案取代，部署入口由 `src/sandbox/deploy.py`（QGA 版）与 `src/sandbox/agent_run.py` 接管。以下内容保留为 VMware 原型的历史记录与验证证据，不再作为实施依据。

## Entry Point

统一入口为 `src/vm_sandbox/deploy.py`。从仓库根目录执行：

```sh
python src/vm_sandbox/deploy.py \
  --config local/deploy.json
```

添加 `--check-only` 只检查配置、本地包 SHA-256 和归档成员，不连接 VM。

## Prerequisites

目标为已启动的独立 Ubuntu 24.04 VM，安装 VMware Tools，并有可通过 Guest 操作接口登录的管理员账户。脚本不启动或克隆 VM，不改动网卡，也不触发模型调用。当前配置指向测试克隆，不能改成通用基础 VM 后直接执行。

脚本读取现成的本地部署包，不访问软件源。Python 包包含 Miniconda 安装器及固定环境的原始 Conda 包；MuJoCo 包包含完整 wheels 和检查代码；Codex 包为官方 0.156.1 Linux x86_64 package。首次安装在 Guest 内生成环境与启动路径，重复运行检查 Python 版本并重新验证软件功能。

## Configuration

配置采用 JSON，相对路径以配置文件所在目录为基准：

| 字段 | 内容 |
| --- | --- |
| vmx | 目标克隆的 VMX 路径 |
| credentials | 私有 Guest 凭据 JSON 路径，权限为 600 |
| reports | 宿主机报告目录，每次运行生成独立子目录 |
| timeout_seconds | 每次 Guest 操作的超时秒数 |
| check_viewer | 是否检查桌面 viewer |
| packages | codex、python、mujoco 三个包，各填写 path 与 sha256 |

凭据文件包含 `username`、`password`，只用于 Guest 管理账户，不含模型 API key。安装脚本临时需要 sudo，凭据只写入私有临时文件；结束时删除 Guest 中的认证脚本。VMware 的 `-gp` 仍通过进程参数传递 Guest 密码，本机其他有相应权限的进程可能读取；当前原型不能称为无明文凭据通道。

## Execution

流程为：校验本地包，传入 Guest 独立暂存目录，安装 Codex、Python、MuJoCo，以普通 agent 用户验证版本、自由落体与离屏渲染，取回报告，清理临时认证脚本。安装文件位于 `/opt/benchmark/` 和 `/task/software/`，不会复制宿主机用户配置或模型认证。

桌面 viewer 检查要求测试克隆已有 agent 的 X11 登录会话。首版使用 `DISPLAY=:0` 和 UID 1001 对应的 GDM Xauthority；这是当前镜像约定，不能直接用于任意用户布局。无桌面实例将 `check_viewer` 设为 `false`；其报告不能据此声称 GUI 验证通过。

报告包括 `deployment.json`、`install.log`、`software-check.json`、`render-check.json`、`render-check.png`；启用 GUI 检查时还包括 `viewer-check.json`。CLI 检查只确认版本和非交互入口，不代表已验证认证、模型推理或工具执行循环。

## Failure Handling

失败时返回非零退出码，尽量取回安装日志并删除临时认证脚本。暂存目录与失败日志保留以便排查。若 Guest 操作超时，Guest 内进程可能仍在运行；先查看 VM 状态，不要立即重复启动安装。若连清理也失败，终端会报告需要检查的 Guest 暂存目录。

脚本只部署到配置指定的 VM，不关闭 VM，也不自动删除安装环境。完成检查后，由宿主机单独关闭测试克隆。基础镜像、快照和现有 Windows VM 不属于部署脚本操作范围。

## Validation

2026-09-25，统一入口在无网卡 Ubuntu 测试克隆中完成重复部署。Codex CLI 0.156.1、Python 3.11.16、MuJoCo 3.14.0 验证通过；自由落体运行 0.1 秒后高度为 0.9504595 米，EGL 生成 320 x 240 图像，桌面 viewer 完成 100 次刷新并正常关闭。

成功报告保存在 `artifacts/runs/deployments/3bb74094118a/`。首次独立安装已分别验证；统一入口本次验证的是重复部署。模型认证与推理尚未测试。
