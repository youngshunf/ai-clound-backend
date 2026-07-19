-- =====================================================
-- 影子账户（流程 C · 隐私最敏感 · 复盘的长期容器，schema=hasn_finance）
-- 产物表：写入走 shadow_account:sync，**同事务登记 hasn_artifacts**。
--   资源 URI = hasn://finance/shadow/{id}（云端权威 ID）。
-- 容器表：带 platform_project_id（doc38 层2）——季季对比的连续体，可整体挂进「我的交易改进」类项目。
--
-- ★★ 隐私红线（评审必查，这是本模块隐私叙事的地基）：
--   · 本表**没有** source_file_ref —— 本地绝对路径只存 SQLite，且受 path_guard.rs::resolve_within_data_dir 约束。
--   · account_alias 存**主人给的别名**（「我的打新账户」），**绝不存真实账号**。
--   · 原始对账单/流水/真实账号/本地绝对路径**永不上云**。
--   · 画像/诊断本身也是高度敏感数据；只有主人确认同步清单后才允许 outbox 上推（05 §2）。
--
-- ★ 自引用复合 FK（superseded_by）同样保 owner 一致：版本链不能跨主人。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_finance/shadow_account.sql --app hasn_finance --schema hasn_finance --execute
-- 设计事实源：docs/hasn-node设计文档/金融投研与量化交易/05-数据与同步契约.md §3.1.0 + §3.1.5 + §4
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_finance";

CREATE TABLE "hasn_finance"."shadow_account" (
  "id"                  bigserial      PRIMARY KEY,
  "owner_id"            varchar(40)    NOT NULL,
  "agent_hasn_id"       varchar(40),
  "local_ref"           varchar(64),
  "node_id"             varchar(64),
  "broker"              varchar(32),
  "account_alias"       varchar(64),
  "stmt_period_start"   date,
  "stmt_period_end"     date,
  "profile_json"        jsonb          NOT NULL DEFAULT '{}',
  "behaviors_json"      jsonb          NOT NULL DEFAULT '{}',
  "source_file_name"    varchar(256),
  "source_hash"         varchar(64),
  "source_asset_uri"    varchar(512),
  "source_synced_at"    timestamptz(6),
  "version"             int            NOT NULL DEFAULT 1,
  "superseded_by"       bigint,
  "platform_project_id" uuid           REFERENCES "hasn_project"."hasn_project"("id") ON DELETE SET NULL,
  "revision"            bigint         NOT NULL DEFAULT 1,
  "last_client_op_id"   varchar(64),
  "usage_json"          jsonb          NOT NULL DEFAULT '{}',
  "status"              varchar(16)    NOT NULL DEFAULT 'active',
  "created_time"        timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"        timestamptz(6) NOT NULL DEFAULT now(),
  -- ★ 供 trade_review 复合 FK 引用 + 自引用版本链
  CONSTRAINT "uq_finance_shadow_owner_id" UNIQUE ("owner_id", "id"),
  CONSTRAINT "fk_finance_shadow_superseded" FOREIGN KEY ("owner_id", "superseded_by")
    REFERENCES "hasn_finance"."shadow_account" ("owner_id", "id") ON DELETE SET NULL,
  -- ★ 分享快照三列必须全空或全非空，不允许半状态（doc39 §3.2）；P1 三者恒全空
  CONSTRAINT "ck_finance_shadow_snapshot_all_or_none" CHECK (
    ("source_hash" IS NULL AND "source_asset_uri" IS NULL AND "source_synced_at" IS NULL)
    OR ("source_hash" IS NOT NULL AND "source_asset_uri" IS NOT NULL AND "source_synced_at" IS NOT NULL)
  )
);

CREATE INDEX "idx_finance_shadow_owner_created" ON "hasn_finance"."shadow_account" ("owner_id", "created_time" DESC);
CREATE INDEX "idx_finance_shadow_owner_project" ON "hasn_finance"."shadow_account" ("owner_id", "platform_project_id") WHERE "platform_project_id" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_shadow_owner_local_ref" ON "hasn_finance"."shadow_account" ("owner_id", "local_ref") WHERE "local_ref" IS NOT NULL;
CREATE UNIQUE INDEX "uq_finance_shadow_owner_op" ON "hasn_finance"."shadow_account" ("owner_id", "last_client_op_id") WHERE "last_client_op_id" IS NOT NULL;

COMMENT ON TABLE  "hasn_finance"."shadow_account" IS '影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."id" IS '云端权威 ID（server_id）——hasn://finance/shadow/{id} 的 {id} 恒为它';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."owner_id" IS '归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."agent_hasn_id" IS '产出分身 HASN ID。为空 = 主人手工建';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."local_ref" IS '本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."node_id" IS '产出设备节点 id（溯源）';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."broker" IS '券商';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."account_alias" IS '主人给的别名（「我的打新账户」）。★隐私红线：绝不存真实账号';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."stmt_period_start" IS '对账单覆盖区间起';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."stmt_period_end" IS '对账单覆盖区间止';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."profile_json" IS '交易画像（持仓周期/交易频率/胜率/盈亏比/偏好标的）。★高度敏感：仅主人确认同步清单后才上推';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."behaviors_json" IS '行为诊断（处置效应/过度交易/追涨/锚定）。★高度敏感：同上';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."source_file_name" IS '脱敏显示名；basename 后仍须清除账号/用户名，无法可靠脱敏就置 NULL——不是原始文件名备份';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."source_hash" IS '已上传分享快照的 sha256，必须与 source_asset_uri 对应；P1 恒为 NULL';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."source_asset_uri" IS '未来显式分享原件后才有 hasn://asset/{id}；P1 恒为 NULL';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."source_synced_at" IS '原件分享快照上传时刻；P1 恒为 NULL';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."version" IS '版本号：这季度 vs 上季度';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."superseded_by" IS '被哪个新版本取代（自引用；复合 FK 保证版本链不跨主人）';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."platform_project_id" IS '挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."revision" IS '云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."last_client_op_id" IS '最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."usage_json" IS '本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."status" IS '状态 (active:正常:green/deleted:已删:red)';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_finance"."shadow_account"."updated_time" IS '更新时间（每次 revision 变化同步刷新；下行增量游标依赖它，禁止 NULL）';
