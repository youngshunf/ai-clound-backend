-- =====================================================
-- 回测报告（流程 B，schema=hasn_finance）
-- 产物表：写入走 backtest_report:sync，**同事务登记 hasn_artifacts**。
--   资源 URI = hasn://finance/backtests/{id}（云端权威 ID）。
-- 不加 platform_project_id：报告是纯产物不是容器，挂靠走层1 hasn_artifacts.project_id（05 §4）。
--
-- ★ 复合 FK 保 owner 一致：单列 FK 不够——(owner_id, strategy_id) 必须指向同一 owner 的策略行，
--   否则 A 的回测能挂到 B 的策略上。strategy_id 可空（临时试跑没沉淀成策略）；PG 默认 MATCH SIMPLE
--   在任一列为 NULL 时跳过 FK 检查，正是所需语义。客户端传入的 owner 字段不可信，owner 只取鉴权上下文。
--
-- ★ 五个指标拆真列而非全塞 metrics_json：**要排序/要比较 → 真列；只展示 → JSONB**。
--   列表页排序与「策略 A vs 策略 B」对比要用它们；JSONB 也能排但要表达式索引且类型不受约束。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_finance/backtest_report.sql --app hasn_finance --schema hasn_finance --execute
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.0 + §3.1.4
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_finance";

CREATE TABLE "hasn_finance"."backtest_report" (
  "id"                 bigserial      PRIMARY KEY,
  "owner_id"           varchar(40)    NOT NULL,
  "agent_hasn_id"      varchar(40),
  "local_ref"          varchar(64),
  "node_id"            varchar(64),
  "strategy_id"        bigint,
  "title"              varchar(256)   NOT NULL,
  "period_start"       date           NOT NULL,
  "period_end"         date           NOT NULL,
  "universe_json"      jsonb          NOT NULL DEFAULT '[]',
  "initial_capital"    numeric(18,2),
  "cost_model_json"    jsonb          NOT NULL DEFAULT '{}',
  "benchmark_symbol"   varchar(16),
  "benchmark_return"   numeric(10,4),
  "annual_return"      numeric(10,4),
  "sharpe"             numeric(10,4),
  "max_drawdown"       numeric(10,4),
  "win_rate"           numeric(10,4),
  "trade_count"        int,
  "metrics_json"       jsonb          NOT NULL DEFAULT '{}',
  "equity_curve_json"  jsonb          NOT NULL DEFAULT '[]',
  "trades_json"        jsonb          NOT NULL DEFAULT '[]',
  "engine_version"     varchar(32)    NOT NULL,
  "data_source"        varchar(32)    NOT NULL,
  "revision"           bigint         NOT NULL DEFAULT 1,
  "last_client_op_id"  varchar(64),
  "usage_json"         jsonb          NOT NULL DEFAULT '{}',
  "status"             varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"       timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"       timestamptz(6) NOT NULL DEFAULT now(),
  -- ★ owner 一致的复合 FK：回测只能挂到同主人的策略上
  CONSTRAINT "fk_finance_backtest_strategy" FOREIGN KEY ("owner_id", "strategy_id")
    REFERENCES "hasn_finance"."strategy" ("owner_id", "id") ON DELETE SET NULL,
  -- ★ 供 strategy.latest_backtest_id / trade_review.shadow_backtest_id 复合 FK 引用
  CONSTRAINT "uq_finance_backtest_owner_id" UNIQUE ("owner_id", "id")
);

CREATE INDEX "idx_finance_backtest_owner_created" ON "hasn_finance"."backtest_report" ("owner_id", "created_time" DESC);
CREATE INDEX "idx_finance_backtest_strategy_created" ON "hasn_finance"."backtest_report" ("strategy_id", "created_time" DESC);
-- 「哪个策略夏普最高」
CREATE INDEX "idx_finance_backtest_owner_sharpe" ON "hasn_finance"."backtest_report" ("owner_id", "sharpe" DESC NULLS LAST);
CREATE UNIQUE INDEX "uq_finance_backtest_owner_local_ref" ON "hasn_finance"."backtest_report" ("owner_id", "local_ref") WHERE "local_ref" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_backtest_owner_op" ON "hasn_finance"."backtest_report" ("owner_id", "last_client_op_id") WHERE "last_client_op_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_finance"."backtest_report" IS '回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."id" IS '云端权威 ID（server_id）——hasn://finance/backtests/{id} 的 {id} 恒为它';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."owner_id" IS '归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."agent_hasn_id" IS '产出分身 HASN ID。为空 = 主人手工建';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."local_ref" IS '本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."node_id" IS '产出设备节点 id（溯源）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."strategy_id" IS '所属策略 id（可空=临时试跑没沉淀成策略；复合 FK 保证与本行同 owner）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."title" IS '报告标题';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."period_start" IS '样本区间起（诚实性红线的数据层强制，必填）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."period_end" IS '样本区间止（诚实性红线的数据层强制，必填）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."universe_json" IS '回测标的池';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."initial_capital" IS '初始资金';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."cost_model_json" IS '手续费/滑点/印花税假设（诚实性红线：不含成本的回测是骗人的——零成本假设下高频策略遍地圣杯，加万三手续费全军覆没。UI 必须与收益指标并列展示，不许折叠）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."benchmark_symbol" IS '基准标的（沪深300…）。诚实性红线：没有基准的年化毫无意义——策略 20% 而同期沪深300 涨 25% 即跑输大盘';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."benchmark_return" IS '同期基准收益';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."annual_return" IS '年化收益（拆真列：要排序/对比）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."sharpe" IS '夏普比率（拆真列：要排序/对比）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."max_drawdown" IS '最大回撤（拆真列：要排序/对比）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."win_rate" IS '胜率（拆真列：要排序/对比）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."trade_count" IS '成交笔数（拆真列：要排序/对比）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."metrics_json" IS '其余指标（索提诺/卡玛/月度分布…），不排序只展示';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."equity_curve_json" IS '净值曲线';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."trades_json" IS '成交明细（P1 先 JSONB：详情页只展示不查询；实测单条 > 1MB 再改落 asset，先量再决定）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."engine_version" IS '引擎版本（可复现性，必填）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."data_source" IS '本次实际出数的数据源（必填：A股回退链有多源，不记下来则两次结果不同时无法判断是策略变化还是数据源变化）';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."revision" IS '云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."last_client_op_id" IS '最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."usage_json" IS '本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."status" IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_finance"."backtest_report"."updated_time" IS '更新时间（每次 revision 变化同步刷新；下行增量游标依赖它，禁止 NULL）';
