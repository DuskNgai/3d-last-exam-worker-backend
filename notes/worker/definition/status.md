# Status

## Worker Process Status

Worker 进程的状态由 `GET /status` 给出：

| 状态 | 含义 |
| --- | --- |
| `starting` | 启动中 |
| `idle` | 空闲中：没有评测处于未完态 |
| `evaluating` | 评测中：有评测处于未完态 |
| `stopping` | 停止中 |

## Evaluation Status

每条评测的状态由 `GET /evaluation` 给出：

| 类别 | 状态 | 含义 |
| --- | --- | --- |
| 未完态 | `queued` | 排队中：等待运行 |
| 未完态 | `preparing_run` | 准备运行：正在部署运行环境 |
| 未完态 | `running` | 运行中：正在运行该条评测 |
| 未完态 | `preparing_verify` | 准备核验：正在回收运行环境、部署核验环境 |
| 未完态 | `verifying` | 核验中：正在核验该条评测 |
| 未完态 | `paused` | 暂停：已暂停该条评测 |
| 完成态 | `succeeded` | 完成：该条评测成功，核验结果可以取回 |
| 完成态 | `failed` | 失败：该条评测失败 |
| 取消态 | `cancelled` | 取消：已取消该条评测 |
| 存档态 | `archived` | 归档：运行目录与输出目录已删除，该条评测只留档案 |

### Control Transitions

暂停、恢复、取消、存档与删除的状态转换，按评测状态的类别区分：

| 类别 | 收到暂停 | 收到恢复 | 收到取消 | 收到存档 | 收到删除 |
| --- | --- | --- | --- | --- | --- |
| 未完态 | 转为 `paused` | `paused` 转回暂停前阶段；其余不变，拒绝 | 转为 `cancelled` | 不变，拒绝 | 不变，拒绝 |
| 完成态 | 不变，拒绝 | 不变，拒绝 | 转为 `cancelled` | 转为 `archived` | 不变，拒绝 |
| 取消态 | 不变，拒绝 | 不变，拒绝 | 保持 `cancelled` | 不变，拒绝 | 全部删除 |
| 存档态 | 不变，拒绝 | 不变，拒绝 | 转为 `cancelled` | 保持 `archived` | 不变，拒绝 |

## Error Code

错误码取值：

| HTTP 状态码 | 错误码 | 含义 |
| --- | --- | --- |
| 400 | `bad_request` | 请求字段缺失、类型错误或值不合格 |
| 401 | `unauthorized` | 设备令牌缺失或不符 |
| 404 | `not_found` | 评测号、输出或报告不存在 |
| 409 | `missing_material` | 该条评测要用的 harness 版本或任务软件版本本机没有 |
| 409 | `insufficient_resources` | 该条评测要求的资源本机满足不了 |
| 409 | `attempt_conflict` | 上一次 attempt 的目录还在 |
| 409 | `evaluation_not_finished` | 该条评测在未完态，输出与报告还不可取，也不能存档 |
| 409 | `evaluation_finished` | 该条评测不在未完态，不能暂停 |
| 409 | `evaluation_cancelled` | 该条评测已取消，不能存档 |
| 409 | `evaluation_not_succeeded` | 该条评测失败或已取消，报告不可取 |
| 409 | `evaluation_not_cancelled` | 该条评测不在取消态，不能删除 |
| 409 | `evaluation_not_paused` | 该条评测未暂停，不能恢复 |
| 413 | `too_large` | 请求体超过上限（1 MB） |
| 503 | `not_ready` | Worker 正在启动或停止，不收派发 |
