-- =====================================================
-- 交易复盘报告（流程 C · ★杀手锏，schema=hasn_finance）
-- 产物表：写入走 trade_review:sync，**同事务登记 hasn_artifacts**。
--   资源 URI = hasn://finance/reviews/{id}（云端权威 ID）。
-- 不加 platform_project_id：报告是纯产物不是容器，挂靠走层1 hasn_artifacts.project_id（05 §4）。
--
-- ★ 两条复合 FK 都保 owner 一致：复盘只能挂到同主人的影子账户/影子回测上。
-- ★ 权威关系就是 shadow_account_id —— extract_shadow_strategy 独立工具产出并持久化 ShadowProfile。
--   旧 analyze_trade_journal(analysis_type="strategy") placeholder 不参与该链路，也不再制造一个错误的
--   strategy 外键；若未来要把影子规则晋升为可执行策略，必须另设显式转换动作。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_finance/trade_review.sql --app hasn_finance --schema hasn_finance --execute
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.0 + §3.1.6
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_finance";

CREATE TABLE "hasn_finance"."trade_review" (
  "id"                  bigserial      PRIMARY KEY,
  "owner_id"            varchar(40)    NOT NULL,
  "agent_hasn_id"       varchar(40),
  "local_ref"           varchar(64),
  "node_id"             varchar(64),
  "shadow_account_id"   bigint         NOT NULL,
  "title"               varchar(256)   NOT NULL,
  "body_md"             text           NOT NULL,
  "findings_json"       jsonb          NOT NULL DEFAULT '{}',
  "shadow_backtest_id"  bigint,
  "pdf_asset_uri"       varchar(128),
  "revision"            bigint         NOT NULL DEFAULT 1,
  "last_client_op_id"   varchar(64),
  "usage_json"          jsonb          NOT NULL DEFAULT '{}',
  "status"              varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6) NOT NULL DEFAULT now(),
  -- ★ owner 一致的复合 FK：复盘只能挂到同主人的账户/回测上
  CONSTRAINT "fk_finance_review_shadow_account" FOREIGN KEY ("owner_id", "shadow_account_id")
    REFERENCES "hasn_finance"."shadow_account" ("owner_id", "id") ON DELETE CASCADE,
  CONSTRAINT "fk_finance_review_shadow_backtest" FOREIGN KEY ("owner_id", "shadow_backtest_id")
    REFERENCES "hasn_finance"."backtest_report" ("owner_id", "id") ON DELETE SET NULL
);

-- 「上次说我追涨，这次改了吗」
CREATE INDEX "idx_finance_review_account_created" ON "hasn_finance"."trade_review" ("shadow_account_id", "created_time" DESC);
CREATE INDEX "idx_finance_review_owner_created" ON "hasn_finance"."trade_review" ("owner_id", "created_time" DESC);
CREATE UNIQUE INDEX "uq_finance_review_owner_local_ref" ON "hasn_finance"."trade_review" ("owner_id", "local_ref") WHERE "local_ref" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_review_owner_op" ON "hasn_finance"."trade_review" ("owner_id", "last_client_op_id") WHERE "last_client_op_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_finance"."trade_review" IS '交易复盘报告（流程 C·杀手锏·产物·同事务登记 hasn_artifacts，05 §3.1.6）';
COMMENT ON COLUMN "hasn_finance"."trade_review"."id" IS '云端权威 ID（server_id）——hasn://finance/reviews/{id} 的 {id} 恒为它';
COMMENT ON COLUMN "hasn_finance"."trade_review"."owner_id" IS '归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）';
COMMENT ON COLUMN "hasn_finance"."trade_review"."agent_hasn_id" IS '产出分身 HASN ID。为空 = 主人手工建';
COMMENT ON COLUMN "hasn_finance"."trade_review"."local_ref" IS '本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI';
COMMENT ON COLUMN "hasn_finance"."trade_review"."node_id" IS '产出设备节点 id（溯源）';
COMMENT ON COLUMN "hasn_finance"."trade_review"."shadow_account_id" IS '所属影子账户 id（权威关系；复合 FK 保证与本行同 owner）';
COMMENT ON COLUMN "hasn_finance"."trade_review"."title" IS '复盘标题';
COMMENT ON COLUMN "hasn_finance"."trade_review"."body_md" IS '复盘正文（markdown）';
COMMENT ON COLUMN "hasn_finance"."trade_review"."findings_json" IS '结构化诊断（可跨期对比）';
COMMENT ON COLUMN "hasn_finance"."trade_review"."shadow_backtest_id" IS '影子回测 id（「你要是一直按自己的策略做，会怎样」；可空；复合 FK 保证同 owner）';
COMMENT ON COLUMN "hasn_finance"."trade_review"."pdf_asset_uri" IS '复盘 PDF 资产引用（hasn://asset/{id}）。主人确认派生同步后，引擎产出的 PDF 才经 daemon AssetGateway 落私有桶；确认前只保留本地路径且不得进 sync payload。序列化边界经 resolve_assets 换签名 URL';
COMMENT ON COLUMN "hasn_finance"."trade_review"."revision" IS '云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测';
COMMENT ON COLUMN "hasn_finance"."trade_review"."last_client_op_id" IS '最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露';
COMMENT ON COLUMN "hasn_finance"."trade_review"."usage_json" IS '本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本';
COMMENT ON COLUMN "hasn_finance"."trade_review"."status" IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN "hasn_finance"."trade_review"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_finance"."trade_review"."updated_time" IS '更新时间（每次 revision 变化同步刷新；下行增量游标依赖它，禁止 NULL）';
