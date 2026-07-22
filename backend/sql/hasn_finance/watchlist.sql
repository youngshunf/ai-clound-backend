-- =====================================================
-- 自选股（模块「金融投研与量化交易」，app_id=finance，schema=hasn_finance）
-- 定位：主人**人工维护**的资产，不是分身产物 —— 故**不登记 hasn_artifacts**。
--   写入走独立 watchlist:sync，同样执行 owner/revision/op-id/tombstone 契约，但不调用产物登记。
-- 「非它不可」的理由：跨设备（Mac 上加自选、手机上要看得到）。
-- 不加 platform_project_id：自选股是**全局资产**、跨项目共用（「2026 消费股」和「打新观察」都盯茅台）。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_finance/watchlist.sql --app hasn_finance --schema hasn_finance --execute
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.1
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_finance";

CREATE TABLE "hasn_finance"."watchlist" (
  "id"                bigserial      PRIMARY KEY,
  "owner_id"          varchar(40)    NOT NULL,
  "symbol"            varchar(16)    NOT NULL,
  "market"            varchar(8)     NOT NULL,
  "display_name"      varchar(64),
  "note"              text,
  "sort_order"        int            NOT NULL DEFAULT 0,
  "revision"          bigint         NOT NULL DEFAULT 1,
  "last_client_op_id" varchar(64),
  "status"            varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"      timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"      timestamptz(6) NOT NULL DEFAULT now()
);

-- 同一主人同一市场同一标的只能有一行（跨设备并发加同一只股票时靠它收敛）
CREATE UNIQUE INDEX "uq_finance_watchlist_owner_symbol" ON "hasn_finance"."watchlist" ("owner_id", "market", "symbol");
-- outbox 幂等回放键：同一 op 不得落到两行（partial，允许手工建的行为空且不互斥）
CREATE UNIQUE INDEX "uq_finance_watchlist_owner_op" ON "hasn_finance"."watchlist" ("owner_id", "last_client_op_id") WHERE "last_client_op_id" IS NOT NULL;
CREATE INDEX "idx_finance_watchlist_owner_sort" ON "hasn_finance"."watchlist" ("owner_id", "sort_order");

COMMENT ON TABLE  "hasn_finance"."watchlist" IS '自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）';
COMMENT ON COLUMN "hasn_finance"."watchlist"."id" IS '云端权威 ID（server_id）';
COMMENT ON COLUMN "hasn_finance"."watchlist"."owner_id" IS '归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）';
COMMENT ON COLUMN "hasn_finance"."watchlist"."symbol" IS '标的代码（600519 / 00700 / AAPL）';
COMMENT ON COLUMN "hasn_finance"."watchlist"."market" IS '市场 (cn:A股:red/hk:港股:orange/us:美股:blue)';
COMMENT ON COLUMN "hasn_finance"."watchlist"."display_name" IS '名称快照（贵州茅台）。快照非权威——实时名走行情服务';
COMMENT ON COLUMN "hasn_finance"."watchlist"."note" IS '主人自己的备注';
COMMENT ON COLUMN "hasn_finance"."watchlist"."sort_order" IS '排序序号（主人手工拖拽次序）';
COMMENT ON COLUMN "hasn_finance"."watchlist"."revision" IS '云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测';
COMMENT ON COLUMN "hasn_finance"."watchlist"."last_client_op_id" IS '最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露';
COMMENT ON COLUMN "hasn_finance"."watchlist"."status" IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN "hasn_finance"."watchlist"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_finance"."watchlist"."updated_time" IS '更新时间（每次 revision 变化同步刷新；下行增量游标依赖它，禁止 NULL）';
