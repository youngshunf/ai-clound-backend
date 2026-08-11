# RUNBOOK · 生产库菜单清理（`sys_menu`）

> 对应脚本：[`2026-08-10-清理无页面与应用面菜单.sql`](./2026-08-10-清理无页面与应用面菜单.sql)
> 设计事实源：`docs/产品与技术/技术设计/02-平台能力/运营管理面/实施/01-运营管理面建设施工清单.md` 批次 0（T0.1 / T0.2）
> 状态：**该 SQL 至今只在本地开发库执行过，生产未执行。** 本文件是给人执行的操作手册，不是自动化脚本。

---

## 0. 这份 runbook 解决什么问题

批次 0 删掉了一批「点开必 404 的菜单」和「已移出平台运营面的 AI-Native 应用菜单」。
删除动作分两半：

| 半 | 内容 | 状态 |
|---|---|---|
| 种子文件 | `backend/sql/generated/` 下对应的 `*_menu.sql` / `*_dict.sql` | 已随代码提交，各环境拉代码即生效 |
| **库里已有的行** | 生产库 `sys_menu` / `sys_role_menu` 的存量行 | **本 runbook 要做的事** |

只删种子不删库行 → 生产侧边栏仍旧挂着一堆 404 菜单。
只删库行不删种子 → 谁重跑一次种子就长回来。两半都要做，顺序不限。

### 三个必须先建立的认知

1. **不要相信任何预写死的行数。** SQL 文件头部按开发库算出「768 → 344」，开发库执行完实测是 **354**。
   这 10 行差已查清：清理执行后又**人工恢复**了 `/hasn/hasn_group_members` 与 `/hasn/hasn_unread_counts`
   两条菜单及其 8 条按钮权限（共 10 行）——它们的页面存在且是批次 1「内容安全」的读数据源，误列进了
   C 类清单。**344 是 SQL 的真实影响结果，354 是恢复后的库状态，两个数都对。**
   即便如此，这条纪律仍然成立：生产库的基数与种子执行历史与开发库不同，**行数一定不一样**。
   判据只能来自「本环境的只读审计」和「脚本自己打印的 `RAISE NOTICE`」，不能来自文档里的数字。
2. **按 `path` 删而不是按 `id` 删。** `sys_menu.id` 是 `SERIAL`，开发库与生产库的 id 完全不对应；
   `path` 来自同一份种子 SQL，跨环境稳定。这是该 SQL 能原样搬到生产的唯一前提 ——
   §2 的第 2 步会验证这个前提在生产库同样成立，**验证不过就停手**。
3. **删的是「菜单行」，不是「数据」。** 业务表一行不动。最坏情况是运营在侧边栏里少看见几个入口，
   通过备份 CSV 可以完整还原。这决定了本操作的风险等级：可回滚、不丢业务数据。

---

## 1. 前置检查（只读，不改任何东西）

### 1.1 约定：连接串一律用占位符

本文件中出现的下列写法**全部是占位符**，执行时替换成真实值，且**不要**把真实值写回本文件或任何提交物：

```
<PGHOST>     生产库主机
<PGPORT>     生产库端口
<PGUSER>     生产库用户
<PGDATABASE> 生产库库名
<PGPASSWORD> 生产库密码（走环境变量或 ~/.pgpass，不要写进命令行）
<备份目录>    本机绝对路径，例如 /Users/<你>/huanxing-backups/sys_menu-prod
```

推荐用 `~/.pgpass`（`chmod 600`）而不是在命令行里带密码 —— 命令行会进 shell history 和 `ps` 输出：

```bash
# ~/.pgpass 一行的格式：hostname:port:database:username:password
chmod 600 ~/.pgpass
export PGHOST=<PGHOST> PGPORT=<PGPORT> PGUSER=<PGUSER> PGDATABASE=<PGDATABASE>
```

下文所有 `psql` 命令都假设已 export 上面四个变量。

### 1.2 确认连到的确实是生产库（每次都做，别凭记忆）

```bash
psql -v ON_ERROR_STOP=1 -c "select current_database(), current_user, inet_server_addr(), inet_server_port(), version();"
```

对不上就停手。**连错库执行一次删除，是本操作唯一不可逆的失误方式。**

### 1.3 摸清当前 `sys_menu` 状态

```bash
psql -v ON_ERROR_STOP=1 \
  -c "select count(*) as sys_menu_总行数 from sys_menu;" \
  -c "select type, count(*) from sys_menu group by type order by type;" \
  -c "select count(*) as sys_role_menu_总行数 from sys_role_menu;"
```

`type` 的含义：`0` 目录、`1` 菜单、`2` 按钮权限、`3` 内嵌、`4` 外链。
开发库执行**后**的形状可作参考（不是生产的预期值）：总 354 = 目录 17 + 菜单 78 + 按钮 256 + 内嵌 1 + 外链 2。

### 1.4 只读审计：这次到底会删掉哪些行

**这是整个流程里最重要的一步。** 有两种做法，能跑 Python 的优先用第一种。

**做法 A · 跑审计脚本（推荐，能同时看到「有页面却没菜单」的反向清单）**

脚本是纯只读的（`backend/tests/test_ops_console_menu_audit.py` 里有静态守卫断言它不含任何 DML 与 `commit`）。
它需要两样东西：能连生产库，以及一份**前端源码 checkout**（用来判断 `.vue` 是否存在）。

```bash
cd <hasn-cloud-backend 主 clone 或 worktree 根>

# 环境变量优先级高于 backend/.env（已实测：设 DATABASE_PORT=15999 后脚本确实去连 15999 并失败退出 2），
# 所以不需要、也**不要**去改 backend/.env
DATABASE_HOST=<PGHOST> DATABASE_PORT=<PGPORT> \
DATABASE_USER=<PGUSER> DATABASE_PASSWORD=<PGPASSWORD> DATABASE_SCHEMA=<PGDATABASE> \
uv run python -m backend.scripts.ops_console.audit_menus \
    --frontend-src <hasn-cloud-frontend>/apps/web-antdv-next/src \
    --csv <备份目录>/生产待删菜单清单.csv
```

⚠️ `--frontend-src` 指向的必须是 **`src`** 而不是 `src/views`：前端 `pageMap` 同时 glob `views/` 和
`plugins/` 两个根，只给 `views` 会把 `plugins/` 下在用的页面误判成 404，从而把不该删的菜单列进清单。
脚本缺省会从自身位置向上找父项目里的前端仓；跨机执行时**显式传**，不要依赖缺省。

产出的 CSV 有 `action` 列（待删 / 连带删除 / 待人工确认 / 保留），**逐行过一遍**，尤其看：

- 有没有运营当前真的在用的入口被划进「待删」；
- 「待人工确认」那些行的裁决（批次 0 的既定裁决：`hasn_group_members`、`hasn_unread_counts` **保留**，
  它们是批次 1「内容安全」的读数据源，场景页建成前是运营看这些数据的唯一入口）。

**做法 B · 只用 psql（生产机上没有前端 checkout / 没有 Python 环境时）**

直接查「SQL 里固化的待删 path 在生产库命中几行」。把 SQL 文件第 1 步里的 path 清单原样复制进来即可：

```sql
-- 只读预演：命中数、连带后代数、会变空的目录
-- 做法：把 2026-08-10-清理无页面与应用面菜单.sql 从 BEGIN 开始到「第 4 步」结束的内容原样执行，
--       但**在末尾用 ROLLBACK 代替 COMMIT**。第 4 步的 RAISE NOTICE 会打印全部数字，
--       而 ROLLBACK 保证一行都没真删。
```

```bash
# 用 sed 把文件末尾的 COMMIT 换成 ROLLBACK，产出一份一次性的预演脚本（不要提交这个文件）
sed 's/^COMMIT;$/ROLLBACK;/' backend/sql/ops_console/2026-08-10-清理无页面与应用面菜单.sql \
    > /tmp/menu_cleanup_dryrun.sql

psql -v ON_ERROR_STOP=1 -f /tmp/menu_cleanup_dryrun.sql
```

留意输出里这一行 NOTICE，**把数字抄下来**，正式执行时要能对上：

```
sys_menu 执行前 N 行；本次删除 M 行（直接命中 a、连带后代 b、变空目录 c），剩余 N-M 行；sys_role_menu 关联行 r
```

### 1.5 验证「path 在生产库唯一且非空」这个前提

```sql
-- 期望：两个查询都返回 0 行。任一有结果就停手 —— 按 path 删的前提在生产库不成立。
SELECT path, count(*) FROM sys_menu WHERE path IS NOT NULL GROUP BY path HAVING count(*) > 1;
SELECT id, title, type FROM sys_menu WHERE path IS NULL OR path = '';
```

### 1.6 备份（**没有备份就不许往下走**）

```bash
mkdir -p <备份目录>

# \copy 是客户端侧命令，不需要服务端文件写权限；注意它**不展开 ~**，必须写绝对路径
psql -v ON_ERROR_STOP=1 \
  -c "\copy (SELECT * FROM sys_menu ORDER BY id) TO '<备份目录>/sys_menu_prod_$(date +%Y%m%d%H%M).csv' CSV HEADER" \
  -c "\copy (SELECT * FROM sys_role_menu ORDER BY id) TO '<备份目录>/sys_role_menu_prod_$(date +%Y%m%d%H%M).csv' CSV HEADER"

# 立刻核对行数与 §1.3 的计数一致（表头占 1 行，所以是 count+1）
wc -l <备份目录>/*.csv
```

**必须做的一步：验证备份真的能还原。** 别等出事才发现 CSV 是坏的 ——
在**非生产**的空库/临时库上把 CSV 灌回一张同结构的临时表，双向 `EXCEPT` 应为 0 差异：

```sql
CREATE TEMP TABLE t_menu (LIKE sys_menu INCLUDING ALL);
\copy t_menu FROM '<备份目录>/sys_menu_prod_<时间戳>.csv' CSV HEADER
SELECT 'csv缺行' AS 方向, count(*) FROM (SELECT * FROM sys_menu EXCEPT SELECT * FROM t_menu) x
UNION ALL
SELECT 'csv多行', count(*) FROM (SELECT * FROM t_menu EXCEPT SELECT * FROM sys_menu) y;
-- 期望两行都是 0
```

同时留一份**逻辑备份**兜底（CSV 只保内容，`pg_dump` 连结构和序列一起保）：

```bash
pg_dump -t sys_menu -t sys_role_menu -Fc -f <备份目录>/menu_tables_$(date +%Y%m%d%H%M).dump
```

### 1.7 前置检查清单（逐项打勾，缺一不可）

- [ ] §1.2 确认连的是生产库
- [ ] §1.3 记录执行前的 `sys_menu` / `sys_role_menu` 行数
- [ ] §1.4 只读审计跑过，CSV 或 NOTICE 数字已人工过目
- [ ] §1.5 `path` 唯一且非空
- [ ] §1.6 CSV 备份 + `pg_dump` 备份都已产出，且 CSV 还原验证通过
- [ ] 代码侧的种子文件删除已经**合并进 `hasn` 主分支**（否则下次部署重跑种子会长回来）
- [ ] 已知会周期性重跑菜单种子的定时任务 / 部署钩子已确认不会在窗口内触发
- [ ] 选了低峰窗口，且有运营侧的人能在执行后 10 分钟内肉眼验一遍侧边栏

---

## 2. 执行

```bash
psql "postgresql://<PGUSER>@<PGHOST>:<PGPORT>/<PGDATABASE>" -v ON_ERROR_STOP=1 \
     -f backend/sql/ops_console/2026-08-10-清理无页面与应用面菜单.sql \
     2>&1 | tee <备份目录>/执行日志_$(date +%Y%m%d%H%M).log
```

关于这条命令：

- **`-v ON_ERROR_STOP=1` 不能省。** 不带它时 psql 遇错会继续往下跑，
  可能在一个已经报废的事务里走完剩下的语句。
- SQL 文件**自带 `BEGIN` / `COMMIT`**，整体原子；任一步报错即整体回滚，不会留下删一半的状态。
- 文件**幂等**：清单按 `path` 匹配，行已不在就匹配不到，`DELETE` 影响 0 行。
  重跑安全 —— 「空目录」那一步要求「该目录有子节点在本次删除集合里」，重跑时删除集合为空，
  因此**不会**误伤后续新建的空目录。
- `tee` 存日志：里面的两条 `RAISE NOTICE`（第 4 步的影响面、第 6 步的自检结论）是事后对账的唯一凭据。

### 预期影响行数

| 环境 | 执行前 | 删除 | 执行后 |
|---|---|---|---|
| 本地开发库（2026-08-10 实测） | 768 | 414 | **354** |
| SQL 文件头部的预测值 | 768 | 424 | 344 |
| 生产库 | §1.3 的实测值 | §1.4 只读审计给出的值 | 相减 |

⚠️ **354 与 344 差的 10 行是执行后人工恢复的两条内容安全菜单（含按钮权限），不是 SQL 算错**。
但「按开发库算出来的行数」
连在开发库自己身上都不完全准。所以：

> **生产的预期行数只有一个合法来源 —— §1.4 只读预演打印的那条 NOTICE。**
> 正式执行时第 4 步会打印同一条 NOTICE；**两次数字必须一致**，不一致说明两次之间库变过，
> 立刻按 §5 停止条件处理。

---

## 3. 执行后验证

### 3.1 数字对账（psql）

```bash
psql -v ON_ERROR_STOP=1 \
  -c "select count(*) as 执行后总行数 from sys_menu;" \
  -c "select type, count(*) from sys_menu group by type order by type;" \
  -c "select count(*) from sys_role_menu;"
```

判据：`执行后总行数` == `§1.3 的执行前行数` − `§2 NOTICE 里的删除行数`。对不上就查日志，别继续。

### 3.2 无悬挂引用（SQL 第 6 步已经查过一次，这里是独立复核）

```sql
-- 两个都必须是 0
SELECT count(*) AS 孤儿菜单 FROM sys_menu c
 WHERE c.parent_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys_menu p WHERE p.id = c.parent_id);

SELECT count(*) AS 悬挂角色关联 FROM sys_role_menu rm
 WHERE NOT EXISTS (SELECT 1 FROM sys_menu m WHERE m.id = rm.menu_id);
```

### 3.3 跑守卫脚本 `check_menu_pages.py`

这是本批的**正门判据**：断言 `sys_menu` 里每一行可下发到侧边栏的菜单，都能在前端找到对应 `.vue`。

```bash
cd <hasn-cloud-backend 主 clone 或 worktree 根>

DATABASE_HOST=<PGHOST> DATABASE_PORT=<PGPORT> \
DATABASE_USER=<PGUSER> DATABASE_PASSWORD=<PGPASSWORD> DATABASE_SCHEMA=<PGDATABASE> \
uv run python -m backend.scripts.ops_console.check_menu_pages \
    --frontend-src <hasn-cloud-frontend>/apps/web-antdv-next/src
echo "退出码=$?"
```

退出码含义：

| 码 | 含义 | 处置 |
|---|---|---|
| `0` | 通过，所有可下发菜单都有页面 | 继续 §3.4 |
| `1` | 有菜单行找不到页面 | 看它打印的清单：是本次漏删的，还是别人新加的坏菜单 |
| `2` | **守卫自身没法工作**（前端路径错、库读不到、一行都没校验到） | 当作「未验证」，不是「通过」；修好再跑 |

`2` 这一档是刻意设计的 —— 静默放行的守卫比没有守卫更糟。已实测：把 `DATABASE_PORT` 指到一个
没人监听的端口，脚本报 `ConnectionRefusedError` 并以 `2` 退出，不会假装通过。

**要看的数字**（本地开发库执行后的实测值，供形状参考，不是生产的预期值）：

```
页面索引：141 个 .vue
已校验菜单行：81；跳过（按钮/空组件/layout 组件）：273
[通过] 所有可下发的菜单行都能找到对应页面。
```

- `已校验菜单行 + 跳过` 应当等于 `sys_menu` 总行数（本地 81 + 273 = 354 ✓）。对不上说明取数口径出了问题。
- `已校验菜单行` **不能是 0** —— 是 0 说明守卫空转，脚本自己会以 `2` 退出。

### 3.4 侧边栏肉眼确认（必须由人做，脚本替代不了）

用**管理员账号**登录生产运营后台，硬刷新（`Cmd/Ctrl + Shift + R`，菜单会被前端缓存）：

1. **该消失的消失了**：侧边栏不再出现 `创作者` / `线索自动化` / `增长` / `副驾` / `设计` / `短片` /
   `记忆` / `图坊` / `项目` / `通知` / `社区` / `任务` / `托管` 这些一级目录
   （对应 SQL 头部列出的 14 个变空目录；生产实际清单以 §1.4 审计输出为准）。
2. **该留的还在**：`系统管理`、`日志`、`监控`、`调度`、`存储`、`LLM`、`用户与订阅`（`user_tier`）、
   `技能市场`（`marketplace`）等平台运营入口正常。
3. **随机点开 5～10 个保留下来的菜单**，确认都能正常打开、不是 404 —— 守卫只证明「文件存在」，
   证明不了「页面能跑」。
4. **换一个非管理员的运营角色再登一次**：`sys_role_menu` 被删过关联行，要确认该角色该看到的还看得到。
   （开发库实测 `sys_role_menu` 56 行里没有一行指向待删菜单，删除数为 0；生产未必如此，
   以 §1.4 NOTICE 里的 `sys_role_menu 关联行 r` 为准 —— **`r > 0` 时这一步是必查项，不能跳过**。）

---

## 4. 回滚

### 4.1 什么时候回滚

- §3.1 数字对不上，且无法解释；
- §3.2 出现悬挂引用（正常情况下 SQL 第 6 步会自己 `RAISE EXCEPTION` 并整体回滚，走到这里说明有异常）；
- §3.4 发现运营在用的入口被误删；
- 任何「说不清现在是什么状态」的时刻。

### 4.2 完整还原（从 CSV）

```bash
psql -v ON_ERROR_STOP=1
```

```sql
BEGIN;

-- 先看一眼当前行数，回滚后要能解释差异
SELECT count(*) AS 还原前_menu FROM sys_menu;
SELECT count(*) AS 还原前_role_menu FROM sys_role_menu;

-- 清空后整表灌回。⚠️ 这会覆盖**执行之后**新增的菜单 ——
--   回滚前务必确认这段时间没有别人新建过菜单（比对 created_time > 备份时间的行）
DELETE FROM sys_role_menu;
DELETE FROM sys_menu;

-- \copy 不展开 ~，必须绝对路径；CSV 里原样保留了 id 列，所以 id 完全还原
\copy sys_menu FROM '<备份目录>/sys_menu_prod_<时间戳>.csv' CSV HEADER
\copy sys_role_menu FROM '<备份目录>/sys_role_menu_prod_<时间戳>.csv' CSV HEADER

-- 序列必须跟着推到 max(id)，否则下次新建菜单会撞主键
SELECT setval('sys_menu_id_seq', (SELECT max(id) FROM sys_menu));
SELECT setval('sys_role_menu_id_seq', (SELECT max(id) FROM sys_role_menu));

-- 确认与备份一致后再提交
SELECT count(*) AS 还原后_menu FROM sys_menu;
SELECT count(*) AS 还原后_role_menu FROM sys_role_menu;

COMMIT;
```

**回滚前的必查项**：确认执行窗口之后没有人新建过菜单，否则整表覆盖会把这些新行一起抹掉：

```sql
SELECT id, path, title, created_time FROM sys_menu
 WHERE created_time > '<备份时间戳>'::timestamptz ORDER BY created_time;
-- 有结果就不要整表覆盖，改用 §4.3 的部分还原
```

### 4.3 部分还原（只回滚被误删的几行）

CSV 里 `id` 列原样保留，从备份里挑出对应行单独 `INSERT` 即可：

```sql
BEGIN;
CREATE TEMP TABLE t_menu_bak (LIKE sys_menu INCLUDING ALL);
\copy t_menu_bak FROM '<备份目录>/sys_menu_prod_<时间戳>.csv' CSV HEADER

-- 只还原指定 path 及其全部后代
WITH RECURSIVE want AS (
    SELECT * FROM t_menu_bak WHERE path IN ('<要恢复的path1>', '<要恢复的path2>')
    UNION ALL
    SELECT b.* FROM t_menu_bak b JOIN want w ON b.parent_id = w.id
)
INSERT INTO sys_menu SELECT * FROM want
    WHERE id NOT IN (SELECT id FROM sys_menu);   -- 已存在的不重复插

SELECT setval('sys_menu_id_seq', (SELECT max(id) FROM sys_menu));
COMMIT;
```

还原完**必须重跑 §3.3 的守卫** —— 恢复回来的菜单如果本来就没页面，守卫会以 `1` 退出提醒你。

### 4.4 兜底：从 `pg_dump` 还原

CSV 路线出问题时（结构变过、CSV 损坏）用 §1.6 的 dump：

```bash
pg_restore -d "postgresql://<PGUSER>@<PGHOST>:<PGPORT>/<PGDATABASE>" \
           --clean --if-exists -t sys_menu -t sys_role_menu \
           <备份目录>/menu_tables_<时间戳>.dump
```

---

## 5. 风险与停止条件

### 5.1 风险清单

| 风险 | 后果 | 缓解 |
|---|---|---|
| 连错库（连到开发/预发） | 在错误环境上删了菜单 | §1.2 每次执行前核对 `current_database()` + `inet_server_addr()` |
| 生产 `path` 有重复或为空 | 按 path 删会误伤或漏删 | §1.5 前置校验，不通过就停手 |
| 生产菜单形状与开发库不同 | 待删清单里混进生产在用的入口 | §1.4 逐行过 CSV，**不要跳过人工复核** |
| 删除后种子被重跑 | 菜单原样长回来，白删一场 | 前置清单里确认种子文件删除已合并进 `hasn`；注意 `hasn_all_menu.sql` 第 8、9 节仍会重建 `/hasn/hasn_group_members` 与 `/hasn/hasn_unread_counts`（该两行本批**刻意保留**，不冲突） |
| `sys_role_menu` 残留悬挂引用 | 角色权限计算出错 | SQL 第 6 步自检 + §3.2 独立复核 |
| 备份不可用 | 无法回滚 | §1.6 强制做还原验证，不是只做备份 |
| 执行窗口内有人在改菜单 | 只读审计的数字和正式执行对不上 | 选低峰窗口；§2 要求两次 NOTICE 数字一致 |

### 5.2 停止条件（命中任意一条 → 立刻停手，不要"先跑跑看"）

1. §1.2 的库身份核对对不上；
2. §1.5 的 `path` 唯一性/非空校验有结果；
3. §1.4 的只读审计跑不起来，或退出码是 `2`（守卫/审计自身故障 = 未验证，不是通过）；
4. §1.6 的备份还原验证有差异，或 CSV 行数与 §1.3 计数对不上；
5. 只读审计给出的待删清单里，有运营明确表示在用的入口，且未经裁决；
6. 只读预演的 NOTICE 数字与正式执行的 NOTICE 数字**不一致**；
7. 种子文件删除**尚未合并进 `hasn`**（删了会长回来，等于白做还多一次误操作风险）；
8. 执行日志里出现任何 `ERROR` / `EXCEPTION`（此时事务已整体回滚，库是干净的 —— 先查因，别重试）；
9. 找不到能在执行后立刻肉眼验侧边栏的人。

### 5.3 不在本 runbook 范围内的事

- **`sys_dict_type` / `sys_dict_data` 的清理**：批次 0 同批删了 28 个孤儿 `*_dict.sql` 种子，
  但**没有**动库里已种下的字典行。要不要清是另一个决定，方案见
  [`2026-08-10-可选-清理孤儿字典数据.sql`](./2026-08-10-可选-清理孤儿字典数据.sql)（**默认不执行**，
  文件里的 `DELETE` 是注释掉的）。
- **业务表数据**：本操作一行业务数据都不碰。
- **前端发版**：删菜单不需要前端发版；前端页面文件的删除是另一批的事。
