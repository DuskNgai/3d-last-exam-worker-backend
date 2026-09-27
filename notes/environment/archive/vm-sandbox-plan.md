# VM Sandbox Plan

## Status and Scope

2026-09-27：本文已被 QEMU/KVM 沙盒方案取代，后端由 VMware Workstation 切换为 QEMU/KVM。以下内容保留为 VMware 原型的历史记录与验证证据，不再作为当前方案的实施依据。

用户已授权开始搭建。Ubuntu 24.04.5 Desktop 已安装，open-vm-tools 与 desktop 组件已补装。无网卡情况下，已实测宿主机传入脚本、Guest 执行和输出取回；公网访问失败。Codex CLI 0.156.1 已在独立无网卡克隆中离线安装；普通 agent 用户的版本命令与 exec 帮助验证通过。Python 3.11.16 与 MuJoCo 3.14.0 已完成 Guest 内离线部署，自由落体、EGL 离屏渲染和桌面 viewer 均验证通过。统一部署入口见 [VM Deployment](vm-deployment.md)。模型连接和正式求解尚未开始。

目标是让新的 Agent 在独立 Linux VM 中读取任务、编写代码、运行 MuJoCo，并交付可取回的 output。历史解法、开发 archive 和宿主机仓库不进入 VM。

已选定 VMware Workstation 与 Ubuntu 24.04 LTS Desktop（x86_64）。2026-09-24 检查确认本机已安装 VMware Workstation 26.0.0，build 25388281；`vmrun -T ws list` 可执行，返回没有运行中的 VM。现有 VMware 清单只包含 Windows 10，现已验证新 Ubuntu VM 的启动与 Guest 控制能力。现有 Windows VM 不属于本项目，不修改或复用。

## Architecture

采用本机已有的 VMware Workstation，通过 `vmrun` 管理生命周期。Guest 使用 Ubuntu 24.04 LTS Desktop，保留图形桌面以支持 MuJoCo viewer，也提供命令行执行。安装介质的具体 point release 与 SHA-256 在构建时固定。

基础系统与 Tools 安装验证完成后，关闭基础 VM 并创建通用干净快照。每次任务从该快照创建独立的完整克隆，使用自己的 VMX 配置与 VMDK 磁盘；首版优先减少磁盘依赖，后续再考虑节省空间的 linked clone。基础 VM 不用于求解，不覆盖基础快照。基础 VM 不预装 Codex CLI 或任务专用 Python 环境；这些通过独立部署包安装到每次运行的克隆内。

模型推理由外部服务提供。需要区分两种 Agent 接入方式：

| 模式 | VM 内运行内容 | 通信方式 |
| --- | --- | --- |
| 工具执行模式 | shell、Python、MuJoCo，以及命令执行服务 | 外部 Agent 通过受控通道执行命令，VM 无网卡 |
| 完整 Agent 模式 | Agent 进程及其所有任务工具 | 通过受控模型网关访问模型 API |

按此前希望 Agent 位于 VM 内的设定，完整 Agent 模式是最终目标。先用工具执行模式验收基础设施，再接入实际 Agent。已选定 Codex CLI 作为首版 harness，安装在 VM 内。具体版本、安装方式、非交互启动命令、模型接口与认证方式需要在实施前依据官方文档确认并锁定；基础设施验收不等于已完成 Agent 接入。

## Related Work

借鉴 OSWorld 的基础快照加逐任务初始化方式，以及 ALE 的任务、Agent、环境解耦方式。OSWorld 支持 VMware，并通过 VM 内 HTTP 服务执行动作；这说明 VMware 可用于自动评测，但不代表必须复制其网络配置。

首版先使用 VMware Guest 操作接口完成 shell 与文件传输，不自研串口协议。GUI 工具接入时再评估 OSWorld 的截图、键鼠服务。ALE 的 `load()`、`start()`、`evaluate()` 可作为后续生命周期接口参考；当前阶段只实现环境与任务准备，评分器仍留待后续。

## Guest Layout

VM 内任务根目录为 `/task`：

```text
/task/
  instruction.md
  input/
  software/
    python/
    mujoco/
    runtime/
  output/
/work/
```

instruction 与 input 从当前公开审核包导入，保留用户直接编辑的内容。runtime 仅包含公开执行代码与 physics.json，不包含预制场景或碰撞网格。导入前记录文件清单和 SHA-256。

Python 在 VM 内重新安装到固定路径，不能直接复制宿主机 Conda 目录。MuJoCo 安装在这个 Python 环境中，`software/mujoco` 指向相应安装位置。软件环境与任务资产分开制作，但从 Agent 看仍保留现有目录约定。

| 路径 | Agent 权限 | 内容 |
| --- | --- | --- |
| /task/instruction.md | 只读 | 当前任务说明 |
| /task/input/ | 只读 | 五个完整 OBJ |
| /task/software/ | 只读 | Python、MuJoCo、公开 runtime |
| /task/output/ | 可写 | 最终提交 |
| /work/ | 可写 | 脚本、日志、临时文件及额外本地环境 |

Agent 使用无 sudo 的普通用户。允许在可写目录写代码或创建环境；离线阶段的新依赖需从预置包缓存安装。系统包和任务目录权限由初始化服务配置，不赋予 Agent 管理权限。

## Base Image

1. 获取官方 Ubuntu 24.04 LTS Desktop ISO，验证官方校验清单；记录下载地址、镜像版本与 SHA-256。
2. 创建独立构建 VM，安装系统、`open-vm-tools`、`open-vm-tools-desktop` 及通用图形系统库。系统安装可通过控制台完成，配置保存为可重复运行的脚本。仅构建阶段使用 NAT 联网。
3. 验证桌面、Tools、Guest 命令执行和文件复制。基础系统用户与任务 Agent 用户分开，后者无 sudo。
4. 清理临时文件与构建凭据，移除虚拟网卡，关闭 VM，创建通用干净快照并记录文件清单。

基础镜像只保存操作系统、Tools 和通用系统依赖。克隆时生成独立的 VMware UUID 与 MAC，并检查 Guest 机器标识是否需要重新生成。Guest 操作所需的认证信息由宿主机私有配置管理，不进入任务包、版本库或命令日志。

## Deployment Packages

采用“复制部署包，在 Guest 内安装”。不直接复制宿主机已安装的 Codex CLI、Conda 环境或用户配置目录。安装过程可能生成绝对路径、启动脚本和符号链接，也可能写入安装目录之外，因此需要在目标系统内运行安装步骤。

| 部署层 | 内容 | 安装目标 |
| --- | --- | --- |
| Harness | 固定版本 Codex CLI 分发包与必要依赖 | VM 内独立工具目录，启动入口加入 Agent 的 PATH |
| Software | Python 3.11.16 安装材料、MuJoCo 3.14.0 及锁定依赖 | /task/software/python/，保留现有软件目录约定 |
| Task | instruction、五个 OBJ、公开 runtime | /task/，output 初始为空 |

部署包在匹配 Ubuntu 24.04 x86_64 的准备环境中制作，并在干净克隆中测试。包内包含安装材料、完整依赖、安装脚本、版本与 SHA-256 清单；不包含认证、历史对话、求解产物或宿主机缓存。离线安装所需系统包必须包含完整依赖，或明确列为基础镜像依赖，不能在安装阶段临时访问软件源。

Codex CLI 的具体分发方式尚未验证。实施时先核对官方 Linux 安装方式，再选择官方分发包；若采用依赖 Node.js 的安装方式，则一并固定 Node.js 与全部离线依赖。不能假设只复制一个 CLI 目录就足够。Python 环境也在 Guest 内创建，不替换 Ubuntu 系统 Python。

每次任务的部署顺序：

1. 从通用干净快照创建克隆，启动并等待 Tools 就绪。
2. 通过 Guest 文件复制接口传入部署包，校验清单。
3. 以初始化账户在 Guest 内执行安装脚本，设置安装路径、启动入口和目录权限。
4. 以普通 Agent 用户验证 CLI 可启动、Python 可导入、MuJoCo 可步进，并保存部署日志。
5. 导入任务包，确认 output 为空，检查只读目录权限。
6. 仅在开始模型连接测试或正式运行时配置本次认证；不复用宿主机现有登录目录。

安装验收阶段不调用模型、不求解拼图。模型连接测试单独验证，不把 CLI 版本命令成功当作 Agent 已可运行。模型选择、调用预算和认证来源在该阶段开始前确定。

首版每次在克隆中安装，以验证“通用空壳加部署包”确实可用。若耗时过长，之后可增加带固定软件的派生快照；派生快照必须在注入凭据与任务文件之前保存，并能追溯到基础镜像和部署包。

MuJoCo 采用 CPU 物理计算，先验证软件渲染；VMware 虚拟显卡的 3D 加速是否需要开启以实际测试为准，不做 GPU passthrough。离屏渲染与桌面 viewer 都需要在完成软件部署后验证。

## File Transfer and Control

首版禁用共享文件夹、拖放和共享剪贴板。宿主机通过 `vmrun` 的 Guest 文件复制接口传入任务文件，以普通 Agent 用户执行求解命令；受保护目录由独立的初始化步骤设置权限。Guest 操作依赖 VMware Tools 与 Guest 认证，不依赖 Guest 的 IP 网络。

每条命令记录工作目录、超时、标准输出、标准错误和退出码。`runProgramInGuest` 的控制调用成功不应直接当作任务命令成功；Guest 内包装脚本将真实退出码和日志写入文件，由宿主机取回。长任务采用异步启动、状态查询和超时终止；终止范围需包含该命令的子进程。

结果从 output 导出，宿主机只接收指定目录中的普通文件，对路径、文件数和总大小做限制，拒绝符号链接、设备文件和路径穿越。若使用归档传输，解包前先验证所有成员。不在宿主机内核中直接挂载 Agent 可写磁盘。

VMware Tools 控制接口与网络隔离分别验收。具体文件复制、进程状态和退出码采集能力需要在当前 Workstation 26.0.0 与 Ubuntu Guest 上实测。宿主机原始日志单独保存，不作为额外任务输入。

## Network Policy

### Build Network

构建 VM 使用 VMware NAT 下载系统包和 Python 包，不设置入站端口转发。NAT 默认允许出网，不属于隔离模式。构建完成后关闭 VM，移除虚拟网卡再封存离线基础状态。

### Offline Execution

基础设施验收和工具执行模式在关机状态下移除虚拟网卡，启动后通过 VMware Tools 执行命令与复制文件。检查 VM 内仅存在 loopback，并实际测试 DNS、公网地址和宿主机局域网地址不可达。

断网指没有 IP 网络连接；VMware Guest 操作接口仍是明确存在的宿主机控制通道。离线模式验收必须证明移除网卡后，该接口依然可用。

### Model Access

完整 Agent 模式在 Guest 内运行 Codex CLI，需要访问外部模型。计划添加专用 Host-only 网卡，连接宿主机模型网关；不添加 NAT 或桥接网卡。Host-only 本身允许访问宿主机及同网段 VM，因此还需宿主机防火墙限制：只允许该 VM 连接指定网关端口，拒绝其他宿主机服务、同网段实例以及经宿主机转发出网。

模型网关只接受约定的模型 API 请求，不接受任意 URL、HTTP CONNECT 或通用 TCP 转发，实际模型密钥保留在宿主机。Guest 仅持有本次运行的受限网关凭据。不能只设置 HTTP_PROXY 就称为受限网络。

需要先确认选定 Agent 支持自定义 API endpoint。网关与防火墙规则尚未实现；正式接入前分别验收模型调用成功、公网、其他宿主机服务及同网段其他 VM 访问失败。新增规则限定在本项目专用虚拟网络，不改变现有 Windows VM 的网络。

## Resources and Lifecycle

首版建议每个实例分配 4 vCPU、8 GiB 内存和 64 GiB 动态增长虚拟磁盘。这些是原型默认值，不是任务评分预算。宿主机还需要为克隆磁盘、快照、日志和输出设置容量上限，虚拟磁盘容量本身不能限制所有宿主机文件增长。

每个实例记录 VMX 绝对路径与实例标识，通过 VMware 配置限制 vCPU 与内存，VM 内限制 Agent 进程数。单条命令和整个实例分别设置超时。停止实例先调用正常关机，超时后仅对本次实例执行强制停止，记录中断原因；不批量结束 VMware 进程。

运行流程为：从干净快照克隆，配置网络与资源，启动 VM，等待 Tools 就绪，传入部署包并在 Guest 内安装，再传入任务并设置权限，执行命令，取回 output，关机，保存运行记录。只清理明确属于本次运行的克隆文件；基础镜像、审核包、原始资产和其他 VM 保持不变。

## Host Artifacts

实现代码位于 `src/vm_sandbox/`。新项目按用途分类保存大文件，均排除版本控制：

```text
artifacts/
  images/
    iso/                         Ubuntu 安装介质
    vmware/ubuntu-24.04-base/     基础 VM 与快照
  installers/
    codex/                       官方 Codex 分发包
    python/                      Python 离线部署包
    mujoco/                      MuJoCo wheels 部署包
  runs/
    codex-install/               已验证的软件测试克隆
    deployments/                 每次部署的独立报告
local/
  deploy.json                    本机部署配置
  guest-credentials.json         私有 Guest 凭据
```

软件包路径在 deploy.json 中相对于 local 解析，复制项目后可继续使用相对路径。历史运行日志保留原始路径，不作为当前执行配置。任务公开包位于 `tasks/picnic-cake/`，原始素材、参考解与历史求解代码不属于本项目。

配置与清单使用 JSON，事件流可使用 JSONL；VMware 文件保持原生格式。评测框架后续需补齐镜像版本、运行状态与提交记录，目前仅有部署原型及环境验证报告。

## Acceptance

以下标准用于审核沙盒基础设施，不放进给 Agent 的 instruction，也不用于给拼图解法打分。

| 检查项 | 通过条件 |
| --- | --- |
| 虚拟化 | VMware 实际启动 Ubuntu 24.04 Desktop，记录 Guest 内核、Workstation 与 Tools 版本 |
| 离线部署 | 从通用基础克隆出发，断网安装 Codex CLI 与任务软件，无需宿主机安装路径 |
| Harness | 普通 Agent 用户可运行固定版本 Codex CLI，无宿主机认证或历史对话 |
| 环境 | VM 内 Python 3.11.16、MuJoCo 3.14.0 可导入并执行物理步进 |
| 输入 | 五个 OBJ、instruction 和 runtime 与导入清单一致，无历史解法 |
| 权限 | Agent 无 sudo；不能修改 input、instruction 和受保护的软件 |
| 隔离 | 看不到宿主机仓库与 archive，无共享项目挂载 |
| 网络 | 离线模式无网卡，公网、DNS 与宿主机 IP 访问均失败 |
| 控制 | 可运行命令、收到退出码，命令超时后可停止，并继续执行下一条 |
| 渲染 | 生成有效离屏图像并取回，桌面 viewer 可打开、刷新和关闭；记录渲染后端 |
| 任务兼容 | 公开 runtime 能加载临时协议测试场景并执行短轨迹 |
| 结果 | 能取回 output 普通文件；越界路径与符号链接被拒绝 |
| 重置 | 新实例不含前一实例生成的标记文件，基础磁盘与快照链 hash 未变 |
| 生命周期 | 本次 VM 在正常与异常结束后均停止，不影响其他 VM |

协议测试使用独立临时目录中的合成几何，只检查运行兼容性，不构成有效拼图提交，不写进审核包或后续 Agent 输入。桌面与 viewer 纳入首版验收，以匹配现有 instruction。人工可以通过 VMware 控制台检查 GUI；让 Agent 获得截图与键鼠工具属于后续 Agent 接入，不能把桌面可见等同于工具已接通。

## Implementation Sequence

1. 审核本文，确认架构、目录与首版验收范围。
2. 检查 VMware 的 Linux Guest 启动能力与安装介质，创建独立 Ubuntu 24.04 Desktop VM。
3. 安装通用系统依赖与 Tools，验证 Guest 控制，封存通用干净快照。
4. 准备 Codex CLI 与任务软件部署包，实现 Guest 内离线安装，并验证路径、依赖、渲染与 viewer。
5. 实现快照克隆、vmrun 控制、任务传入和输出导出，执行离线验收并留存日志。
6. 接入 Guest 内 Codex CLI 及受控模型 API，验收完整 Agent 模式。任务定义审核完成后，再启动从零求解。
