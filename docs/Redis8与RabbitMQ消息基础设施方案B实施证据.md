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

## 4. 后续证据索引

| 阶段 | 证据 |
|---|---|
| B0–B2 | 本文持续补充；生产部署记录另存父仓 `docs/生产部署/部署记录/` |
| B3 | 待 Redis 8 蓝绿分支建立后登记 |
| B4 | 待 Socket.IO 分支建立后登记 |
| B5 | 待 realtime 分支建立后登记 |
| B6 | 待 offline sync 后端/daemon 分支建立后登记 |
| B7 | 待可观测与最终生产收口分支建立后登记 |
