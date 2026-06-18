-- =====================================================
-- 设计系统生成应用（app_id=designsystem）：revision 版本表
-- 每次 save / 协作改动出一版，可回滚；关键文件内联入行供 picker/校验/离线直读。
-- 完整 bundle（多文件 zip）存私有桶，bundle_asset_id 存 hasn://asset 引用。
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_designsystem/revision.sql --app hasn_designsystem --schema hasn_designsystem --execute
-- 设计事实源：设计 §5.3
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_designsystem";

CREATE TABLE "hasn_designsystem"."revision" (
  "id"                         bigserial      PRIMARY KEY,
  "design_system_id"           bigint         NOT NULL,
  "rev_no"                     int            NOT NULL,
  "author_kind"                varchar(8)     NOT NULL DEFAULT 'human',
  "author_id"                  varchar(64)    NOT NULL DEFAULT '',
  "bundle_asset_id"            varchar(128),
  "tokens_css"                 text,
  "design_tokens_json"         jsonb,
  "tailwind_css"               text,
  "design_md"                  text,
  "components_html"            text,
  "components_manifest_json"   jsonb,
  "token_contract_report_json" jsonb,
  "note"                       varchar(512),
  "created_time"               timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"               timestamptz(6)
);

CREATE UNIQUE INDEX "uq_ds_revision_no" ON "hasn_designsystem"."revision" ("design_system_id", "rev_no");
CREATE INDEX "idx_ds_revision_ds" ON "hasn_designsystem"."revision" ("design_system_id");

COMMENT ON TABLE  "hasn_designsystem"."revision" IS '设计系统版本（每次 save/协作出一版，可回滚）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."id" IS '主键 ID（自增 BigInt）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."design_system_id" IS '所属 design_system.id';
COMMENT ON COLUMN "hasn_designsystem"."revision"."rev_no" IS '版本号（design_system 内单调递增，从 1 起）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."author_kind" IS '作者类型 (human:人:blue/agent:分身:violet)';
COMMENT ON COLUMN "hasn_designsystem"."revision"."author_id" IS '作者 HASN ID（人或分身）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."bundle_asset_id" IS '完整 bundle zip 资产引用（hasn://asset/{id}，可空）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."tokens_css" IS '真源 tokens.css（四层 token 契约）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."design_tokens_json" IS '派生 design-tokens.json（含分层/血缘/评分）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."tailwind_css" IS '派生 tailwind-v4.css（@theme 映射）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."design_md" IS '设计说明 DESIGN.md（创意部分，分身产出）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."components_html" IS '组件样例 components.html';
COMMENT ON COLUMN "hasn_designsystem"."revision"."components_manifest_json" IS '组件清单 components.manifest.json';
COMMENT ON COLUMN "hasn_designsystem"."revision"."token_contract_report_json" IS 'token 契约评分 + 血缘报告';
COMMENT ON COLUMN "hasn_designsystem"."revision"."note" IS '版本备注（如"主色调暖一点"，可空）';
COMMENT ON COLUMN "hasn_designsystem"."revision"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_designsystem"."revision"."updated_time" IS '更新时间';
