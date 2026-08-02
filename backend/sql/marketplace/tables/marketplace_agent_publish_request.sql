CREATE TABLE "hasn_marketplace"."marketplace_agent_publish_request" (
  "id" bigserial PRIMARY KEY,
  "agent_hasn_id" varchar(40) NOT NULL,
  "owner_hasn_id" varchar(40) NOT NULL,
  "resource_kind" varchar(20) NOT NULL,
  "idempotency_key" varchar(128) NOT NULL,
  "asset_uri" varchar(255) NOT NULL,
  "content_hash" varchar(128) NOT NULL,
  "file_hash" varchar(64) NOT NULL,
  "resource_id" varchar(255),
  "version" varchar(50),
  "state" varchar(24) NOT NULL DEFAULT 'processing',
  "result" jsonb,
  "work_session_id" varchar(64),
  "created_time" timestamptz(6) NOT NULL DEFAULT NOW(),
  "updated_time" timestamptz(6),
  CONSTRAINT "uq_marketplace_agent_publish_request_identity"
    UNIQUE ("agent_hasn_id", "resource_kind", "idempotency_key")
);

CREATE INDEX "idx_marketplace_agent_publish_request_owner"
  ON "hasn_marketplace"."marketplace_agent_publish_request" ("owner_hasn_id", "created_time");

COMMENT ON TABLE "hasn_marketplace"."marketplace_agent_publish_request" IS 'Agent 市场发布幂等请求';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."id" IS '主键 ID';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."agent_hasn_id" IS '发起发布的 Agent HASN ID';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."owner_hasn_id" IS '资源所属主人 HASN ID';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."resource_kind" IS '资源类型 (skill:技能:blue/template:模板:green/skill_pack:技能包:cyan)';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."idempotency_key" IS '调用方生成的服务端去重键';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."asset_uri" IS '经 Owner ACL 验证的 hasn://asset/{id}';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."content_hash" IS '服务端解包后计算的规范化内容指纹，仅用于冲突检测';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."file_hash" IS '服务端读取资产字节后计算的 SHA256';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."resource_id" IS '首次提交创建或更新的权威资源 ID';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."version" IS '首次提交解析出的资源版本';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."state" IS '请求状态 (processing:处理中:orange/committed:已提交:green/partial:部分成功:yellow/failed:失败:red)';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."result" IS '首次已提交结果，重复请求原样回放';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."work_session_id" IS 'daemon 可信注入的工作会话 ID';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."created_time" IS '创建时间';
COMMENT ON COLUMN "hasn_marketplace"."marketplace_agent_publish_request"."updated_time" IS '更新时间';
