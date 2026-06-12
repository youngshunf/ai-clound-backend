-- =====================================================
-- 维度②：per-agent kb 白名单（云端权威；取代 daemon 本地 GrantStore，D5）
-- 无 grant 行 = inherit（Agent 全权继承 owner 全部库，owner 可收紧/封禁）
-- 生成：uv run fba codegen generate --sql-file backend/sql/hasn_knowledge/agent_kb_grant.sql --app hasn_knowledge --schema hasn_knowledge --execute
-- 设计事实源：知识库AI-Native应用重设计（RAGFlow处理后端）.md §2.2 agent_kb_grant + §5.3
-- =====================================================
CREATE SCHEMA IF NOT EXISTS "hasn_knowledge";

CREATE TABLE "hasn_knowledge"."agent_kb_grant" (
  "id"            bigserial      PRIMARY KEY,
  "owner_id"      varchar(40)    NOT NULL,
  "agent_hasn_id" varchar(40)    NOT NULL,
  "mode"          varchar(16)    NOT NULL DEFAULT 'inherit',
  "kb_ids"        jsonb          NOT NULL DEFAULT '[]',
  "created_time"  timestamptz(6) NOT NULL DEFAULT now(),
  "updated_time"  timestamptz(6)
);

CREATE UNIQUE INDEX "uq_agent_kb_grant" ON "hasn_knowledge"."agent_kb_grant" ("owner_id", "agent_hasn_id");

COMMENT ON TABLE  "hasn_knowledge"."agent_kb_grant" IS '分身知识库白名单（维度②，云端权威）';
COMMENT ON COLUMN "hasn_knowledge"."agent_kb_grant"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_knowledge"."agent_kb_grant"."owner_id" IS '归属 owner HASN ID';
COMMENT ON COLUMN "hasn_knowledge"."agent_kb_grant"."agent_hasn_id" IS '分身 HASN ID';
COMMENT ON COLUMN "hasn_knowledge"."agent_kb_grant"."mode" IS '授权模式 (inherit:继承全部:green/restricted:限定范围:orange/denied:禁止访问:red)';
COMMENT ON COLUMN "hasn_knowledge"."agent_kb_grant"."kb_ids" IS 'restricted 白名单 kb_id 数组';
COMMENT ON COLUMN "hasn_knowledge"."agent_kb_grant"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_knowledge"."agent_kb_grant"."updated_time" IS '更新时间';
