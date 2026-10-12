# Configuration

Worker 各阶段所需的配置项。

## Setup

| 配置项 | 中文 | 类型 | 含义 |
| --- | --- | --- | --- |
| `worker_address` | 监听地址 | 字符串 | Worker 对外提供接口的绑定地址 |
| `worker_port` | 监听端口 | 整数 | Worker 对外提供接口的端口 |
| `device_id` | 设备标识 | 字符串 | Master 签发，对外表明 Worker 身份的名字 |
| `device_token` | 设备令牌 | 字符串 | Master 签发的认证凭据 |
| `tls_certificate_file` | TLS 证书 | 字符串 | 接口 HTTPS 用的自签证书的文件路径 |
| `tls_key_file` | TLS 私钥 | 字符串 | 自签证书对应的私钥文件路径 |
| `materials_directory` | 材料目录 | 字符串 | 材料放置的目录，内容见 [materials.md](materials.md) |

## Run

| 配置项 | 中文 | 类型 | 含义 |
| --- | --- | --- | --- |

## Verification

| 配置项 | 中文 | 类型 | 含义 |
| --- | --- | --- | --- |
