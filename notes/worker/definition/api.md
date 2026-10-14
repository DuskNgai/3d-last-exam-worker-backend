# Worker API

Master 访问 Worker 的接口。本文所列请求均由 Master 发起。

## Configuration

本文用到的设备标识、设备令牌、TLS 证书、TLS 私钥、监听地址、监听端口见 [configuration.md](configuration.md)。

## Transport

接口走 HTTPS。Worker 出示安装时生成的 TLS 自签证书，对应的 TLS 私钥在安装时一并生成，只存本机；证书指纹由人交给 Master 登记。

TODO：未来转向 CA 签发。

## Authentication

Master 的每个请求都带 `Authorization: Bearer <设备令牌>`，设备令牌不符应答 401。

## Endpoints

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/status` | 查询 Worker 进程状态 |
| POST | `/evaluate` | 派发一条评测的首次尝试 |
| POST | `/reattempt` | 使用本机材料重试一次评测 |
| GET | `/evaluation` | 查询一次评测尝试 |
| POST | `/pause` | 暂停一次评测尝试 |
| POST | `/resume` | 恢复一次评测尝试 |
| POST | `/cancel` | 取消一次评测尝试 |
| GET | `/report` | 取回一次评测尝试的报告 |
| GET | `/output` | 取回一次评测尝试的输出 |
| POST | `/archive` | 存档一次评测尝试 |
| POST | `/delete` | 删除一次评测尝试 |

### `GET /status`

应答：

- 200：返回当前进程状态。

应答字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | 字符串 | 进程状态，取值见 [status.md](status.md#worker-process-status) |
| `device_id` | 字符串 | 设备标识 |
| `evaluation_ids` | 字符串数组 | 本机有未完态尝试的评测号，去重后给出，可为空 |

### `POST /evaluate`


| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | 首次尝试号固定为 1 |


应答：

- 202：首次收下该组合，已排队；字段同 `GET /evaluation`。
- 200：相同组合已经登记，返回该尝试号对应的状态，不重复排队或执行；字段同 `GET /evaluation`。
- 409 `invalid_state`：本机同一评测号的另一次尝试仍在未完态。
- 422 `missing_material`：所需的 harness 或工具版本本机没有。
- 422 `insufficient_resources`：请求的资源要求超出本机能力。
- 503 `not_ready`：进程状态为 `starting`、`stopping`，或 Master 心跳超时，不收派发。

### `POST /reattempt`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `last_attempt` | 正整数 | 本机已失败的来源尝试号 |
| `attempt` | 正整数 | Master 分配的新尝试号，必须大于 `last_attempt` |

应答：

- 202：首次收下新组合，已排队；字段同 `GET /evaluation`。
- 200：组合已经登记，返回当前状态，不重复执行；字段同 `GET /evaluation`。
- 409 `invalid_state`：来源尝试的状态不为 `failed`，或者本机同一评测号的另一次尝试仍在未完态。
- 409 `reattempt_material_unavailable`：本机共享输入缺失或校验不通过。
- 503 `not_ready`：进程状态为 `starting`、`stopping`，或 Master 心跳超时，不收重试。

### `GET /evaluation`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 200：返回该次尝试的当前状态。

应答字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `status` | 字符串 | 评测状态，取值见 [status.md](status.md#evaluation-status) |
| `current_step` | 字符串 | TODO |
| `reason` | 字符串 | 暂停、失败或取消的原因，其余情况为空 |

TODO：`current_step` 是 Worker 给的当前步骤，取评测记录里最新的一条，自由文本，没有固定取值。评测记录的条目结构未定：步骤名的取值集合、每条记哪些字段（名称、时刻、还附带什么）都没有定义。

### `POST /pause`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 202：表示收到。
- 409 `invalid_state`：尝试已结束或取消。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `POST /resume`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 202：表示收到。
- 409 `invalid_state`：尝试未暂停。
- 503 `not_ready`：Master 心跳超时，暂不恢复尝试。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `POST /cancel`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 202：表示收到。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `GET /report`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 200：仅 `succeeded` 的尝试可取。
- 404 `not_found`：尝试不存在、已归档，或者成功尝试的报告文件缺失。
- 409 `invalid_state`：尝试尚未结束，或者结束状态不为 `succeeded`。

应答字段：

TODO：应答字段未定，待 Master 一侧派发的权威定义给出后补充。暂定是核验写出的 `score.json` 与 `report.md`。

### `GET /output`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 200：`succeeded`、`failed`、`cancelled` 的尝试可取，应答体是 gzip 压缩的 tar 包（`Content-Type: application/gzip`）。
- 404 `not_found`：尝试不存在、已归档，或者已结束尝试的输出文件缺失。
- 409 `invalid_state`：尝试尚未结束。

TODO：应答体未定，待 Master 一侧派发的权威定义给出后补充。

### `POST /archive`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 202：表示收到。
- 409 `invalid_state`：尝试尚未结束或已取消。

引起的状态转换见 [status.md](status.md#evaluation-status)。

### `POST /delete`

请求字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `evaluation_id` | 字符串 | 评测号 |
| `attempt` | 正整数 | Master 分配的尝试号 |

应答：

- 202：表示收到。
- 409 `invalid_state`：尝试未取消。

引起的状态转换见 [status.md](status.md#evaluation-status)。

## Errors

应答字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `code` | 字符串 | 机器可读错误码，取值见 [status.md](status.md#error-code) |
| `error` | 字符串 | 说明，写清具体是哪一项 |
| `reason` | 字符串 | 补充错误原因 |
