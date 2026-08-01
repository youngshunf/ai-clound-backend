# RabbitMQ 单节点生产部署

本目录部署 RabbitMQ 4.3.4，供 Celery、Socket.IO 和 HASN realtime 分阶段迁移使用。它是单节点故障域，不提供高可用；迁移期间应用默认仍使用 Redis，只有相应阶段的开关显式切换后才会使用 RabbitMQ。

## 安全与存储边界

- 容器使用 host network，5672、15672、15692、EPMD 4369 和单节点分发端口 25672 都只监听 `127.0.0.1`，禁止另加公网端口映射。
- 管理界面只能通过 SSH 隧道访问；Prometheus 也只能从本机采集。
- 数据、日志、密钥和 definitions 备份统一落在 `/data2/huanxing-rabbitmq/`。
- 仓库 definitions 不包含用户、密码、密码哈希或完整 DSN。三个角色密码只存在于服务器权限为 `600` 的密钥文件。
- `guest` 仅在首次启动到 bootstrap 完成前存在且只能 loopback 登录；bootstrap 会删除它。
- RabbitMQ Prometheus endpoint 仍只监听 `127.0.0.1:15692`。容器化 Prometheus 通过
  systemd socket proxy 访问 `172.24.0.1:15693`；该地址只属于可观测 Docker 私网，
  禁止改为 `0.0.0.0` 或公网地址。

## 首次部署

在服务器后端仓库已更新到目标提交后执行：

```bash
RABBITMQ_ROOT=/data2/huanxing-rabbitmq
install -d -o 999 -g 999 -m 750 \
  "$RABBITMQ_ROOT/data" \
  "$RABBITMQ_ROOT/logs"
install -d -o root -g root -m 700 \
  "$RABBITMQ_ROOT/secrets" \
  "$RABBITMQ_ROOT/backups"

umask 077
{
  printf 'RABBITMQ_CELERY_PASSWORD=%s\n' "$(openssl rand -hex 32)"
  printf 'RABBITMQ_REALTIME_PASSWORD=%s\n' "$(openssl rand -hex 32)"
  printf 'RABBITMQ_MONITOR_PASSWORD=%s\n' "$(openssl rand -hex 32)"
} > "$RABBITMQ_ROOT/secrets/bootstrap.env"
chmod 600 "$RABBITMQ_ROOT/secrets/bootstrap.env"

cd deploy/rabbitmq
docker compose config --quiet
docker compose pull
docker compose up -d
./bootstrap.sh

install -o root -g root -m 644 \
  huanxing-rabbitmq-prometheus-proxy.socket \
  huanxing-rabbitmq-prometheus-proxy.service \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now huanxing-rabbitmq-prometheus-proxy.socket
ufw allow from 172.24.0.250 to 172.24.0.1 port 15693 proto tcp \
  comment 'huanxing prometheus rabbitmq scrape'
```

不要把密钥值粘贴到命令行历史、工单、日志或 Git。应用 `.env` 只注入自身角色的账号和密码，禁止跨角色复用。

## 验证

```bash
cd deploy/rabbitmq
docker compose ps
docker compose exec -T rabbitmq rabbitmq-diagnostics status
docker compose exec -T rabbitmq rabbitmq-diagnostics check_running
docker compose exec -T rabbitmq rabbitmq-diagnostics check_local_alarms
docker compose exec -T rabbitmq rabbitmq-diagnostics -s listeners
docker compose exec -T rabbitmq rabbitmqctl list_vhosts
docker compose exec -T rabbitmq rabbitmqctl list_permissions -p huanxing
docker compose exec -T rabbitmq rabbitmqctl list_queues \
  -p huanxing name durable messages_ready messages_unacknowledged consumers
systemctl status --no-pager huanxing-rabbitmq-prometheus-proxy.socket
curl --fail --silent --show-error http://172.24.0.1:15693/metrics >/dev/null
```

宿主机 RabbitMQ 原生 listener 必须只出现 `127.0.0.1:4369`、`127.0.0.1:5672`、
`127.0.0.1:15672`、`127.0.0.1:15692` 和 `127.0.0.1:25672`；额外允许 systemd
socket proxy 监听 `172.24.0.1:15693`。还必须从公网独立探测 RabbitMQ 原生端口与
15693 均不可达。管理界面只允许这样建立临时隧道：

```bash
ssh -N -L 15672:127.0.0.1:15672 huanxing-server2
```

## definitions 备份与恢复

切换前及每次拓扑变更后导出不含消息正文的 definitions：

```bash
cd deploy/rabbitmq
backup="/data2/huanxing-rabbitmq/backups/definitions-$(date +%Y%m%d-%H%M%S).json"
docker compose exec -T rabbitmq rabbitmqctl export_definitions - > "$backup"
chmod 600 "$backup"
```

恢复前先确认目标文件和当前队列状态，再执行：

```bash
cd deploy/rabbitmq
docker compose cp /data2/huanxing-rabbitmq/backups/<已确认文件>.json \
  rabbitmq:/tmp/restore.json
docker compose exec -T rabbitmq rabbitmqctl import_definitions /tmp/restore.json
```

持久化数据目录不得用 `docker compose down -v`、递归删除或清理 Docker root 的方式处理。需要回滚应用消息链路时，按阶段 runbook 将应用开关切回 Redis；RabbitMQ 保持运行以保留现场和指标。
