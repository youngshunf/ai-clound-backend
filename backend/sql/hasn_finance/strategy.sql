-- =====================================================
-- 策略（流程 B · 长生命周期容器，schema=hasn_finance）
-- 产物表：写入走 strategy:sync，**同事务登记 hasn_artifacts**（register-on-write 铁律）。
--   资源 URI = hasn://finance/strategies/{id}（云端权威 ID）。
-- 容器表：带 platform_project_id（doc38 层2 容器级挂靠）——建→回测→迭代跨数周，
--   项目总览要能看到「这个项目下沉淀了哪些策略」。
--
-- ★ latest_backtest_id 的 FK 后置补：它与 backtest_report.strategy_id 互为外键（循环依赖），
--   PG 建表阶段无法同时声明。本表先只留列，FK 由 migrations/2026-07-17-finance-circular-fk.sql
--   在两表都存在后 ALTER 补上（复合 FK → backtest_report(owner_id, id)，保证 owner 一致）。
--
-- ★ 策略分享 P1 不开：code_py 是可执行 Python，分享 = 让接收方执行别人写的代码。
--   必须由服务端按 finance.strategy 拒绝，不能只隐藏 UI。重开条件见 06-决策记录.md Q6。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_finance/strategy.sql --app hasn_finance --schema hasn_finance --execute
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.0 + §3.1.3 + §4
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_finance";

CREATE TABLE "hasn_finance"."strategy" (
  "id"                  bigserial      PRIMARY KEY,
  "owner_id"            varchar(40)    NOT NULL,
  "agent_hasn_id"       varchar(40),
  "local_ref"           varchar(64),
  "node_id"             varchar(64),
  "name"                varchar(128)   NOT NULL,
  "description"         text,
  "market"              varchar(8)     NOT NULL,
  "universe_json"       jsonb          NOT NULL DEFAULT '[]',
  "params_json"         jsonb          NOT NULL DEFAULT '{}',
  "code_py"             text,
  "code_sha256"         varchar(64),
  "source"              varchar(16)    NOT NULL,
  "bound_agent_id"      varchar(40),
  "latest_backtest_id"  bigint,
  "platform_project_id" uuid           REFERENCES "hasn_project"."hasn_project"("id") ON DELETE SET NULL,
  "revision"            bigint         NOT NULL DEFAULT 1,
  "last_client_op_id"   varchar(64),
  "usage_json"          jsonb          NOT NULL DEFAULT '{}',
  "status"              varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6) NOT NULL DEFAULT now(),
  -- ★ 供子表复合 FK 引用：保证 backtest_report.strategy_id 指向的策略与自己同 owner
  CONSTRAINT "uq_finance_strategy_owner_id" UNIQUE ("owner_id", "id")
);

CREATE INDEX "idx_finance_strategy_owner_created" ON "hasn_finance"."strategy" ("owner_id", "created_time" DESC);
-- owner 隔离后的项目反查（项目总览「沉淀了哪些策略」）
CREATE INDEX "idx_finance_strategy_owner_project" ON "hasn_finance"."strategy" ("owner_id", "platform_project_id") WHERE "platform_project_id" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_strategy_owner_local_ref" ON "hasn_finance"."strategy" ("owner_id", "local_ref") WHERE "local_ref" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_strategy_owner_op" ON "hasn_finance"."strategy" ("owner_id", "last_client_op_id") WHERE "last_client_op_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_finance"."strategy" IS '策略（流程 B·产物+容器·可挂平台项目，05 §3.1.3）';
COMMENT ON COLUMN "hasn_finance"."strategy"."id" IS '云端权威 ID（server_id）——hasn://finance/strategies/{id} 的 {id} 恒为它';
COMMENT ON COLUMN "hasn_finance"."strategy"."owner_id" IS '归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）';
COMMENT ON COLUMN "hasn_finance"."strategy"."agent_hasn_id" IS '产出分身 HASN ID。为空 = 主人手工建';
COMMENT ON COLUMN "hasn_finance"."strategy"."local_ref" IS '本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI';
COMMENT ON COLUMN "hasn_finance"."strategy"."node_id" IS '产出设备节点 id（溯源）';
COMMENT ON COLUMN "hasn_finance"."strategy"."name" IS '策略名';
COMMENT ON COLUMN "hasn_finance"."strategy"."description" IS '策略说明';
COMMENT ON COLUMN "hasn_finance"."strategy"."market" IS '市场 (cn:A股:red/hk:港股:orange/us:美股:blue)';
COMMENT ON COLUMN "hasn_finance"."strategy"."universe_json" IS '适用标的池';
COMMENT ON COLUMN "hasn_finance"."strategy"."params_json" IS '可调参数（均线周期等）';
COMMENT ON COLUMN "hasn_finance"."strategy"."code_py" IS '策略源码（引擎产出的 code/signal_engine.py）——策略本体。P1 禁止分享：服务端按 finance.strategy 硬拒';
COMMENT ON COLUMN "hasn_finance"."strategy"."code_sha256" IS '源码指纹（改没改过、回测对不对得上）';
COMMENT ON COLUMN "hasn_finance"."strategy"."source" IS '来源 (swarm:专家团队生成:blue/manual:手动创建:default/default:内置示例:gray)';
COMMENT ON COLUMN "hasn_finance"."strategy"."bound_agent_id" IS '协作分身 HASN ID（对齐 doc21 AppCollab）';
COMMENT ON COLUMN "hasn_finance"."strategy"."latest_backtest_id" IS '最新回测 id（冗余缓存，列表页显示最新夏普免 N+1）。权威在 backtest_report，不一致时以后者为准。FK 后置补（循环依赖）';
COMMENT ON COLUMN "hasn_finance"."strategy"."platform_project_id" IS '挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）';
COMMENT ON COLUMN "hasn_finance"."strategy"."revision" IS '云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测';
COMMENT ON COLUMN "hasn_finance"."strategy"."last_client_op_id" IS '最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露';
COMMENT ON COLUMN "hasn_finance"."strategy"."usage_json" IS '本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本';
COMMENT ON COLUMN "hasn_finance"."strategy"."status" IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN "hasn_finance"."strategy"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_finance"."strategy"."updated_time" IS '更新时间（每次 revision 变化同步刷新；下行增量游标依赖它，禁止 NULL）';
