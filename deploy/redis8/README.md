# Redis 8 单机部署与快照迁移

本目录用于把生产应用 Redis 从专用 Redis 6 实例迁移到 Redis 8.8.0。容器仅监听 `127.0.0.1:9397`，镜像按 digest 锁定，数据位于 `/data2/huanxing-redis8`，同时启用 RDB 与 AOF。

## 安全边界

- 先确认旧实例仅由本项目使用，并盘点 API、Celery Worker、Beat、IM Consumer、同步 Worker 等全部写入者。
- 导入的 RDB 必须在全部写入者停止后生成；校验完成前不得切换应用连接。
- Redis 8 接受新写入后，旧 Redis 6 不再是可直接切回的权威副本。回滚必须先冻结写入，再用经过验证的逻辑导出或兼容快照把 Redis 8 的新增数据同步回可接管实例。
- 禁止在命令参数、日志、Supervisor 配置或版本库中写入 Redis 密码。
- 旧实例在观察期内保留，但不得继续接受业务写入；确认无共享调用方后才能设置只读限制。

## 初始化

1. 在服务器创建 `/data2/huanxing-redis8/secrets/bootstrap.env`，仅写入一行 `REDIS8_PASSWORD=<随机值>`。随机值必须是 32–128 位 URL-safe 字符串，文件权限必须为 `600`。
2. 停止并确认全部旧 Redis 写入者已经退出，执行 `BGSAVE`，等待 `rdb_bgsave_in_progress=0` 且 `rdb_last_bgsave_status=ok`。
3. 把最终 RDB 复制到独立备份目录，记录文件大小和 SHA-256，不得直接使用仍可能被旧实例覆盖的活动文件。
4. 设置 `REDIS8_SOURCE_RDB` 为该只读快照的绝对路径，执行 `sudo --preserve-env=REDIS8_SOURCE_RDB ./bootstrap.sh`。
5. 脚本会校验密钥权限、RDB 完整性、镜像版本及容器健康状态；目标数据已存在时会拒绝覆盖。

## 切换前只读校验

分别通过环境变量提供源、目标连接，随后执行：

```bash
SOURCE_REDIS_URL='<源连接>' \
TARGET_REDIS_URL='<目标连接>' \
./verify_snapshot.py
```

校验器遍历 16 个逻辑库，对比 `dbsize`、键集合、类型、内容摘要和 TTL；键名只输出不可逆短摘要，连接信息不会输出。它覆盖 string、list、set、zset、hash 和 stream，遇到未知类型会显式失败。60 秒内即将过期的键不参与逐键摘要，但停写窗口内 `dbsize` 仍须完全一致。

校验通过后，还必须确认：

- `redis-server --version` 为 `8.8.0`；
- 宿主机只有 `127.0.0.1:9397` 在监听；
- 容器健康状态为 `healthy`，无重启循环；
- RDB 最近保存成功，AOF 已启用且 `aof_last_write_status=ok`；
- 应用使用 RESP2 和 Lua 列表移动兼容模式完成首轮切换；
- API、Worker、Beat、IM Consumer 与同步 Worker 逐个重启并完成真实读写验证。

## 观察与回滚

切换后持续记录错误率、延迟、连接数、命中率、内存、淘汰数、持久化状态和容器重启次数。回滚演练必须在隔离实例完成，证明 Redis 8 产生的新写入可以无损迁回，再进入生产观察期。

生产回滚的顺序是：停止全部写入者、确认无活动写入、生成并校验 Redis 8 最终快照、迁入兼容接管实例、运行本目录的只读校验器、修改应用连接、按依赖顺序逐个启动服务。任一校验失败都必须保持停写并进入故障处置，禁止带数据差异强行启动业务。
