# Redis 8 与 RabbitMQ 消息基础设施方案 B 实施证据

> 状态：持续更新
>
> 首次采集：2026-07-29 23:55—2026-07-30 00:12 CST
>
> 后端基线：`huanxing-cloud-backend@d64337a26`
>
> 实施分支：`fix/rabbitmq-b-celery`
>
> 隔离 worktree：`.worktrees/rabbitmq-b-celery`

本文只记录可复核的命令、汇总指标和证据路径。禁止写入密码、完整 DSN、消息正文，
也不使用 owner、node、message 或 event ID 作为指标标签。

## 1. B0-01 迁移前基线

### 1.1 版本与运行模式

生产主机 `117.72.92.229` 于 2026-07-30 00:04 CST 的只读采集结果：

| 项目 | 基线 |
|---|---|
| API | Supervisor `RUNNING`，4 worker，监听 `0.0.0.0:8020` |
| Celery worker / beat / Flower | 均为 `RUNNING` |
| IM consumer / sync worker | 均为 `RUNNING` |
| Celery broker | `redis`，DB 1 |
| 应用 Redis | Redis 6.0.16，standalone，`127.0.0.1:9396`，DB 3 |
| RabbitMQ | 未安装，5672/15672/15692 无监听 |
| 传统 Socket.IO | 代码基线为 `AsyncRedisManager` / `RedisManager` |
| HASN realtime | Redis pending/processing LIST + `hasn:ws:deliver` Pub/Sub |
| 离线恢复 | Redis `hasn:offline:*` + PostgreSQL sync/history 兼容期路径 |
| 可观测栈 | Grafana 13.1.1、Alloy 1.18.0、Loki 3.7.4、Prometheus 3.13.1、Tempo 3.0.2，均为 `Up` |

目标版本已在施工前复核：

- RabbitMQ 官方 4.3 文档当前指向 4.3.4：
  <https://www.rabbitmq.com/docs>
- RabbitMQ 4.3.4 发布于 2026-07-23：
  <https://www.rabbitmq.com/release-information>
- Redis 8.8.0 是 Redis Open Source 8.8 GA：
  <https://github.com/redis/redis/releases/tag/8.8.0>
- Celery 5.6.3 配置文档明确支持通过 `confirm_publish` 开启 publisher confirm：
  <https://docs.celeryq.dev/en/stable/userguide/configuration.html>

### 1.2 Redis 与 Celery 汇总

采集命令通过生产应用的 `Settings` 建立真实连接，只输出汇总：

```text
redis_version=6.0.16
redis_mode=standalone
uptime_in_seconds=13050356
used_memory=7459192
used_memory_human=7.11M
used_memory_peak_human=96.57M
maxmemory=0
mem_fragmentation_ratio=1.25
instantaneous_ops_per_sec=14
total_commands_processed=304884479
keyspace_hits=23868316
keyspace_misses=2025713
evicted_keys=0
expired_keys=203068
rejected_connections=0
used_cpu_sys=20025.052402
used_cpu_user=19423.744668
app_db_size=222
celery_db_size=4
offline_key_count=1
ws_pending_key_count=0
ws_processing_key_count=0
celery_db_key_types={'set': 4}
celery_list_items=0
active={'celery@lavm-n0zinl4gex': 0}
reserved={'celery@lavm-n0zinl4gex': 0}
scheduled={'celery@lavm-n0zinl4gex': 0}
```

`inspect` 同时产生 `DuplicateNodenameWarning`。该告警已作为 B2 切换前必须消除或
解释的基线问题登记，不能把队列为空误写成 worker 控制面完全健康。

### 1.3 容量与健康

```text
主机 load average=0.93/1.11/1.03
根盘=/dev/vda1 99G，已用 58G，剩余 36G，使用率 62%
/data2=/dev/vdb 98G，已用 47G，剩余 47G，使用率 50%
本机 captcha=HTTP 200，time_total=0.039223s
```

现有应用 Prometheus 仅提供通用 HTTP `fba_request_cost_time` 直方图，没有
`/ws/node` 在线投递时延指标。因此下列 B0 证据仍待真实 4-worker 压测补齐：

- 定向投递与广播的 p50/p95/p99；
- 断线重连恢复时间、漏唤醒和用户可见重复；
- 受控负载下 4 个 API worker 的 CPU；
- 7 天 offline key 的恢复行为。

在这些数据补齐前，不勾选 B0-01 的性能验收项。

### 1.4 本地质量基线

隔离 worktree 使用主 clone 的 arm64 CPython 3.13.14，并按 `uv.lock` 安装
`server`、`dev` 依赖组。

```text
uv run mypy backend/
=> Success: no issues found in 2985 source files
```

首次 `uv sync` 自动选择了 x86_64 CPython 3.13.5，导致 Apple Silicon 上
`opendal` 原生扩展导入停在 Rosetta 翻译阶段。改为显式使用 arm64 解释器后，
同一扩展热导入耗时 0.02 秒。

修正后的环境使用真实本地 PostgreSQL `127.0.0.1:15432` 和 Redis
`127.0.0.1:6379` 完成了一轮全量基线：

```text
uv run pytest backend/ -q --tb=short
=> 5315 passed, 14 failed, 1 error, 27 skipped in 443.07s
```

失败均不在本次新增测试。复跑已确认其中至少两类是共享本地环境基线问题：

- growth 真实开通缺少可解密的知识库 service key；
- IM 用例在复用数据库中撞到前次残留的固定 nickname 唯一约束。

其余失败集中在既有 sync、资产和响应信封用例，后续须改用隔离的真实测试库逐项
复跑。不能把这轮结果写成全量门槛通过，也不能为消除失败清理未知归属的本地数据。

`python3 tests/p0_real_e2e.py` 首次运行因 daemon 在 90 秒等待窗内仍处于 Rust
冷编译而失败；失败点是本机 `/health` 尚未监听，不是断言失败。应先执行：

```bash
cargo build -p hasn-daemon --bin hasn-node
```

再用空闲的 `HASN_P0_E2E_DAEMON_PORT` 重跑并保留
`test-results/e2e/p0-real-e2e-evidence.json`。

## 2. B0-02 配置契约

2026-07-30 00:43 CST 在隔离 worktree 复核：

```text
uv run mypy backend/core/conf.py backend/common/messaging/
=> Success: no issues found in 3 source files

uv run pytest backend/tests/test_rabbitmq_settings.py \
  tests/deploy/test_rabbitmq_configuration.py -q --tb=short
=> 16 passed, 60 warnings in 0.12s
```

配置测试覆盖 Redis 默认行为、RabbitMQ 角色凭据启动守卫、shadow/active 冲突、
AMQP DSN URL 编码和无凭据端点描述。warnings 是仓库既有 SQLAlchemy/Pydantic
弃用告警，本阶段未新增同类告警。

## 3. B1-01 RabbitMQ 生产基础设施

### 3.1 部署边界

2026-07-30 00:29—00:44 CST 在生产主机建立新的单节点 RabbitMQ 4.3.4：

| 项目 | 结果 |
|---|---|
| 镜像 | 官方 `rabbitmq:4.3.4-management`，锁定多架构索引摘要 |
| 故障域 | 单节点，明确不宣称 HA |
| 容器资源 | 2 GiB 内存、1.5 CPU、512 PID；RabbitMQ 1 GiB 内存水位 |
| 数据路径 | `/data2/huanxing-rabbitmq/data` |
| 日志路径 | `/data2/huanxing-rabbitmq/logs` |
| 备份路径 | `/data2/huanxing-rabbitmq/backups`，definitions 备份权限 `600` |
| 业务状态 | 应用、Celery、Socket.IO、HASN realtime 仍全部走 Redis |

最终本地部署文件与生产副本 SHA-256 逐项一致：

```text
rabbitmq.conf      1b3a2bfdd1857a6e378559b89582428d8d90c7c1f2d98037a30957646c4fd0b8
definitions.json   314d8f6295306962ff436182afcfb29fec5a3a60da0abc0b0370eac8173bd1d8
enabled_plugins    a63f0dd4e9181c82af9ad191df89429da0b761517e4a05570a0c67de4c0c1c2d
docker-compose.yml b04026215afdc78a1c8ca62d83a967c24c98a007393987afc638309e37100aee
bootstrap.sh       32584c26a5f9640665312cb885bedd658118ea64c5ba7bce884ae0095b6dc810
```

### 3.2 安全与持久性

首次真实 bootstrap 显式暴露并修正了 `rabbitmqctl`/`rabbitmq-diagnostics` 命令归属；
首次重启检查又发现 Erlang 分发端口仍监听全接口。补测试后将 4369 和 25672
与三个应用端口一并收口。最终宿主机监听：

```text
127.0.0.1:4369
127.0.0.1:5672
127.0.0.1:15672
127.0.0.1:15692
127.0.0.1:25672
```

从独立公网侧探测五个端口均为 `TimeoutError`。容器强制重建后复核：

```text
check_running=通过
check_local_alarms=无本地告警
role_authentication=ok
management_http=200
prometheus_http=200
guest_count=0
queue_state=huanxing.celery.default ready=0 unacked=0 consumers=0
error_log_count=0
```

重启后 `huanxing` vhost、三个 durable exchange、Celery durable classic queue、
临时 realtime queue policy、三角色权限和 vhost 限额均保留。definitions 模板的
`users`、`permissions` 为空，不含密码、密码哈希、默认口令或完整 DSN。

## 4. B2-01 Celery RabbitMQ 可靠性与真实 E2E

### 4.1 固定配置

2026-07-30 01:22 CST 完成 Celery 5.6.3 精确锁版和 broker 配置测试。RabbitMQ
模式固定使用 durable classic queue `huanxing.celery.default`、direct exchange
`huanxing.celery`、persistent delivery、publisher confirm、60 秒 heartbeat、
prefetch 1、late ACK 和 worker-lost reject；Redis rollback 分支保留历史 `celery` 队列，
且不伪造 RabbitMQ confirm。生产预检发现同机其他项目也监听通用 `celery` 队列后，
该初始决定已修正：回滚队列改为项目独占的 `huanxing.celery.rollback`，避免跨项目误消费。

RabbitMQ 4.3.4 的真实 remote-control 首次运行暴露了兼容性缺口：
Celery/Kombu 默认把 pidbox 请求/回复 queue 和事件接收 queue 声明为非持久、
非独占队列，而 RabbitMQ 4.3 默认拒绝已弃用的 `transient_nonexcl_queues`。
没有打开 RabbitMQ 的弃用兼容开关，而是在应用配置中把 control/event queue
改为非持久的独占队列；它们本就绑定单个客户端连接，断线自动删除符合其生命周期。

配置、静态类型与变更相关回归验证：

```text
DATABASE_PORT=15432 ENVIRONMENT=dev uv run pytest -q <本阶段 12 组测试文件>
=> 53 passed, 12 skipped, 140 warnings in 2.36s

uv run mypy <本阶段 26 个 Python 实现与测试文件>
=> Success: no issues found in 26 source files

uv run mypy backend/
=> Success: no issues found in 2999 source files

git ls-files -m -o --exclude-standard -z | xargs -0 uv run prek run --files
=> 全部 hooks 通过
```

Docker Compose 另经本地 YAML 解析、测试约束和生产主机的真实
`docker compose config --quiet` 验证。期间发现并修正两项实际部署缺陷：

- Compose 直接导出 `CELERY_BROKER=rabbitmq` 会被 Celery CLI 当作 URL；
  改用 `CELERY_BROKER_MODE=rabbitmq`，Settings 在校验前归一到应用字段。
- 容器启动命令原先在后台 `supervisord` 后执行无目标 `supervisorctl restart`；
  改为 `exec supervisord -n`，确保 PID 1 生命周期正确。

### 4.2 真实 RabbitMQ 与 PostgreSQL E2E

测试通过 SSH 本地转发连接生产 RabbitMQ loopback 监听，凭据只进入进程环境；
每轮创建唯一 durable queue/exchange，启动独立 `celery worker --pool=solo` 子进程，
并在结束后删除测试拓扑。result backend 使用真实本地 PostgreSQL，不使用 eager、
mock、fake broker 或内存 backend。

本机是 macOS，Celery prefork 子进程会在 Python 导入期间触发 Objective-C fork
安全保护，因此本地故障语义 E2E 固定使用真实 solo worker；Linux 生产的 prefork
并发 4 启动、消费和重投另列入 B2-02 生产验收，不用本机兼容参数替代。

```text
CELERY_RABBITMQ_E2E=1 uv run pytest -q \
  backend/tests/tasks/test_celery_rabbitmq_e2e.py
=> 11 passed, 60 warnings in 77.31s
```

十一条链路分别验证：

- 真实生产任务 `credit_outbox_metrics_refresh` 由 RabbitMQ 消费，结果写入项目自定义
  PostgreSQL `DatabaseBackend`；
- 首次失败走 Celery `retry`，第二次成功并返回真实 `retries=1`；
- `countdown=2` 的实际执行延迟不低于 1.5 秒；
- `inspect.ping` 与 `inspect.registered` 经 pidbox 正常返回；
- `revoke` 可撤销仍在 countdown 窗口内的任务，任务不会产生业务效果；
- Flower 同形态的事件 Receiver 使用独占临时 queue 捕获 worker heartbeat；
- 真实 Beat 子进程使用项目 `DatabaseScheduler`，在随机临时 PostgreSQL 数据库写入
  调度行、发布探针、由 worker 消费，并在正常退出时回写 `total_run_count`；
- 实际 Flower 进程只监听回环地址，未认证请求返回 `401`，正确 Basic Auth 可读
  `/api/workers` 并发现目标 worker；
- publish 前关闭连接不会向队列写入任务；
- broker 已接收 publish、但 confirm 返回前关闭连接时，生产者得到歧义失败；用同一
  `task_id` 重投后出现两次 delivery，PostgreSQL 幂等表只产生一次业务效果；
- consumer ACK 前强制终止 worker 后，消息带 `redelivered=true` 由下一 worker
  重收，两次 delivery 仍只产生一次业务效果。

实际 `DatabaseScheduler` 首次运行暴露了生产缺陷：空调度表时
`self._schedule or {}` 返回临时字典，导致默认计划与 `beat_schedule` 写入后丢失，
Beat 只持锁而不发布。改为始终返回同一个可变映射后，真实用例通过。

故障 E2E 还暴露了 `BEFORE_CONFIRM` 与 `BEFORE_PUBLISH` 的转发线程退出竞态。代理现以
短 socket 轮询响应停止信号、双向 shutdown 并严格上抛 cleanup 错误；publish 前场景
连续 3 次通过后，再通过上述 11 项全套。
测试结束后生产 RabbitMQ 中名称含 `.e2e.` 的 queue/exchange、E2E 用户和连接均为
0，`huanxing_celery` 角色标签仍为 `[]`。

真实 Redis rollback 使用独立 DB 15、生产 Celery 应用和真实 PostgreSQL result backend，
消费生产 `credit_outbox_metrics_refresh` 任务，并验证 registered、active、reserved、
scheduled 与独占 rollback queue：

```text
CELERY_REDIS_E2E=1 CELERY_BROKER_MODE=redis CELERY_BROKER_REDIS_DATABASE=15 \
  DATABASE_PORT=15432 ENVIRONMENT=dev \
  uv run pytest backend/tests/tasks/test_celery_redis_rollback_e2e.py -q
=> 1 passed, 60 warnings in 5.46s
```

测试验证独占 `huanxing.celery.rollback` queue，仅在 DB 15 初始为空时运行，
结束后只删除本轮创建的键。

### 4.3 任务 ACK 与幂等审计

共核对 `backend/app/**/tasks.py` 和 `backend/app/tasks/push_message.py` 的 34 个
任务入口：

| 类别 | 处理结论 |
|---|---|
| 数据库状态更新、日志清理和确定性运算 | 使用条件更新、唯一键或确定性结果；可安全采用默认 late ACK |
| billing / relation / artifact outbox | 持久事件 ID、状态机、`SKIP LOCKED` 或 reconcile 保护权威业务写；WSPUSH 只是 best-effort 加速，客户端仍由 sync pull 追平 |
| Growth 开通 | 可靠步骤表和 reconcile 收敛，采用默认 late ACK |
| Growth 采集 | 显式 early ACK；`pending` 权威状态、行锁和周期恢复避免并发重复落库，但 worker 在爬虫/LLM 返回后、事务提交前退出仍可能重复外部调用，不宣称调用成本 exactly-once |
| Growth 渠道发送 | 显式 early ACK、`FOR UPDATE SKIP LOCKED` 串行认领；每个审批版本生成稳定 `idempotency_key`，注册通道必须声明 provider 去重保证 |
| Owner 记忆 / Peer 画像 | 显式 early ACK；按 owner 或 owner-peer 的 PostgreSQL 事务级 advisory lock 串行化，锁后重查 pending/脏状态，周期 sweep 恢复未提交结果；LLM 调用成本不宣称 exactly-once |
| 存储与技能 sweeper | 按数据库权威状态、内容指纹或游标 reconcile，重复轮次收敛到同一状态 |
| 旧 `push_message` | 已退役为无副作用兼容 no-op；实际移动推送由持久集成事件 consumer 按既有 best-effort 契约处理 |

RabbitMQ 默认任务使用 late ACK、prefetch 1 和 worker-lost reject；不可事务化的
LLM、爬虫、外部发送任务逐个显式覆盖为 early ACK。这里保证的是数据库权威业务状态
可恢复、可收敛，不把跨数据库与外部 provider 的崩溃窗口描述成 exactly-once。

B2-01 的配置、真实 RabbitMQ 协议 E2E、实际 `DatabaseScheduler` 和真实 Redis
rollback 均已完成。Linux prefork、生产任务、Redis 排空、服务切换与生产回滚实操
属于 B2-02。

### 4.4 全量后端回归基线

```text
DATABASE_PORT=15432 ENVIRONMENT=dev uv run pytest backend/ -q --tb=short
=> 5351 passed, 38 skipped, 6 failed, 1 error in 440.73s
```

施工前同一共享环境为 `5315 passed, 27 skipped, 14 failed, 1 error`。本轮剩余项均不在
本阶段变更文件：

- Growth 真实开通缺可解密知识库 service key；
- 两项 IM PG 用例撞到共享库固定 nickname 残留；
- Agent asset 用例复用全局 Redis client 时跨 pytest 事件循环；
- 知识分享 adapter 读到了与预期 ORM 类型不一致的存量结果；
- 股票下载 cleanup 触发既有 `STORAGE_TOMBSTONE_TARGET_MISMATCH`；
- 响应信封基线尚未纳入三个既有 bootstrap route。

以上失败均可单独复现；未删除共享数据库未知归属数据，也未修改非本任务文件掩盖失败。

### 4.5 B2-02 生产预检发现

2026-07-30 03:32 CST，生产代码已部署到 `eeee71884`，Celery 已锁定为 5.6.3，
API 在 Redis broker 下返回 200。切换前停 Beat 并核对 Worker PID/PPID 后确认没有
孤儿进程；`DuplicateNodenameWarning` 来自同机 `lottery-project` 的另一组 Celery
Worker。该 Worker 与本项目连接同一 Redis broker、使用相同默认节点名，并监听通用
`celery` queue。

因此在继续生产切换前增加两项硬修复：

- Redis rollback queue 改为 `huanxing.celery.rollback`；
- 本项目 Worker 使用唯一 `huanxing@%h` 节点名，Supervisor 增加
  `stopasgroup=true` / `killasgroup=true`。

真实 Redis rollback E2E 和部署配置测试已覆盖上述修复。Beat 保持停止，待修复部署、
Redis 最终排空和 RabbitMQ 切换完成后再恢复。

首次最终启动 Beat 时，RabbitMQ 立即出现一个执行中的 `task_demo`。追查确认上游示例
调度仍以 30 秒/每分钟频率进入生产队列，造成无业务价值的持续 unacked，且违反本项目
禁止 fake/echo 的实现门槛。现已从生产调度表移除三项 demo，并删除仅承载这些任务的
`backend/app/task/tasks/tasks.py`；测试同时约束调度表和自动发现包都不得再包含
`task_demo`、`task_demo_async`、`task_demo_params`。`DatabaseScheduler` 在首次加载
数据库调度前还会把三类存量启用行设为 `enabled=false`；真实 RabbitMQ + 临时
PostgreSQL Beat E2E 已验证存量行被禁用且正常周期任务仍能发布、回写运行次数。
Beat 在该修复部署前再次停止。

### 4.6 B2-02 生产切换与实际回滚

切换窗口为 2026-07-30 03:21–03:56 CST。部署源最终为 `origin/huanxing`
`2f3007bd5245a366d9e3b4d03cd2f761cf1cc168`，生产快照位于：

```text
/data2/backups/预部署-20260730-032808
/data2/backups/celery-rabbitmq-cutover-20260730-032301
/data2/backups/celery-rabbitmq-cutover-final-20260730-033723
```

依赖同步把生产 Celery 从 5.6.2 精确升级为 5.6.3。部署 runner 另发现并执行了主分支
已有的两项待执行迁移：

- `hasn_growth/migrations/2026-07-29-growth-review-v8.sql`
- `hasn_task/migrations/2026-07-29-workflow-template-source-release.sql`

四项标记为生产暂缓的危险迁移未执行。

干净切换按以下顺序实操：

1. 停止 Beat，再停止 API 阻断新生产者；
2. Redis 旧 `celery`、新 `huanxing.celery.rollback`、unacked 和 unacked index
   连续三次均为 0；
3. 优雅停止本项目 Worker 和 Flower，本项目 Celery 进程数归零；
4. 原子写入两份生产 env 与精确 Supervisor 配置，启动唯一节点
   `huanxing@lavm-n0zinl4gex` 的 RabbitMQ Worker；
5. Linux prefork 子进程数为 4，注册 35 个任务，真实
   `credit_outbox_metrics_refresh` 成功消费并写入 PostgreSQL `DatabaseBackend`；
6. 启动 API 并得到 200，此时 Beat 仍停止。

随后执行了真实回滚，而非只做文档推演：

1. 停 API，RabbitMQ active/reserved/scheduled 和 ready/unacked 连续三次为 0；
2. 停 Worker，把 broker 回拨到 Redis；新 Worker 的 transport 为 `redis`，
   默认 queue 为 `huanxing.celery.rollback`；
3. 启动 API 得到 200，真实生产任务成功，rollback queue、unacked 均回到 0，
   同机其他项目使用的通用 `celery` queue 增量为 0；
4. 再次停 API、排空 Redis、停 Worker，把 broker 切回 RabbitMQ；
5. RabbitMQ Worker、真实生产任务和 API 200 再次通过后才恢复 Beat，Flower 最后启动。

删除 demo 并禁用存量数据库行后的最终验收时间为 2026-07-30 03:56 CST：

```text
api / worker / beat / flower = RUNNING
local API / public API = 200 / 200
rabbitmq container = running/healthy
rabbitmq checks = check_running + check_local_alarms 通过
huanxing.celery.default = ready 0 / unacked 0 / consumers 1
worker = ping 1 / registered 32 / prefork 4 / active 0 / reserved 0 / scheduled 0
worker demo registered = 0
redis huanxing.celery.rollback = 0
遗留 demo scheduler = 3 rows / enabled 0
Flower = unauthenticated 401 / authenticated 200 / worker visible 1
E2E queue / exchange / connection = 0 / 0 / 0
四服务错误标记 = 0
```

最终 Beat 启动后连续 70 秒采样 8 次，均为 active 0、demo active 0、
RabbitMQ ready/unacked 0/0。数据库调度表同时显示：

```text
履约指标刷新 runs=4436 last_run=2026-07-30 03:56:00 CST
用户云存储作业投递 runs=627 last_run=2026-07-30 03:56:00 CST
```

这证明至少一个完整的一分钟生产 Beat 周期已发布、消费并回写。B2-02 切换与回滚演练
完成；检查点二的 24 小时稳定性观察仍在进行，尚未提前宣称通过。

## 5. B3-01 Redis 8 / redis-py 8 兼容性

### 5.1 客户端与切换契约

2026-07-30 04:00–04:13 CST 在隔离 worktree 完成 `redis-py==8.0.1`
精确锁版，`uv.lock` 与 `requirements.txt` 同步更新。应用 Redis 客户端新增两个显式
契约：

- `REDIS_PROTOCOL=2|3`，兼容阶段默认 RESP2；`RedisCli` 始终把该值传给
  redis-py，并启用兼容响应形状；
- `REDIS_LIST_MOVE_MODE=lua|lmove`，Redis 6 默认走原子 Lua，Redis 8 蓝绿验收
  后才由生产环境显式切到原生 `LMOVE LEFT RIGHT`，配置错误不会静默回退。

两条 pending/processing 路径均以 `LPOP/RPUSH` 等价语义保持 FIFO。Redis 8.0.1
异步连接池已提供原生连接计数契约，因此恢复 redis-py 原生 OpenTelemetry 指标；
标准 `opentelemetry-instrumentation-redis` 仍负责 Redis span。

实现提交为 `2b201ca8`，主分支合入提交为 `0e7d4a646`。

### 5.2 真实 Redis 8.8 集成测试

本机 Homebrew `redis-server` 为 Redis 8.8.0。测试在随机回环端口启动隔离真实进程，
使用每轮随机强密码、禁用持久化，并在结束时关闭进程；没有使用 fake Redis、内存
替代服务或假 fallback。

`backend/tests/test_redis8_integration.py` 的 7 个实际用例覆盖：

- RESP2 与 RESP3 的显式协商、兼容响应形状、事务 pipeline 和 TTL；
- 两个 RESP3 客户端的真实 Pub/Sub 往返；
- presence 写入、代际 Lua 刷新/注销、存活 TTL；
- 两个真实客户端竞争同一分布式锁；
- Lua 与原生 `LMOVE` 两条 pending/processing 路径的三条消息 FIFO；
- 隔离 Python 进程初始化 redis-py 原生 OTel，8 个并发异步 `PING` 后可读取
  `db.client.connection.count`，stderr 无 `Callback failed`。

验证结果：

```text
REDIS8_E2E=1 uv run pytest \
  backend/tests/test_redis_observability.py \
  backend/app/hasn_im/tests/test_routing_delivery_bus.py \
  backend/tests/test_redis8_integration.py \
  backend/tests/hasn/test_ws_delivery_bus.py -q
=> 25 passed, 96 warnings in 5.65s

uv run pytest backend/tests/test_redis_observability.py \
  backend/tests/hasn/test_ws_delivery_bus.py -q
=> 16 passed, 96 warnings in 0.10s

uv run mypy backend/database/redis.py \
  backend/app/hasn_im/adapters/routing/
=> Success: no issues found in 7 source files

uv run mypy backend/
=> Success: no issues found in 2999 source files

uv run prek run --files <B3-01 变更文件>
=> 全部 hooks 通过
```

扩大到整个 `backend/app/hasn_im/tests` 并连接真实 PostgreSQL
`127.0.0.1:15432` 后为 `205 passed, 2 failed`。两项失败均是共享测试库中既有
固定中文 nickname 撞唯一约束，与本阶段 Redis/WS 文件无关；未删除未知归属测试数据
来伪造全绿。

B3-01 已完成。B3-02 仍须执行新端口部署、RDB 恢复、逐服务切换和 24 小时观察，
本文不提前标记生产升级完成。

## 6. B3-02 Redis 8.8 蓝绿部署护栏与生产预检

### 6.1 可复现部署与快照核验

实现提交为 `29a6defe`，主分支合入提交为 `3db58a822`。`deploy/redis8/`
新增以下可执行护栏：

- 使用 digest 锁定官方 Redis 8.8.0 镜像，只监听回环地址 `9397`；
- 启用强密码、RDB、AOF `everysec`、`noeviction` 和 512 MiB 上限；
- bootstrap 在启动前校验 secret 文件权限、RDB 可读性和目录边界；
- healthcheck 不把密码放入进程参数；
- 只读快照核验覆盖 16 个 DB、全部 Redis 数据类型、TTL、DBSIZE 和
  `MEMORY STATS`，只输出摘要和 digest，不输出原始 key、URL 或业务值；
- 双 Redis 真实测试验证源实例不被修改、目标实例恢复后摘要一致。

验证结果：

```text
REDIS8_E2E=1 uv run pytest backend/tests/test_redis8_integration.py -q
=> 7 passed

uv run mypy backend/
=> Success: no issues found in 3003 source files

uv run prek run --files <B3-02 变更文件>
=> 全部 hooks 通过
```

本机没有 Docker，因此 Compose 真实启动和 RDB 恢复只允许在生产蓝绿窗口执行，未以
替代服务伪造结果。

### 6.2 生产 Redis 精确归属纠偏

2026-07-30 04:45 CST 的首次只读盘点确认生产有两个不同实例：

- 应用当前配置指向 systemd `redis-server.service`，进程
  `/usr/bin/redis-server 127.0.0.1:9396`，数据目录 `/var/lib/redis`，
  Redis 6.0.16；
- `*:6379` 实际可执行文件为 `/usr/local/bin/valkey-server`，属于另一服务，
  本方案不触碰。

应用配置使用 `127.0.0.1:9396`、DB 3；当前协议仍为 RESP2，LIST 移动仍为 Lua。
首次盘点的实例级只读状态为：

```text
memory = 6.00 MiB / peak 96.57 MiB
connected_clients = 249
evicted_keys = 0
rejected_connections = 0
rdb_last_bgsave_status = ok
aof_enabled = 0
nonempty_db = 0, 1, 3, 5, 6, 10
db3 = 196 keys（hash 3 / list 1 / set 3 / string 188 / zset 1）
```

蓝绿冻结窗口进一步按 `CLIENT LIST` 的数据库、进程和工作目录核对后，确认
`127.0.0.1:9396` 并不是本项目独占实例，而是至少被以下现存服务共享：

- `/www/wwwroot/java/dc_star`、`java/jpay/merchant`、`java/llf`、
  `java/tongyu`、`java/engineer`；
- lottery 项目的 Celery/API；
- `api.xingya.dcfuture.cn`；
- 本项目 API/worker/beat/Flower/IM/sync 服务。

核对时约有 242 个客户端，连接分布于 DB 0、1、3、5、6、8、10；同一 DB 3
也存在其他应用使用的通用 FBA 前缀。仅凭本项目 `.env` 的 DB 3 无法证明 key
命名空间归属，更不能把整个实例升级解释为“只迁移本项目”。此前“项目实例”的表述
不准确，以本节纠偏结论为准。

生产部署前快照位于：

```text
/data2/backups/方案B-B3B4预部署-20260730-044937
```

其中 PostgreSQL dump、代码归档和 env 快照均为权限 `0600`，`SHA256SUMS` 已校验。

### 6.3 Redis 8.8 真实恢复、核验与安全中止

2026-07-30 05:45 CST 在停止本项目精确服务后生成最终冻结快照：

```text
/data2/backups/redis8-cutover-20260730-054538
```

`redis6-final.rdb` 为 63,792 bytes，使用真实 `redis-check-rdb` 校验通过并读取
223 个 key。首次生产 bootstrap 暴露并修复了两个真实问题：

1. 官方 Redis 镜像 entrypoint 会把 `redis-check-rdb` 当作 `redis-server`
   参数；改为显式 `--entrypoint redis-check-rdb`，实现提交 `64de3d0a`，
   主分支合入提交 `afd64f1de`；
2. 初始即启用 AOF 会生成空 AOF 并优先于导入 RDB，导致目标实例空载启动；改为
   先以 RDB 启动并强制 `loaded_keys > 0`，再在线启用 AOF、等待 rewrite、持久化
   配置、重启并复核 key 数，实现提交 `e4b116d8`，主分支合入提交
   `2e609889d`。

首次空载产物没有删除，已隔离到可追溯目录：

```text
/data2/backups/redis8-failed-empty-import-20260730-054923
```

修复后真实 Redis 8.8.0 容器 `huanxing-redis8` 在回环端口 `9397` 健康运行，
恢复时载入 192 个尚未过期的 key，随后开启 AOF 并通过重启验证。快照核验对所有
可比较的 190 个 key 做了跨 DB 类型、值摘要和 TTL 对比，全部一致；源端多出的
3 个 key 均为仍在刷新或临近过期的短 TTL key，分别位于 DB 0 和 DB 6。这一持续
写入现象与 `CLIENT LIST` 共同暴露了共享实例事实。

因为无法证明 DB 3 及通用前缀为本项目独占，继续分批切换会造成跨应用双写分叉；
把所有共享调用方一并迁移又超出本任务授权范围。因此 B3-02 在写入任何应用 `.env`
之前安全中止：

- 本项目六个精确服务全部恢复到 Redis 6 并为 `RUNNING`；
- API 路径恢复，RabbitMQ Celery queue 为 `ready=0/unacked=0`；
- 生产 `.env` 仍指向 `127.0.0.1:9396`；
- Redis 8.8 保持隔离、不接生产应用流量，供后续真实测试与重新规划使用；
- 未停止、重启或迁移任何其他共享调用方。

B3-02 当前不是“待执行”，而是“已完成恢复验证、生产切换被共享实例归属门禁阻断”。
解除门禁必须先获得共享 Redis 全部调用方的迁移授权，或完成可证明无交叉读写的
独立命名空间迁移方案；不能为了勾选任务而绕过该安全边界。

## 7. B4-01 Socket.IO 消息管理器工厂

实现提交为 `732f05e8`，主分支合入提交为 `356c45e67`。默认
`SOCKETIO_MANAGER=redis` 保持既有 `AsyncRedisManager` / `RedisManager`；
RabbitMQ 模式使用 `AsyncAioPikaManager` / `KombuManager`，统一 channel
`huanxing.socketio`，并为每个进程声明符合生产权限边界的
`python-socketio.<uuid>` exclusive、auto-delete queue。API 与 Celery 启动均执行
连接预检，失败显式阻断启动，不回退到 Redis。

本地验证结果：

```text
uv run pytest backend/tests/socketio/ -q
=> 21 passed, 1 skipped

uv run pytest <B3/B4 相关测试> -q
=> 46 passed, 1 skipped, 140 warnings

uv run mypy backend/
=> Success: no issues found in 3003 source files

uv run prek run --files <B4-01 变更文件>
=> 全部 hooks 通过
```

2026-07-30 在生产服务器使用真实 RabbitMQ、两个独立 API 进程、两个真实 WebSocket
客户端和同步 publisher 执行该 E2E：

```text
RABBITMQ_SOCKETIO_E2E=1 uv run --frozen --env-file .env pytest \
  backend/tests/socketio/test_manager_factory_rabbitmq_e2e.py -q
=> 1 passed, 60 warnings in 7.61s
```

依赖同步还暴露出 `aio-pika` 只位于可选 `server` dependency group，导致生产
`uv sync --frozen` 后运行时缺包。已把它移入项目运行时依赖，测试覆盖冻结安装契约；
实现提交为 `8f1ffa15`，主分支合入提交为 `60dd788c7`。修复部署后 API、worker、
beat、Flower、IM consumer 和 sync worker 均恢复 `RUNNING`。

这证明 Redis/RabbitMQ 两种 factory 路径与 RabbitMQ 跨进程互通真实可用。生产
`SOCKETIO_MANAGER` 仍为 `redis`，按 B2 观察门槛尚未提前切换。

## 8. B5-01 realtime wake-up port 与 Redis adapter

实现提交为 `0e323c25`，主分支合入提交为 `7da98542e`。新增
`RealtimeWakeupBus`，只暴露 `publish_node_wakeup`、`publish_broadcast`、
`start`、`stop`；Redis Pub/Sub 的连接、频道、消息编解码、重连和资源释放全部移入
`RedisRealtimeWakeupBus`。`WsDeliveryBus` 继续原位负责 pending/processing LIST、
generation 校验、发送确认和周期补偿，不再创建 Redis Pub/Sub 连接。

真实 Redis 8.8 测试使用两个独立真实客户端验证 adapter 的定向唤醒、广播、订阅就绪
和停止释放；既有定向、广播、generation、processing 恢复语义未改。

```text
uv run pytest backend/tests/hasn/test_realtime_wakeup_bus_port.py \
  backend/tests/hasn/test_ws_delivery_bus.py \
  backend/app/hasn_im/tests/test_routing_delivery_bus.py -q
=> 19 passed, 96 warnings

REDIS8_E2E=1 uv run pytest backend/tests/test_redis8_integration.py -q
=> 8 passed, 60 warnings

uv run mypy backend/app/hasn_im/ports/ \
  backend/app/hasn_im/adapters/routing/
=> Success: no issues found in 18 source files

uv run prek run --files <B5-01 变更文件>
=> 全部 hooks 通过
```

B5-01 仅完成零行为变化抽象；生产默认配置保持 Redis active。

## 9. B5-02/B5-03 RabbitMQ realtime 与 shadow 对账

### 9.1 实现与静态验证

实现提交为 `7db54fe8`，主分支合入提交为 `9a098a1d4`。新增实现满足以下契约：

- robust fanout exchange 固定为 `huanxing.realtime`；
- 每个 worker 使用稳定、受限字符集的 instance ID 声明
  `huanxing.realtime.worker.<instance_id>`，queue 为 exclusive、auto-delete，
  并设置 5 分钟 `x-expires`；
- 消息只含严格版本、`event_id`、`sent_at_ms`、事件类型及最小唤醒字段，使用
  non-persistent delivery；畸形 schema 告警并 ACK，不 requeue；
- Redis active / RabbitMQ shadow 以同一业务意图双发，shadow consumer 只观测
  `event_id`，不调用用户 handler；
- RabbitMQ active 与 shadow 同时开启属于非法组合，启动阶段显式失败；
- 发布、消费、schema 错误和端到端延迟均接入低基数 Prometheus 指标；
- bus 在 worker 内惰性构造，避免 prefork 前复制连接或 instance ID；关闭时按
  consumer、channel、connection 顺序释放。

本地验证结果：

```text
uv run pytest backend/tests/hasn/test_rabbitmq_realtime_bus.py \
  backend/tests/hasn/test_ws_delivery_bus.py \
  backend/app/hasn_im/tests/test_routing_delivery_bus.py \
  backend/tests/test_rabbitmq_settings.py -q
=> 53 passed, 2 skipped, 96 warnings

uv run mypy backend/app/hasn_im/ports/ \
  backend/app/hasn_im/adapters/routing/ \
  backend/app/hasn_im/observability/metrics.py backend/core/registrar.py
=> Success: no issues found in 22 source files

uv run prek run --files <B5-02/B5-03 变更文件>
=> 全部 hooks 通过
```

### 9.2 生产 RabbitMQ 四 worker 与重连

2026-07-30 使用生产 RabbitMQ 和 `huanxing_realtime` 最小权限账号启动四个独立
realtime bus。真实定向、广播和单 worker 原位停止/重启共 3 个事件均向四个
consumer 各投递一次；重启前后临时 queue 名保持稳定，每个 worker 观测到的
`event_id` 集合与发布集合严格相等：

```text
RABBITMQ_REALTIME_E2E=1 uv run --frozen --env-file .env pytest \
  backend/tests/hasn/test_rabbitmq_realtime_bus.py::\
test_real_rabbitmq_fanout_to_four_workers_and_restart -q
=> 1 passed, 60 warnings in 0.44s
```

### 9.3 十万条真实 shadow 对账

shadow E2E 使用隔离 Redis 8.8 的 DB 15 作为 active、生产 RabbitMQ 作为
observe-only shadow；凭据仅以测试进程环境注入，没有写入生产 `.env`。

第一次真实运行在首批并发 200 个 Redis publish 时触发 redis-py 8 默认连接池上限
100 的 `MaxConnectionsError`。失败没有被重试掩盖：测试驱动改为读取真实连接池上限，
并把并发限制在“最多 50 且不超过连接池一半”，为订阅与同进程操作保留容量。修复提交
为 `559a6bca`，主分支合入提交为 `c63249ed9`；本地 `20 passed, 2 skipped`、
mypy 与 prek 全部通过。

修复后的生产真实结果：

```text
RABBITMQ_REALTIME_SHADOW_STRESS_E2E=1 \
HASN_REALTIME_BUS=redis HASN_REALTIME_SHADOW_RABBITMQ=true \
uv run --frozen --env-file .env pytest \
  backend/tests/hasn/test_rabbitmq_realtime_bus.py::\
test_real_shadow_reconciles_one_hundred_thousand_event_ids -q
=> 1 passed, 60 warnings in 61.47s
```

断言覆盖：

```text
published_event_ids = 100000
rabbitmq_consumed_event_ids = published_event_ids
redis_active_user_handler_calls = 100000
rabbitmq_shadow_user_handler_calls = 0
```

退出后 RabbitMQ 没有遗留 `huanxing.realtime.worker.*` queue，Redis 8 DB 15
`DBSIZE=0` 且 `hasn:ws:deliver` 订阅数为 0；Celery 默认 queue 仍为
`ready=0/unacked=0`，六个精确生产服务均为 `RUNNING`。生产
`HASN_REALTIME_BUS` 仍为 `redis`，`HASN_REALTIME_SHADOW_RABBITMQ` 仍关闭；
正式 shadow 观察窗受 B3-02 依赖门禁约束，尚未提前开启。

### 9.4 隔离 broker 真实重启恢复

2026-07-30 在生产服务器额外启动固定名称
`huanxing-rabbitmq-realtime-e2e` 的隔离 RabbitMQ 4.3.4 容器，镜像 digest
与生产锁版一致，仅映射回环固定端口 `35672`。测试先完成一次真实发布与消费，
再通过 Docker CLI 重启该隔离 broker；全程未停止或重启生产 RabbitMQ。

首次真实重启暴露出 adapter 缓存的 robust exchange 在 broker 重启后已经失效：
发布会抛出 `ChannelInvalidStateError`。修复后 publisher 会在锁内等待 robust
channel 恢复；未恢复时原子摘除旧 channel/connection 并重建。若 publish 已取得
exchange 后失败，则沿用同一 `event_id` 最多重放一次；唤醒消息不承载业务事实，
可能重复由既有 pending/generation 语义幂等收敛。该修复提交为 `8696e9b6`，
主分支合入提交为 `2487ae747`。

真实验证结果：

```text
RABBITMQ_REALTIME_RESTART_E2E=1 \
RABBITMQ_REALTIME_RESTART_CONTAINER=huanxing-rabbitmq-realtime-e2e \
uv run --frozen --env-file .env pytest \
  backend/tests/hasn/test_rabbitmq_realtime_bus.py::\
test_real_rabbitmq_recovers_after_isolated_broker_restart -q
=> 1 passed, 60 warnings in 10.18s
```

断言覆盖重启前后两个定向事件按顺序抵达、queue 名保持稳定，且 API 进程无需重启。
测试退出后固定容器、数据卷均不存在，端口 `35672` 空闲；生产 RabbitMQ 仍为
`running/healthy`，六个精确生产服务全部为 `RUNNING`。当前尚未把“真实 Redis
pending、四个完整 API worker、broker 停机与周期 drain”合并为一个端到端拓扑，
因此 B5-02 的对应组合验收项保持未勾选。

## 10. B6 离线恢复收口

### 10.1 B6-01/B6-03/B6-04 后端实现

实现提交为 `0eeed735`。`docs/方案B离线帧Durable覆盖矩阵.md` 逐项登记所有生产
`RealtimeFrame`，把它们归入 durable sync、瞬时无需离线或缺口待补；AST 静态守卫确保
新增离线方法没有登记时 CI 直接失败。实现同时满足：

- `HASN_OFFLINE_RECOVERY=dual` 时，客户端恢复只走 PostgreSQL sync/history；
  `hasn:offline:*` 继续影子写入，但不参与 claim、ACK 或用户可见展示；
- `HASN_OFFLINE_RECOVERY=sync` 在构造 Redis offline key 前停止写入、claim、ACK 和遗留读取；
- `hasn.task.exec` 的任务 run、dispatch outbox 和 sync event 共用业务事务与稳定
  `dispatch_id=task:run:{run_id}:exec`；
- 定时对账按稳定身份区分 Redis 独有、sync 独有和两边都有，指标只使用低基数
  `result` 标签；Redis 候选会精确查询仍保留的历史 sync 事实，避免 LIST 整键 TTL
  被新写入刷新后误报。

2026-07-30 复跑 B6/B7 相关测试：

```text
uv run pytest \
  backend/app/hasn_im/tests/test_architecture_guards.py \
  backend/app/hasn_im/tests/test_observability_metrics.py \
  backend/app/hasn_im/tests/test_offline_frame_policy.py \
  backend/app/hasn_im/tests/test_offline_shadow_reconciler.py \
  backend/app/hasn_im/tests/test_task_dispatch_outbox_pg.py \
  backend/tests/tasks/test_celery_broker_config.py \
  backend/tests/test_rabbitmq_observability.py -q
=> 68 passed，2 errors
```

两项 error 都来自真实 PostgreSQL fixture 连接 `127.0.0.1:5432` 被拒绝；没有改成
mock、skip 或假数据。它们是 B6 生产部署后必须补跑的真实事务与 Redis 恢复门槛，
因此 B6-01 尚不能标记生产验收完成。

### 10.2 B6-02 daemon durable 命令与同步补拉

`hasn-node` 使用既有 worktree
`.worktrees/doc03-message-history-bootstrap`、分支
`fix/doc03-message-history-bootstrap` 实现，并于 2026-07-30 fast-forward 合入主分支、
从主 clone 推送至 `origin/main@ac637fbe2`。该分支从 `c972720ac` 起包含消息历史
bootstrap 全链，新增 durable 命令提交为 `03c0ee844`，Clippy 守卫收口为
`ac637fbe2`：

- owner schema 新增 V027 `sync_command_inbox`，实时 `hasn.task.exec` 与 sync pull
  先经过同一个 SQLite 幂等收件箱；
- 命令使用租约领取、到期接管和持久化退避，登录、冷启动、恢复、重连和周期补拉均会
  drain；
- 调度入口对运行中稳定工作会话和 completed/error/cancelled 终态工作会话均返回
  `AlreadyRunning`，避免同一 `dispatch_id` 重复执行；
- `message.recalled` 在 SQLite 事务内原子修正消息状态、正文和会话预览；
  `conversation.updated` 按权威 revision 回源，失败时不推进 cursor；
- retention gap 进入历史快照，WS 与 sync 依靠稳定 ID 收敛。

截至 2026-07-30 的定向验证：

```text
cargo test -p hasn-node runtime::sync_pull
=> 56 passed

cargo test -p hasn-daemon --test cross_device_msg_sync
=> 3 passed

cargo test -p hasn-daemon --lib \
  wire_session::tests::handle_inbound_task_exec_dispatches_runtime_and_reports_task_result \
  -- --exact
=> 1 passed
```

此外，消息撤回、SQLite inbox、运行中/终态重复、迁移目录和四库参考 schema 的精确测试
全部通过；`cargo fmt --all -- --check`、`cargo check -p hasn-node`、
`cargo check -p hasn-daemon`，以及下列生产代码 Clippy 也已通过：

```text
cargo clippy -p hasn-node -p hasn-daemon --lib --all-features -- -D warnings
=> 通过
```

全 targets Clippy 先发现并修复本分支测试的 `expect_used`、`panic` 和不必要借用；
继续运行后只剩主线既有增长派发测试的
`literal_string_with_formatting_args`。为继续审计而临时放行该单项时，外置
`/Volumes/ExtraData` 从系统中消失，导致未修改的 `hasn-mcp` 编译器进程以
`SIGBUS` 退出；`diskutil` 已确认对应物理盘不再可见。内置盘仅余约 24 GiB，
不得用新建本地全量 target 把系统盘写满。因此 `--all-targets --all-features`
门槛保持未完成，外置盘恢复后必须从明确 target 重跑。真实云端、PostgreSQL、
双设备和 `p0_real_e2e.py` 仍须等待后端与 daemon 同窗部署。

### 10.3 尚未通过的 B6 生产门槛

- 后端新代码尚未生产部署，`HASN_OFFLINE_RECOVERY` 仍未进入生产 `dual`。
- 生产 7 天 shadow 尚未开始；没有
  `redis_only_unrecoverable=0` 的连续七天证据。
- 真实双设备断网、撤回、成员变更、任务命令、retention gap、空库恢复尚未完成。
- 上述门槛通过前不得切到 `sync`，也不得清理旧 Redis offline key。

## 11. B7 可观测与生产收口

### 11.1 代码与监控资产

实现提交为 `05551659`，主分支合入提交为 `c641db26a`，私网防火墙 Runbook 修订为
`935600d1f`。实现包含：

- W3C `traceparent`/`tracestate` 传播，不传播 baggage；
- `hasn.im.integration_event.process → rabbitmq.publish → rabbitmq.process →
  websocket.send` span 链；
- publish confirm、delivery ACK 和 redelivery 应用指标；
- RabbitMQ 原生指标的私网 systemd socket proxy；
- 生产 dashboard、Prometheus 采集、10 条告警规则和完整 Runbook。

本地验证结果：

```text
RabbitMQ/B6/B7 非 PostgreSQL 定向用例
=> 68 passed

uv run mypy <6 个 B7 源文件>
=> Success: no issues found

uv run pytest backend/tests/test_rabbitmq_observability.py -q
=> 7 passed

uv run prek run --files <B7 变更文件>
=> 全部 hooks 通过
```

### 11.2 生产 Prometheus 与 Grafana

2026-07-30 生产配置备份位于：

```text
/data2/huanxing-observability/backups/scheme-b-observability-20260730-101155
```

RabbitMQ 原生 Prometheus endpoint 保持 `127.0.0.1:15692`。生产启用精确的 systemd
socket proxy：

```text
listen  = 172.24.0.1:15693
forward = 127.0.0.1:15692
service = active，NRestarts=0
```

UFW 只允许固定 Prometheus 地址 `172.24.0.250` 访问该私网端口；从外部网络探测
15693 不可达。Prometheus 只读挂载 rules 目录并仅重建自身容器后：

- `rabbitmq` target 为 `up`；
- `scheme-b-rabbitmq` 规则组 10/10 `health=ok`，当前全部 inactive；
- Grafana file provider 已加载 UID `huanxing-scheme-b-rabbitmq`，位于“唤星生产”目录。

10:23 CST 的真实 broker 样本：

```text
published=19388
confirmed=19388
returned=0
delivered_ack=1802
acked=1770
redelivery=0
ready=0
unacked=0
consumers=4
memory_alarm=0
disk_alarm=0
```

本轮仅重建 Prometheus，并未重启 RabbitMQ、Celery、API 或 HASN worker，因而没有重置
B2 的 Celery/RabbitMQ 稳定观察窗。

### 11.3 尚未通过的 B7 门槛

- 后端 B7 代码尚未生产部署，真实消息 trace 还不能在 Tempo/Grafana Explore 验证。
- Grafana 当前没有 contact point，默认 policy receiver 为 `empty`。必须取得真实
  PagerDuty、企业微信、邮件或 webhook 接收器后，才能执行通知和 resolved 闭环；
  禁止配置本地或伪造接收器冒充验收。
- 在真实接收器就绪前不得执行 memory/disk alarm 生产演练。
- 当前环境没有可用的已登录 Browser 会话，dashboard JSON 与加载状态已机械验证，
  但可视化截图验收仍待浏览器会话。

## 12. 后续证据索引

| 阶段 | 证据 |
|---|---|
| B0–B2 | 本文持续补充；生产部署记录另存父仓 `docs/生产部署/部署记录/` |
| B3 | B3-01 见第 5 节；B3-02 见第 6 节，恢复核验已完成，生产切换被共享实例归属门禁阻断 |
| B4 | 实现和生产真实互通见第 7 节，正式切换等待观察门槛 |
| B5 | B5-01 见第 8 节；B5-02/B5-03 实现及生产真实测试见第 9 节 |
| B6 | 实现与当前验证见第 10 节；生产 dual 七天观察和真实双设备 E2E 待完成 |
| B7 | 代码、生产采集、dashboard 和规则见第 11 节；真实 trace、接收器与 alarm 演练待完成 |
