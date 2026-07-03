-- =====================================================
-- 平台运维授予源（Admin-only·G1 平台特权门唯一授予源）
-- 特权 scope（前缀 diag:/ops:/platform: 整段排他）仅经本表授予；
-- owner 不可自授（PUT /scopes 三态非放行依据）；撤销=删行、即时生效
-- （消费时活取 + 短 TTL 缓存失效，同 get_agent_scopes_cached 模式）。
-- 设计事实源：docs/hasn-node设计文档/MCP统一工具体系/18-统一工具暴露机制设计.md §4.1/§6.1
-- =====================================================
CREATE TABLE IF NOT EXISTS "public"."hasn_platform_operator_grants" (
  "id"            bigserial      PRIMARY KEY,
  "agent_hasn_id" varchar(64)    NOT NULL,
  "scope"         varchar(64)    NOT NULL,
  "granted_by"    varchar(64)    NOT NULL,
  "note"          varchar(256),
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6),
  CONSTRAINT "uq_platform_operator_grant" UNIQUE ("agent_hasn_id", "scope")
);

COMMENT ON TABLE "public"."hasn_platform_operator_grants" IS '平台运维授予源（Admin-only·G1 特权门）';
COMMENT ON COLUMN "public"."hasn_platform_operator_grants"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."hasn_platform_operator_grants"."agent_hasn_id" IS '被授予的分身 hasn_id';
COMMENT ON COLUMN "public"."hasn_platform_operator_grants"."scope" IS '特权 scope（精确值 diag:read:all / diag:manage 或段尾通配 ops:*，* 仅限末段）';
COMMENT ON COLUMN "public"."hasn_platform_operator_grants"."granted_by" IS '操作的 Admin（审计）';
COMMENT ON COLUMN "public"."hasn_platform_operator_grants"."note" IS '备注（授予理由，可空）';
COMMENT ON COLUMN "public"."hasn_platform_operator_grants"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."hasn_platform_operator_grants"."updated_time" IS '更新时间';
