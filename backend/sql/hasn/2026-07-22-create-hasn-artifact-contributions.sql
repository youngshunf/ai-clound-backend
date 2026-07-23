CREATE TABLE "public"."hasn_artifact_contributions" (
    "id"                  BIGSERIAL PRIMARY KEY,
    "contribution_id"     VARCHAR(40) NOT NULL,
    "artifact_id"         VARCHAR(40) NOT NULL,
    "owner_hasn_id"       VARCHAR(40) NOT NULL,
    "agent_hasn_id"       VARCHAR(40) NOT NULL,
    "work_session_id"     VARCHAR(64),
    "project_id"          UUID,
    "action"              VARCHAR(16) NOT NULL,
    "source_kind"         VARCHAR(32) NOT NULL,
    "source_tool"         VARCHAR(128),
    "source_app_id"       VARCHAR(64),
    "dispatch_id"         VARCHAR(64),
    "tool_call_id"        VARCHAR(128),
    "source_event_id"     VARCHAR(128),
    "idempotency_key"     VARCHAR(768) NOT NULL,
    "conversation_id"     UUID,
    "message_id"          BIGINT,
    "occurred_time"       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "metadata"            JSONB NOT NULL DEFAULT '{}',
    "created_time"        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    "updated_time"        TIMESTAMPTZ,
    CONSTRAINT "uq_hasn_artifact_contributions_contribution_id" UNIQUE ("contribution_id"),
    CONSTRAINT "uq_hasn_artifact_contributions_idempotency"
        UNIQUE ("owner_hasn_id", "agent_hasn_id", "idempotency_key"),
    CONSTRAINT "fk_hasn_artifact_contributions_artifact"
        FOREIGN KEY ("artifact_id") REFERENCES "public"."hasn_artifacts" ("artifact_id"),
    CONSTRAINT "ck_hasn_artifact_contributions_action"
        CHECK ("action" IN ('create', 'update')),
    CONSTRAINT "ck_hasn_artifact_contributions_source_kind"
        CHECK ("source_kind" IN ('app_write', 'platform_tool', 'runtime_file', 'agent_note', 'external_import'))
);

COMMENT ON TABLE "public"."hasn_artifact_contributions" IS 'Agent 对产物的不可变参与记录';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."id" IS '数据库主键';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."contribution_id" IS '参与记录公开标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."artifact_id" IS '关联产物当前态公开标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."owner_hasn_id" IS '主人隔离键';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."agent_hasn_id" IS '参与分身标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."work_session_id" IS '本次参与所属工作会话';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."project_id" IS '本次参与所属平台项目';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."action" IS '参与动作 (create:新增:update:修改)';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."source_kind" IS '参与来源 (app_write:应用写入:platform_tool:平台工具:runtime_file:运行时文件:agent_note:分身自撰:external_import:外部导入)';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."source_tool" IS '实际写工具或处理器名称';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."source_app_id" IS '本次操作所在应用上下文';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."dispatch_id" IS '派发关联标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."tool_call_id" IS '工具调用标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."source_event_id" IS '来源事件标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."idempotency_key" IS '来源幂等键';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."conversation_id" IS '来源会话标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."message_id" IS '来源消息标识';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."occurred_time" IS '真实写入或后置核验完成时间';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."metadata" IS '不含正文和本地绝对路径的上下文快照';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."created_time" IS '记录创建时间';
COMMENT ON COLUMN "public"."hasn_artifact_contributions"."updated_time" IS '记录更新时间';

CREATE INDEX "idx_hasn_artifact_contributions_context"
    ON "public"."hasn_artifact_contributions"
    ("owner_hasn_id", "agent_hasn_id", "work_session_id", "project_id", "artifact_id", "occurred_time" DESC);

CREATE INDEX "idx_hasn_artifact_contributions_project"
    ON "public"."hasn_artifact_contributions" ("owner_hasn_id", "project_id", "occurred_time" DESC)
    WHERE "project_id" IS NOT NULL;
