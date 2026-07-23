-- 将旧产物行中的当前态补齐为独立字段；参与上下文后续只写入 contributions。
-- 有些环境从未部署过 local_path 版本；临时补列以便统一完成遗留数据转换，迁移末尾必定删除。
ALTER TABLE "public"."hasn_artifacts"
    ADD COLUMN IF NOT EXISTS "local_path" VARCHAR(512);

ALTER TABLE "public"."hasn_artifacts"
    DROP CONSTRAINT IF EXISTS "ck_hasn_artifacts_exactly_one_locator";

ALTER TABLE "public"."hasn_artifacts"
    ADD COLUMN IF NOT EXISTS "artifact_key" VARCHAR(768),
    ADD COLUMN IF NOT EXISTS "artifact_kind" VARCHAR(16),
    ADD COLUMN IF NOT EXISTS "resource_app_id" VARCHAR(64),
    ADD COLUMN IF NOT EXISTS "local_locator_key" VARCHAR(256),
    ADD COLUMN IF NOT EXISTS "local_entry_kind" VARCHAR(16);

-- 生成模型统一继承时间基类，参与记录也需要该字段以保持查询列一致。
ALTER TABLE "public"."hasn_artifact_contributions"
    ADD COLUMN IF NOT EXISTS "updated_time" TIMESTAMPTZ;

-- 旧实现曾把 hasn://asset/{id} 错放 resource_uri；先归还给 asset 本体字段。
UPDATE "public"."hasn_artifacts"
SET
    "asset_id" = COALESCE("asset_id", substring("resource_uri" FROM '^hasn://asset/(.+)$')),
    "resource_uri" = NULL
WHERE "resource_uri" LIKE 'hasn://asset/%';

-- 严格四选一的回填优先级为应用资源、正文、资产、本地 locator；不保留互相矛盾的旧定位副本。
UPDATE "public"."hasn_artifacts"
SET
    "body" = CASE WHEN "resource_uri" IS NOT NULL THEN NULL ELSE "body" END,
    "asset_id" = CASE WHEN "resource_uri" IS NOT NULL OR "body" IS NOT NULL THEN NULL ELSE "asset_id" END,
    "local_locator_key" = CASE
        WHEN "resource_uri" IS NOT NULL OR "body" IS NOT NULL OR "asset_id" IS NOT NULL THEN NULL
        ELSE "local_locator_key"
    END;

-- 历史本地路径不再离开云端。旧行无从恢复节点密钥，故转换为不可逆 legacy locator 并如实标为 missing。
UPDATE "public"."hasn_artifacts"
SET
    "local_locator_key" = CASE
        WHEN "local_locator_key" IS NULL AND "local_path" IS NOT NULL THEN 'legacy:' || md5("local_path")
        ELSE "local_locator_key"
    END,
    "local_entry_kind" = CASE
        WHEN "local_entry_kind" IS NULL AND "local_path" IS NOT NULL THEN 'file'
        ELSE "local_entry_kind"
    END,
    "status" = CASE
        WHEN "local_path" IS NOT NULL AND "status" = 'active' THEN 'missing'
        ELSE "status"
    END;

UPDATE "public"."hasn_artifacts"
SET "artifact_kind" = CASE
    WHEN "resource_uri" IS NOT NULL AND "resource_uri" NOT LIKE 'hasn://asset/%' THEN 'resource'
    WHEN "body" IS NOT NULL THEN 'document'
    WHEN COALESCE("kind", '') IN ('image', 'video', 'voice', 'file') THEN "kind"
    WHEN "asset_id" IS NOT NULL OR "local_locator_key" IS NOT NULL THEN 'file'
    ELSE 'document'
END
WHERE "artifact_kind" IS NULL;

UPDATE "public"."hasn_artifacts"
SET "resource_app_id" = NULLIF(split_part("resource_kind", '.', 1), '')
WHERE "resource_app_id" IS NULL AND "resource_kind" IS NOT NULL;

UPDATE "public"."hasn_artifacts"
SET "artifact_key" = CASE
    WHEN "resource_uri" IS NOT NULL AND "resource_uri" NOT LIKE 'hasn://asset/%' THEN 'resource:' || "resource_uri"
    WHEN "asset_id" IS NOT NULL THEN 'asset:' || "asset_id"
    WHEN "local_locator_key" IS NOT NULL THEN 'local:' || COALESCE("node_id", 'unknown') || ':' || "local_locator_key"
    WHEN "body" IS NOT NULL THEN 'body:legacy:' || "artifact_id"
    ELSE 'legacy:' || "artifact_id"
END
WHERE "artifact_key" IS NULL;

-- 历史当前态也必须拥有一条不可变参与记录；否则统一查询的上下文最新记录无法选出该产物。
-- 元数据刻意留空，避免把旧快照中可能存在的本地绝对路径重新带入云端。
INSERT INTO "public"."hasn_artifact_contributions" (
    "contribution_id",
    "artifact_id",
    "owner_hasn_id",
    "agent_hasn_id",
    "work_session_id",
    "project_id",
    "action",
    "source_kind",
    "source_tool",
    "source_app_id",
    "dispatch_id",
    "source_event_id",
    "idempotency_key",
    "conversation_id",
    "message_id",
    "occurred_time",
    "metadata"
)
SELECT
    'con_' || substring(md5('legacy:' || a."artifact_id") FROM 1 FOR 32),
    a."artifact_id",
    a."owner_hasn_id",
    COALESCE(NULLIF(a."agent_hasn_id", ''), 'legacy_agent'),
    a."session_id",
    a."project_id",
    CASE WHEN a."action" IN ('create', 'update') THEN a."action" ELSE 'create' END,
    CASE
        WHEN a."resource_uri" IS NOT NULL THEN 'app_write'
        WHEN a."local_locator_key" IS NOT NULL THEN 'runtime_file'
        WHEN a."source_kind" = 'agent_note' THEN 'agent_note'
        WHEN a."source_kind" = 'external_import' THEN 'external_import'
        ELSE 'platform_tool'
    END,
    a."source_tool",
    a."source_app_id",
    a."dispatch_id",
    a."origin_ref",
    'legacy:' || a."artifact_id",
    a."conversation_id",
    a."message_id",
    COALESCE(a."updated_time", a."created_time", NOW()),
    '{}'::JSONB
FROM "public"."hasn_artifacts" AS a
ON CONFLICT ("owner_hasn_id", "agent_hasn_id", "idempotency_key") DO NOTHING;

ALTER TABLE "public"."hasn_artifacts"
    ALTER COLUMN "artifact_key" SET NOT NULL,
    ALTER COLUMN "artifact_kind" SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS "uq_hasn_artifacts_owner_artifact_key"
    ON "public"."hasn_artifacts" ("owner_hasn_id", "artifact_key");

ALTER TABLE "public"."hasn_artifacts"
    DROP CONSTRAINT IF EXISTS "ck_hasn_artifacts_artifact_kind",
    ADD CONSTRAINT "ck_hasn_artifacts_artifact_kind"
        CHECK ("artifact_kind" IN ('resource', 'document', 'image', 'video', 'voice', 'file')),
    DROP CONSTRAINT IF EXISTS "ck_hasn_artifacts_status",
    ADD CONSTRAINT "ck_hasn_artifacts_status"
        CHECK ("status" IN ('active', 'missing', 'deleted')),
    DROP CONSTRAINT IF EXISTS "ck_hasn_artifacts_local_entry_kind",
    ADD CONSTRAINT "ck_hasn_artifacts_local_entry_kind"
        CHECK ("local_entry_kind" IS NULL OR "local_entry_kind" IN ('file', 'directory'));

-- 新写入必须严格四选一；历史不规范行在后续回填完成前不影响已有业务读取。
ALTER TABLE "public"."hasn_artifacts"
    DROP CONSTRAINT IF EXISTS "ck_hasn_artifacts_exactly_one_locator",
    ADD CONSTRAINT "ck_hasn_artifacts_exactly_one_locator"
        CHECK (num_nonnulls("body", "asset_id", "resource_uri", "local_locator_key") = 1) NOT VALID;

ALTER TABLE "public"."hasn_artifacts"
    VALIDATE CONSTRAINT "ck_hasn_artifacts_exactly_one_locator";

-- 完成不可逆定位键回填后，云端不再保留任何本地绝对路径。
ALTER TABLE "public"."hasn_artifacts"
    DROP COLUMN IF EXISTS "local_path";

CREATE INDEX IF NOT EXISTS "idx_hasn_artifacts_owner_current"
    ON "public"."hasn_artifacts" ("owner_hasn_id", "updated_time" DESC, "created_time" DESC);
