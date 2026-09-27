# 3D Last Exam Evaluation

在此搭建 evaluation 框架。以 [Evaluation](notes/framework/evaluation.md)、[架构图](notes/framework/evaluation.html) 为设计依据，执行与核验分离。

## 沙盒环境

Agent 在独立 QEMU/KVM VM 中运行：代码在 `src/sandbox/`，`setup/` 与 `deploy/` 是核心库（base 镜像构建；VM 生命周期、Guest 控制、离线部署、模型网关、agent 编排），`cli/` 是对外命令行层（解析参数、调用核心），`qga.py` 是共用传输层。文档：`notes/environment/qemu-setup.md` 管安装配置，`notes/environment/qemu-vm-lifecycle.md` 管虚拟机生命周期，`notes/environment/agent-task.md` 管 agent 任务运行。VMware 原型文档保留在 `notes/environment/archive/` 作为历史记录。
