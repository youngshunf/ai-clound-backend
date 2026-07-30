# 方案 B 生产部署与回滚 Runbook

> 适用范围：RabbitMQ 4.3、Celery、Redis 8、Socket.IO、HASN realtime、offline recovery、
> OpenTelemetry、Prometheus 和 Grafana。所有阶段独立验证、独立回滚，禁止合并成一次大切换。

## 1. 不变量与操作边界

- RabbitMQ 是同机单节点故障域，不提供整机高可用。
- RabbitMQ 5672、15672、15692、4369、25672 和 Redis 端口只能监听 loopback。
- 密码只从服务器权限为 `600` 的密钥文件注入；命令输出、文档、Git、截图不得包含密码、
  完整 DSN、消息正文或用户/节点/消息 ID。
- 只重启本阶段涉及的精确服务，禁止 `supervisorctl restart all`。
- Redis 6 仍被非唤星应用共享时，禁止停止、替换或清空 Redis 6。
- RabbitMQ 数据目录禁止 `docker compose down -v`、递归删除或手工清空。
- 任何阶段验证不通过，立即回到该阶段列出的回滚点，保留日志、queue、trace 和指标现场。

生产路径与服务名：

| 对象 | 值 |
|------|----|
| 后端目录 | `/www/wwwroot/api.huanxing.dcfuture.cn` |
| RabbitMQ | `huanxing-rabbitmq` |
| Redis 8 | `huanxing-redis8`，宿主 loopback 端口 `9397` |
| 旧共享 Redis 6 | 宿主 loopback 端口 `9396` |
| API | `api.huanxing.dcfuture.cn` |
| Celery worker | `huanxing-backend-worker` |
| Celery beat | `huanxing-backend-beat` |
| Flower | `huanxing-backend-flower` |
| IM consumer | `huanxing-hasn-im-consumer` |
| sync worker | `huanxing-hasn-sync-worker` |
| 可观测栈 | `/data2/huanxing-observability/config/production` |

## 2. 每阶段统一前置检查

在主 clone 确认目标提交已推送，再登录生产机。下列命令均为只读：

```bash
cd /www/wwwroot/api.huanxing.dcfuture.cn
git branch --show-current
git rev-parse HEAD
git status --short

supervisorctl status \
  api.huanxing.dcfuture.cn \
  huanxing-backend-worker \
  huanxing-backend-beat \
  huanxing-backend-flower \
  huanxing-hasn-im-consumer \
  huanxing-hasn-sync-worker

docker inspect huanxing-rabbitmq --format '{{.State.Health.Status}} {{.RestartCount}}'
docker exec huanxing-rabbitmq rabbitmq-diagnostics check_running
docker exec huanxing-rabbitmq rabbitmq-diagnostics check_local_alarms
docker exec huanxing-rabbitmq rabbitmqctl list_queues \
  -p huanxing name durable messages_ready messages_unacknowledged consumers
```

部署前记录：

1. 目标 Git commit、当前生产 commit 和配置差异。
2. 六个 Supervisor 服务的启动时间、PID 和状态。
3. RabbitMQ 版本、容器镜像 digest、restart count、alarm、queue 和 consumer。
4. Redis 6/8 版本、端口、持久化状态和关键前缀数量。
5. Prometheus target、Grafana dashboard UID 和告警接收器名称。

## 3. B2：Celery broker 切换

### 3.1 切换

配置使用 RabbitMQ Celery 专用账号：

```dotenv
CELERY_BROKER='rabbitmq'
CELERY_BROKER_MODE='inherit'
CELERY_RABBITMQ_HOST='127.0.0.1'
CELERY_RABBITMQ_PORT=5672
CELERY_RABBITMQ_VHOST='huanxing'
CELERY_RABBITMQ_USERNAME='huanxing_celery'
CELERY_RABBITMQ_PASSWORD='<从权限 600 的本机密钥注入>'
```

按顺序只重启 Celery 组件：

```bash
supervisorctl restart huanxing-backend-worker
supervisorctl restart huanxing-backend-beat
supervisorctl restart huanxing-backend-flower
```

验证真实任务、beat、Flower、publisher confirm、queue ready/unacked 和 worker 日志。稳定观察
24 小时期间任一组件重启，公共稳定窗口从最后一次启动时间重新计算。

### 3.2 回滚

把 `CELERY_BROKER` 改回 `redis`，保留 RabbitMQ 现场，仍按 worker → beat → Flower 精确重启。
回滚前确认 RabbitMQ ready/unacked，避免把已受理任务误判为未提交后重复派发。

## 4. B3：Redis 8 蓝绿切换

### 4.1 强制门禁

先证明旧 Redis 6 没有非唤星客户端或取得这些应用所有者的明确迁移授权。至少核对：

```bash
redis-cli -h 127.0.0.1 -p 9396 CLIENT LIST
redis-cli -h 127.0.0.1 -p 9396 INFO keyspace
redis-cli -h 127.0.0.1 -p 9397 INFO server
redis-cli -h 127.0.0.1 -p 9397 INFO persistence
```

发现未知 `CLIENT LIST`、未知 key 前缀或独立应用依赖时立即停止 B3；不得仅凭 key 数量推断所有权。

### 4.2 切换

完成真实双写/回放/一致性验证后，将唤星配置改为：

```dotenv
REDIS_HOST='127.0.0.1'
REDIS_PORT=9397
REDIS_PROTOCOL=3
REDIS_LIST_MOVE_MODE='lmove'
```

只重启依赖全局 Redis 连接的唤星服务：

```bash
supervisorctl restart api.huanxing.dcfuture.cn
supervisorctl restart huanxing-hasn-im-consumer
supervisorctl restart huanxing-hasn-sync-worker
```

Celery 已走 RabbitMQ，不因 B3 重启 Celery。验证 session、缓存、限流、presence、
pending/processing、锁、Pub/Sub、LMOVE、TTL 和重连。

### 4.3 回滚

恢复 `REDIS_PORT=9396`、`REDIS_PROTOCOL=2`、`REDIS_LIST_MOVE_MODE=lua`，精确重启上述三个服务。
Redis 8 保留现场，不删除 key。旧 Redis 6 只有在所有共享所有者完成迁移后才能另行退役。

## 5. B4/B5：Socket.IO 与 HASN realtime

### 5.1 Shadow

Shadow 只允许 Redis active、RabbitMQ observe-only：

```dotenv
SOCKETIO_MANAGER='redis'
HASN_REALTIME_BUS='redis'
HASN_REALTIME_SHADOW_RABBITMQ=true
```

验证四个 consumer、真实双 WebSocket、跨 worker 定向投递、广播、断线重连、schema error ACK
和 shadow 对账。Shadow 不得向客户端重复投递。

### 5.2 正式切换

```dotenv
SOCKETIO_MANAGER='rabbitmq'
HASN_REALTIME_BUS='rabbitmq'
HASN_REALTIME_SHADOW_RABBITMQ=false
REALTIME_RABBITMQ_HOST='127.0.0.1'
REALTIME_RABBITMQ_PORT=5672
REALTIME_RABBITMQ_VHOST='huanxing'
REALTIME_RABBITMQ_USERNAME='huanxing_realtime'
REALTIME_RABBITMQ_PASSWORD='<从权限 600 的本机密钥注入>'
```

只重启 API：

```bash
supervisorctl restart api.huanxing.dcfuture.cn
```

验证公开 API、Socket.IO、两个真实 WS 客户端、sync publisher、RabbitMQ 临时队列自动清理、
confirm、ACK、redelivery 和 pending/processing 恢复。

### 5.3 回滚

恢复 Socket.IO 与 realtime 的 Redis active，关闭 RabbitMQ shadow，再只重启 API。Redis
pending/processing 是持久投递事实，回滚不得清空。

## 6. B6：Offline recovery 切换

### 6.1 三态顺序

1. `HASN_OFFLINE_RECOVERY=redis`：旧事实源。
2. `HASN_OFFLINE_RECOVERY=dual`：客户端只读 Redis；PostgreSQL sync 仅 shadow 对账。
3. `HASN_OFFLINE_RECOVERY=sync`：客户端从 PostgreSQL sync 恢复；Redis offline 停写。

`dual` 禁止双重回放。每个 durable 写点必须先有 PostgreSQL sync event，Redis 只保留短期加速。

### 6.2 门禁

- 7 天以上离线、空库恢复、双设备并发、断点重连均通过真实 E2E。
- 7 天 shadow 中 `redis_only=0` 且不存在不可恢复 gap。
- SQLite 事务失败不推进 cursor。
- task.exec 重放由 inbox lease、运行中 work session 和终态 work session 三层幂等收敛。
- `SCAN hasn:offline:*` 的计数与抽样稳定消息 ID 已同 sync feed 对账并留证。

切到 `sync` 后只重启 API、IM consumer 和 sync worker。回滚到 `dual` 时从 PostgreSQL 重建
加速数据，禁止依赖已退役旧 key。

## 7. B7：OpenTelemetry、Prometheus 与 Grafana

### 7.1 配置资产

- Dashboard：父仓 `docs/生产部署/grafana监控/方案B-RabbitMQ消息基础设施.json`
- Prometheus 采集：父仓 `docs/生产部署/grafana监控/方案B-Prometheus采集配置.yml`
- 告警规则：父仓 `docs/生产部署/grafana监控/方案B-RabbitMQ告警规则.yml`

RabbitMQ 原生 endpoint 继续只监听 `127.0.0.1:15692`；systemd socket proxy 只在
可观测 Docker 私网监听 `172.24.0.1:15693` 并转发到原生 endpoint。生产 Prometheus
增加 `job_name: rabbitmq`，目标为该私网 proxy。修改前备份
`/data2/huanxing-observability/config/production`，使用 `promtool check config` 和
`promtool check rules` 验证后，只 reload/recreate Prometheus；RabbitMQ、Celery 和 API
不因此重启。Grafana file provider 加载 dashboard 后，以 UID
`huanxing-scheme-b-rabbitmq` 检查。

### 7.2 Trace 验证

独立 IM consumer 使用 `service.name=hasn_im_consumer_worker` 初始化 tracer。真实消息链路应为：

```text
hasn.im.integration_event.process
  → rabbitmq.publish
    → rabbitmq.process
      → websocket.send
```

RabbitMQ header 只传播 W3C `traceparent`/`tracestate`，不传播 baggage。Grafana Explore
按真实 `trace_id` 打开后检查：

- 四个 span 的 trace ID 相同，父子边界正确。
- 属性只有稳定 operation、destination、consumer、event type、result 和 error type。
- 不含密码、完整 DSN、异常文本、正文或 owner/node/message/event ID。

### 7.3 Memory alarm 演练

只在低流量维护窗口执行，先确认真实告警接收器已经发过测试通知。记录原配置为 `1GB`：

```bash
docker exec huanxing-rabbitmq rabbitmqctl set_vm_memory_high_watermark absolute 128MB
docker exec huanxing-rabbitmq rabbitmq-diagnostics check_local_alarms
```

确认 Prometheus 指标 `rabbitmq_alarms_memory_used_watermark == 1`、Grafana 告警 firing、
真实接收器收到通知后立即恢复：

```bash
docker exec huanxing-rabbitmq rabbitmqctl set_vm_memory_high_watermark absolute 1GB
docker exec huanxing-rabbitmq rabbitmq-diagnostics check_local_alarms
```

若 128MB 未触发，先读取当前 RabbitMQ 内存再选择略低于当前用量的正值；禁止制造 OOM。

### 7.4 Disk alarm 演练

读取 `/data2` 当前可用空间，临时把 `disk_free_limit` 设置为略高于当前可用值，禁止创建大文件：

```bash
df -h /data2
docker exec huanxing-rabbitmq rabbitmqctl set_disk_free_limit 999GB
docker exec huanxing-rabbitmq rabbitmq-diagnostics check_local_alarms
```

确认 `rabbitmq_alarms_free_disk_space_watermark == 1` 和真实通知后立即恢复：

```bash
docker exec huanxing-rabbitmq rabbitmqctl set_disk_free_limit 5GB
docker exec huanxing-rabbitmq rabbitmq-diagnostics check_local_alarms
```

`999GB` 只改变阈值，不写盘；如生产盘可用空间高于该值，应使用略高于实测可用空间的阈值。

## 8. 单点故障演练

| 故障 | 操作边界 | 预期 | 恢复 |
|------|----------|------|------|
| RabbitMQ | 只重启 `huanxing-rabbitmq` | Celery/realtime 显式失败或重连；无无限重试、无重复业务事实 | broker 健康后确认 queue/consumer/confirm |
| Redis 8 | 只重启 `huanxing-redis8` | API 显式 5xx/降级；durable PostgreSQL 事实不丢 | Redis 健康后验证 presence、pending 和锁 |
| PostgreSQL | 只停止/恢复已确认的 PostgreSQL 服务 | send 不 ACK，cursor 不推进，outbox 可重试 | 数据库恢复后按幂等键续跑 |
| API | 只重启 `api.huanxing.dcfuture.cn` | 公开健康短暂失败；Celery/IM worker 不被连带重启 | API health、WS 重连、跨 worker 投递恢复 |

PostgreSQL 服务可能被其他应用共享，执行停机演练前必须确认影响范围和维护窗口；没有所有者授权时
只允许使用隔离测试实例或网络故障代理，不得操作共享生产实例。

## 9. 最终验收

代码门槛：

```bash
uv run mypy backend/
uv run pytest backend/ --cov=backend --cov-report=term-missing
```

涉及 hasn-node 时：

```bash
cargo fmt --all -- --check
cargo check --workspace
cargo test --workspace
cargo clippy --workspace --all-targets --all-features -- -D warnings
python3 tests/p0_real_e2e.py
```

最终生产状态必须同时满足：

- 六个 Supervisor 服务健康，公开 API、Celery、IM consumer、Flower 均通过真实探针。
- RabbitMQ/Redis/PostgreSQL/Grafana/Prometheus/Tempo 健康。
- RabbitMQ queue ready/unacked 无异常积压，consumer 数符合切换阶段，alarm 为 0。
- Grafana 展示真实 publish、confirm、return、deliver、ack、redelivery、ready、unacked、
  consumer 和 alarm。
- 一条真实消息 trace 完整且无敏感字段。
- memory/disk alarm 演练通知与 resolved 通知均已由真实接收器收到。
- 最终配置无临时双写或 shadow：`HASN_REALTIME_SHADOW_RABBITMQ=false`，
  `HASN_OFFLINE_RECOVERY=sync`；旧 Redis offline key 只在完成七天门禁后退役。

部署记录写入父仓 `docs/生产部署/部署记录/`，包含时间、commit、配置差异、精确重启对象、
验证输出摘要、trace ID（可脱敏）、告警通知证据和回滚点。
