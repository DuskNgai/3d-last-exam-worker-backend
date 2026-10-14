# Configuration

Worker 所需的配置项，配置文件是 `src/configuration.yaml`。

## Setup

| 配置项 | 中文 | 类型 | 含义 |
| --- | --- | --- | --- |
| `worker_address` | 监听地址 | 字符串 | Worker 对外提供接口的绑定地址 |
| `worker_port` | 监听端口 | 整数 | Worker 对外提供接口的端口 |
| `device_id` | 设备标识 | 字符串 | Master 签发，对外表明 Worker 身份的名字 |
| `device_token` | 设备令牌 | 字符串 | Master 签发的认证凭据 |
| `tls_certificate_file` | TLS 证书 | 字符串 | 接口 TLS 加密用的自签证书的文件路径 |
| `tls_key_file` | TLS 私钥 | 字符串 | 自签证书对应的私钥文件路径 |
| `verification_environment` | 核验环境 | 字符串 | 核验用的 Python venv，只读 |

## Directory

| 配置项 | 中文 | 类型 | 含义 |
| --- | --- | --- | --- |
| `materials_directory` | 材料目录 | 字符串 | 材料放置的目录，内容见 [materials.md](materials.md) |
| `input_directory` | 输入目录 | 字符串 | 任务卡、任务输入与核验材料的存放目录 |
| `run_directory` | 运行目录 | 字符串 | 各条评测各次尝试的虚拟机临时文件存放目录；必须是本机磁盘 |
| `output_directory` | 输出目录 | 字符串 | 各条评测各次尝试固定后的运行输出与评测记录的存放目录 |
| `verification_directory` | 核验目录 | 字符串 | 各条评测各次尝试核验结果的存放目录 |
| `archive_directory` | 档案目录 | 字符串 | 评测的归档目录，档案内容见 [archive.md](archive.md) |

