# Main

Worker 常驻进程接受 Master 请求，组织运行、核验与交付。

## Launch

Worker 检查配置并建立 HTTPS 监听，清理残留资源，恢复已接收尝试的状态，初始化材料清单，然后接受派发。启动过程见 [lifecycle.md](lifecycle.md#launch)。

## Heartbeat

Master 定期查询 Worker 进程状态。Worker 根据查询时间判断 Master 是否仍在管理本机尝试；超时后暂停未完态尝试，Master 恢复查询后决定哪些尝试继续。具体流程见 [lifecycle.md](lifecycle.md#running)。

## Shutdown

Worker 收到终止信号后停止接收请求，终止正在运行的虚拟机与执行进程，再退出。退出过程见 [lifecycle.md](lifecycle.md#shutdown)。
