-- =====================================================
-- 投研报告（流程 A · 最高频产物，schema=hasn_finance）
-- 产物表：写入走 research_report:sync，**同事务登记 hasn_artifacts**（register-on-write 铁律）。
--   资源 URI = hasn://finance/reports/{id}，{id} 恒为本表 id（云端权威 ID）——守「本地 ID 永不上 URI」铁律。
-- 不加 platform_project_id：报告是**纯产物**不是容器，项目挂靠走层1 hasn_artifacts.project_id（05 §4）。
-- 不 FK 到 watchlist：分析的标的未必在自选股里。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_finance/research_report.sql --app hasn_finance --schema hasn_finance --execute
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.0 + §3.1.2
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_finance";

CREATE TABLE "hasn_finance"."research_report" (
  "id"                bigserial      PRIMARY KEY,
  "owner_id"          varchar(40)    NOT NULL,
  "agent_hasn_id"     varchar(40),
  "local_ref"         varchar(64),
  "node_id"           varchar(64),
  "symbol"            varchar(16)    NOT NULL,
  "market"            varchar(8)     NOT NULL,
  "display_name"      varchar(64),
  "title"             varchar(256)   NOT NULL,
  "verdict"           varchar(16)    NOT NULL,
  "conviction"        smallint,
  "summary"           text,
  "body_md"           text           NOT NULL,
  "findings_json"     jsonb          NOT NULL DEFAULT '{}',
  "data_as_of"        date           NOT NULL,
  "swarm_preset"      varchar(64),
  "swarm_run_ref"     varchar(64),
  "engine_version"    varchar(32),
  "bound_agent_id"    varchar(40),
  "revision"          bigint         NOT NULL DEFAULT 1,
  "last_client_op_id" varchar(64),
  "usage_json"        jsonb          NOT NULL DEFAULT '{}',
  "status"            varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"      timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"      timestamptz(6) NOT NULL DEFAULT now()
);

-- 核心查询：「这只股我之前分析过什么、当时什么结论」
CREATE INDEX "idx_finance_report_owner_symbol" ON "hasn_finance"."research_report" ("owner_id", "market", "symbol", "created_time" DESC);
CREATE INDEX "idx_finance_report_owner_created" ON "hasn_finance"."research_report" ("owner_id", "created_time" DESC);
-- 本地幂等键：create 响应丢失后重推必须回到同一云端 id，不制造重复行
CREATE UNIQUE INDEX "uq_finance_report_owner_local_ref" ON "hasn_finance"."research_report" ("owner_id", "local_ref") WHERE "local_ref" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_report_owner_op" ON "hasn_finance"."research_report" ("owner_id", "last_client_op_id") WHERE "last_client_op_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_finance"."research_report" IS '投研报告（流程 A·产物·同事务登记 hasn_artifacts，05 §3.1.2）';
COMMENT ON COLUMN "hasn_finance"."research_report"."id" IS '云端权威 ID（server_id）——hasn://finance/reports/{id} 的 {id} 恒为它';
COMMENT ON COLUMN "hasn_finance"."research_report"."owner_id" IS '归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）';
COMMENT ON COLUMN "hasn_finance"."research_report"."agent_hasn_id" IS '产出分身 HASN ID。为空 = 主人手工建（本模块罕见）';
COMMENT ON COLUMN "hasn_finance"."research_report"."local_ref" IS '本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI';
COMMENT ON COLUMN "hasn_finance"."research_report"."node_id" IS '产出设备节点 id（溯源：这份报告是哪台机器跑的）';
COMMENT ON COLUMN "hasn_finance"."research_report"."symbol" IS '标的代码（查询键①）';
COMMENT ON COLUMN "hasn_finance"."research_report"."market" IS '市场 (cn:A股:red/hk:港股:orange/us:美股:blue)';
COMMENT ON COLUMN "hasn_finance"."research_report"."display_name" IS '名称快照（非权威，实时名走行情服务）';
COMMENT ON COLUMN "hasn_finance"."research_report"."title" IS '报告标题';
COMMENT ON COLUMN "hasn_finance"."research_report"."verdict" IS '结论 (bullish:看多:red/bearish:看空:green/neutral:中性:default)';
COMMENT ON COLUMN "hasn_finance"."research_report"."conviction" IS '信心 1–5。允许为空 = 分身没给，不许默认 3 假装有';
COMMENT ON COLUMN "hasn_finance"."research_report"."summary" IS '一句话结论（列表页展示，免读全文）';
COMMENT ON COLUMN "hasn_finance"."research_report"."body_md" IS '报告正文（markdown）';
COMMENT ON COLUMN "hasn_finance"."research_report"."findings_json" IS '结构化要点（估值/风险/催化剂），列表页筛选用';
COMMENT ON COLUMN "hasn_finance"."research_report"."data_as_of" IS '数据截止时点（诚实性红线的数据层强制：不记它主人就无法判断报告是否新鲜；UI 必须常驻展示，不许折叠进详情）';
COMMENT ON COLUMN "hasn_finance"."research_report"."swarm_preset" IS '用的哪套专家团队预设';
COMMENT ON COLUMN "hasn_finance"."research_report"."swarm_run_ref" IS '本地 run_id（仅溯源，同 local_ref 规约：不进 URI、不据它打开）';
COMMENT ON COLUMN "hasn_finance"."research_report"."engine_version" IS '引擎版本（可复现性）';
COMMENT ON COLUMN "hasn_finance"."research_report"."bound_agent_id" IS '协作分身 HASN ID（详情页「找它改」，对齐 doc21 AppCollab）';
COMMENT ON COLUMN "hasn_finance"."research_report"."revision" IS '云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测';
COMMENT ON COLUMN "hasn_finance"."research_report"."last_client_op_id" IS '最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露';
COMMENT ON COLUMN "hasn_finance"."research_report"."usage_json" IS '本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本';
COMMENT ON COLUMN "hasn_finance"."research_report"."status" IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN "hasn_finance"."research_report"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_finance"."research_report"."updated_time" IS '更新时间（每次 revision 变化同步刷新；下行增量游标依赖它，禁止 NULL）';
