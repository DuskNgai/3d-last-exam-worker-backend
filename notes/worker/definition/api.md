# Worker API

Master 访问 Worker 的接口。全部请求由 Master 发起，Worker 不主动联络 Master。

## Configuration

本文用到的设备标识、设备令牌、TLS 证书、TLS 私钥、监听地址、监听端口见 [configuration.md](configuration.md)。

## Transport

接口走 HTTPS。Worker 出示安装时生成的 TLS 自签证书，对应的 TLS 私钥在安装时一并生成，只存本机；证书指纹经其他途径交 Master 登记。

TODO：未来可能转向 CA 签发。

## Authentication

Master 的每个请求都带 `Authorization: Bearer <设备令牌>`，设备令牌不符应答 401。

## Endpoints

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/status` | 查询 Worker 进程状态 |
| POST | `/evaluate` | 派发一条评测 |
| GET | `/evaluation` | 查询一条评测 |
| POST | `/pause` | 暂停一条评测 |
| POST | `/resume` | 恢复一条评测 |
| POST | `/cancel` | 取消一条评测 |
| POST | `/archive` | 存档一条评测 |
| POST | `/delete` | 删除一条评测 |
| GET | `/report` | 取回一条评测的报告 |
| GET | `/output` | 取回一条评测的输出 |

### `GET /status`

应答字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | 字符串 | 进程状态，取值见 [status.md](status.md#worker-process-status) |
| `device_id` | 字符串 | 设备标识 |
| `evaluation_ids` | 字符串数组 | 处于未完态的评测号，可为空 |

### `POST /evaluate`

TODO：请求和应答字段未定，待 Master 一侧派发的权威定义给出后补充。`failed` 的评测号再次派发：attempt 加一，状态回到 `queued`。

应答：

- 409 `attempt_conflict`：上一次 attempt 的目录还在。
- 503 `not_ready`：进程状态为 `starting` 或 `stopping`，不收派发。

### `GET /evaluation`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | 字符串 | 评测状态，取值见 [status.md](status.md#evaluation-status) |
| `attempt` | 整数 | 该评测第几次尝试 |
| `current_step` | 字符串 | TODO |
| `reason` | 字符串 | 失败的原因或取消，其余情况为空 |

TODO：`current_step` 是 Worker 给的当前步骤，取评测记录里最新的一条，自由文本，没有固定取值。评测记录的条目结构未定——步骤名的取值集合、每条记哪些字段（名称、时刻、还附带什么）都没有定义；`current_step` 与档案里的各步骤时刻都取自它，需要先定结构。

### `POST /pause`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答：

- 202：表示收到。
- 409 `evaluation_finished`：不在未完态的评测。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `POST /resume`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答：

- 202：表示收到。
- 409 `evaluation_not_paused`：未暂停的评测。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `POST /cancel`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答：

- 202：表示收到。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `POST /archive`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答：

- 202：表示收到。
- 409 `evaluation_not_finished`：未完态的评测。
- 409 `evaluation_cancelled`：取消态的评测。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `POST /delete`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答：

- 202：表示收到。
- 409 `evaluation_not_cancelled`：不在取消态的评测。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `GET /report`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答：

- 200：仅 `succeeded` 的评测可取。
- 409 `evaluation_not_finished`：未完态的评测。
- 409 `evaluation_not_succeeded`：失败或已取消的评测。

应答字段：

TODO：应答字段未定，待 Master 一侧派发的权威定义给出后补充。暂定是核验写出的 `score.json` 与 `report.md`。

### `GET /output`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |

应答：

- 200：`succeeded`、`failed`、`cancelled` 的评测可取，应答体是 gzip 压缩的 tar 包（`Content-Type: application/gzip`）。
- 409 `evaluation_not_finished`：未完态的评测。

TODO：应答体未定，待 Master 一侧派发的权威定义给出后补充。

## Errors

应答字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `code` | 字符串 | 机器可读错误码，取值见 [status.md](status.md#error-code) |
| `error` | 字符串 | 说明，写清具体是哪一项 |
