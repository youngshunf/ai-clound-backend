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

## 5. 后续证据索引

| 阶段 | 证据 |
|---|---|
| B0–B2 | 本文持续补充；生产部署记录另存父仓 `docs/生产部署/部署记录/` |
| B3 | 待 Redis 8 蓝绿分支建立后登记 |
| B4 | 待 Socket.IO 分支建立后登记 |
| B5 | 待 realtime 分支建立后登记 |
| B6 | 待 offline sync 后端/daemon 分支建立后登记 |
| B7 | 待可观测与最终生产收口分支建立后登记 |
