-- 唤星用户 ↔ new-api 用户映射表
-- 记录唤星 sys_user 与 new-api users/tokens 的对应关系
-- new-api 自身的表（users, tokens, logs, channels）由 GORM AutoMigrate 创建，不在此管理

CREATE TABLE "public"."llm_newapi_user_mapping" (
    "id"                bigserial PRIMARY KEY,
    "huanxing_user_id"  bigint NOT NULL,
    "newapi_user_id"    bigint NOT NULL,
    "newapi_token_key"  varchar(128) NOT NULL,
    "newapi_token_id"   bigint NOT NULL,
    "app_code"          varchar(32) NOT NULL DEFAULT 'huanxing',
    "status"            varchar(16) NOT NULL DEFAULT 'active',
    "created_time"      timestamptz(6) NOT NULL DEFAULT NOW(),
    "updated_time"      timestamptz(6),
    CONSTRAINT "uq_newapi_mapping_user_app" UNIQUE ("huanxing_user_id", "app_code")
);

-- 索引
CREATE INDEX "idx_newapi_mapping_huanxing_user" ON "public"."llm_newapi_user_mapping" ("huanxing_user_id");
CREATE INDEX "idx_newapi_mapping_newapi_user" ON "public"."llm_newapi_user_mapping" ("newapi_user_id");
CREATE INDEX "idx_newapi_mapping_status" ON "public"."llm_newapi_user_mapping" ("status");

-- 表注释
COMMENT ON TABLE "public"."llm_newapi_user_mapping" IS '唤星用户与 new-api 用户映射表';

-- 字段注释
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."id" IS '主键 ID';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."huanxing_user_id" IS '唤星 sys_user.id';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."newapi_user_id" IS 'new-api users.id';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."newapi_token_key" IS 'new-api tokens.key（用户默认 API Key）';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."newapi_token_id" IS 'new-api tokens.id';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."app_code" IS '应用标识 (huanxing:唤星/zhixiaoya:知小鸦)';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."status" IS '状态 (active:启用:green/disabled:禁用:red)';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."created_time" IS '创建时间';
COMMENT ON COLUMN "public"."llm_newapi_user_mapping"."updated_time" IS '更新时间';
