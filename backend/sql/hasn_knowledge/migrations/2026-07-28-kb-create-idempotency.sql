-- Knowledge 建库业务幂等：同一 Owner 的 client_request_id 只能创建一个知识库。
ALTER TABLE "hasn_knowledge"."kb"
    ADD COLUMN IF NOT EXISTS "client_request_id" varchar(200);

CREATE UNIQUE INDEX IF NOT EXISTS "uq_kb_owner_client_request"
    ON "hasn_knowledge"."kb" ("owner_id", "client_request_id")
    WHERE "client_request_id" IS NOT NULL;

COMMENT ON COLUMN "hasn_knowledge"."kb"."client_request_id"
    IS 'Owner 范围建库业务幂等键；相同键重试返回原库，参数冲突返回 409';
