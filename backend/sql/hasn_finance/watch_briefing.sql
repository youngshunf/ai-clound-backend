-- =====================================================
-- 盯盘简报（流程 D，schema=hasn_finance）
-- ⚠ schema 随 7 表一次性建立，但流程 D 的调度/通知/工具/页面均**不属于第一版上线门**（05 §3.1.7）。
-- 产物表：写入走 watch_briefing:sync，**同事务登记 hasn_artifacts**。
--   资源 URI = hasn://finance/briefings/{id}（云端权威 ID）。
-- 不加 platform_project_id：纯产物，挂靠走层1 hasn_artifacts.project_id（05 §4）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_finance/watch_briefing.sql --app hasn_finance --schema hasn_finance --execute
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.0 + §3.1.7
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_finance";

CREATE TABLE "hasn_finance"."watch_briefing" (
  "id"                    bigserial      PRIMARY KEY,
  "owner_id"              varchar(40)    NOT NULL,
  "agent_hasn_id"         varchar(40),
  "local_ref"             varchar(64),
  "node_id"               varchar(64),
  "briefing_date"         date           NOT NULL,
  "title"                 varchar(256)   NOT NULL,
  "body_md"               text           NOT NULL,
  "covered_symbols_json"  jsonb          NOT NULL DEFAULT '[]',
  "trigger"               varchar(16)    NOT NULL,
  "revision"              bigint         NOT NULL DEFAULT 1,
  "last_client_op_id"     varchar(64),
  "usage_json"            jsonb          NOT NULL DEFAULT '{}',
  "status"                varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"          timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"          timestamptz(6) NOT NULL DEFAULT now()
);

-- ★ 只约束定时简报：一天一份、重跑覆盖不新增。
--   手动简报**不受限**——主人盘中问三次就该有三份；旧写法 UNIQUE(owner_id, briefing_date, trigger)
--   会把第二次手动简报撞唯一约束吞掉。
CREATE UNIQUE INDEX "uq_finance_briefing_owner_date_scheduled" ON "hasn_finance"."watch_briefing" ("owner_id", "briefing_date") WHERE "trigger" = 'scheduled';
CREATE INDEX "idx_finance_briefing_owner_date" ON "hasn_finance"."watch_briefing" ("owner_id", "briefing_date" DESC);
CREATE UNIQUE INDEX "uq_finance_briefing_owner_local_ref" ON "hasn_finance"."watch_briefing" ("owner_id", "local_ref") WHERE "local_ref" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_briefing_owner_op" ON "hasn_finance"."watch_briefing" ("owner_id", "last_client_op_id") WHERE "last_client_op_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_finance"."watch_briefing" IS '盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."id" IS '云端权威 ID（server_id）——hasn://finance/briefings/{id} 的 {id} 恒为它';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."owner_id" IS '归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."agent_hasn_id" IS '产出分身 HASN ID。为空 = 主人手工建';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."local_ref" IS '本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."node_id" IS '产出设备节点 id（溯源）';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."briefing_date" IS '简报日期';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."title" IS '简报标题';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."body_md" IS '简报正文（markdown）';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."covered_symbols_json" IS '覆盖了哪些标的（按标的反查简报）';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."trigger" IS '触发 (scheduled:定时:blue/manual:手动:default)';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."revision" IS '云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."last_client_op_id" IS '最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."usage_json" IS '本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."status" IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_finance"."watch_briefing"."updated_time" IS '更新时间（每次 revision 变化同步刷新；下行增量游标依赖它，禁止 NULL）';
