# 3D Last Exam Evaluation

在此搭建 evaluation 框架。以 [Evaluation](notes/framework/evaluation.md)、[架构图](notes/framework/evaluation.html) 为设计依据，agent 只在沙盒虚拟机里执行任务，核验在宿主侧独立进行。

## Sandbox

Agent 在独立 QEMU/KVM 虚拟机中跑任务：

- 安装与配置：[QEMU Sandbox Setup](notes/environment/qemu-setup.md)
- 运行任务：[Agent Run](notes/environment/qemu-agent-run.md)
- 验证 harness 安装包：[Harness Install Test](notes/environment/harness-install-test.md)
- 验证工具包：[Tool Install Test](notes/environment/tool-install-test.md)
